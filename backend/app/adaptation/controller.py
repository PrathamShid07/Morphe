"""Structural adaptation controller.

Lifecycle of one operation (every step is recorded on the adaptation event):

  detect/plan -> build candidate architecture (pure) -> evaluate baseline + candidate in isolated sandboxes on the
  same labeled dataset -> acceptance policy -> [rejected: stop, nothing changed]
  -> exclusive mutation: checkpoint current version -> converge stores to the candidate -> integrity check ->
  new version -> post-commit verification (live evaluation must match the sandbox prediction and must not regress)
  -> [any failure: converge back to the checkpoint; status `failed` (not yet committed) or `rolled_back`].
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..common import (ConflictError, IntegrityError, MorpheError, NotFoundError, ValidationFailure, get_logger, iso,
                      log_event, new_id)
from ..coordinator import AlreadyCompleted, MutationCoordinator
from ..evaluation.evaluator import Evaluator
from ..evaluation.version_comparator import AcceptancePolicy
from ..graph.persistence import ArchitecturePersistence
from ..memory.access_tracker import AccessTracker
from ..monitoring.events import ADAPTATION, EventLog
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from ..versioning.compare import diff_states, summarize_diff
from ..versioning.rollback import ArchitectureLifecycle
from ..versioning.snapshots import VersionStore
from ..versioning.state import ArchitectureState
from ..versioning.validator import LiveValidator
from .detector import MERGE, SHORTCUT, SPLIT, PlanningContext, Proposal, StructureDetector

log = get_logger("adaptation")
OPERATIONS = (SPLIT, MERGE, SHORTCUT)


def _round_robin(proposals: list[Proposal]) -> list[Proposal]:
    """Interleave operation types (each already ranked) so a cap on evaluated proposals cannot starve a whole type."""
    by_op: dict[str, list[Proposal]] = {}
    for p in proposals:
        by_op.setdefault(p.operation, []).append(p)
    order = [op for op in (SPLIT, MERGE, SHORTCUT) if op in by_op]
    out: list[Proposal] = []
    while any(by_op.values()):
        for op in order:
            if by_op[op]:
                out.append(by_op[op].pop(0))
    return out


class PostCommitVerificationError(Exception):
    pass


class _AdaptationFailure(Exception):
    """Carries a failure plus the outcome of the in-lock recovery attempt out of the mutation context."""

    def __init__(self, cause: Exception, info: dict[str, Any]):
        super().__init__(str(cause))
        self.cause, self.info = cause, info


class AdaptationService:
    def __init__(self, settings, qdrant: QdrantStore, mongo: MongoStore, persistence: ArchitecturePersistence,
                 versions: VersionStore, lifecycle: ArchitectureLifecycle, validator: LiveValidator,
                 evaluator: Evaluator, policy: AcceptancePolicy, coordinator: MutationCoordinator,
                 events: EventLog, tracker: AccessTracker):
        self.s = settings
        self.qdrant, self.mongo = qdrant, mongo
        self.persistence, self.versions, self.lifecycle = persistence, versions, lifecycle
        self.validator, self.evaluator, self.policy = validator, evaluator, policy
        self.coordinator, self.events, self.tracker = coordinator, events, tracker
        self.detector = StructureDetector(settings)
        self._run_lock = threading.Lock()
        self._running = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="morphe-adapt")
        self._ref_cache: dict[tuple, dict[str, Any]] = {}

    # ----------------------------------------------------------- planning ctx
    def planning_context(self) -> PlanningContext:
        with self.coordinator.read():
            state = self.persistence.export_live_state()
            vectors, meta = {}, {}
            for mid, payload, vec in self.qdrant.scroll_memories(with_vectors=True):
                vectors[mid], meta[mid] = vec, payload
        return PlanningContext(state, vectors, meta)

    def _reference_report(self, eval_ctx, baseline_state: ArchitectureState) -> dict[str, Any] | None:
        """Quality of the INITIAL architecture on the same validation queries (cached) for the cumulative drift guard."""
        if not self.s.cumulative_guard:
            return None
        genesis = self.versions.genesis_id()
        if not genesis:
            return None
        gstate, _ = self.lifecycle.reconcile_state(self.versions.snapshot_state(genesis))
        if gstate.checksum() == baseline_state.checksum():
            return None  # nothing has drifted yet: the immediate-parent guard already covers it
        key = (genesis, eval_ctx.dataset.dataset_hash, len(eval_ctx.queries), self.s.eval_depth, self.s.seed_module_count,
               self.s.max_candidate_modules, gstate.checksum())
        if key not in self._ref_cache:
            self._ref_cache[key] = self.evaluator.evaluate_state(gstate, eval_ctx, "initial_reference")
        return self._ref_cache[key]

    # ------------------------------------------------------------------ views
    def status(self) -> dict[str, Any]:
        w = self.tracker.window()
        qn = int(w.get("query_count", 0))
        cur = self.coordinator.current()
        last, _ = self.mongo.list_events(ADAPTATION, "started_at", 1)
        pairs = sorted(((k, v) for k, v in (w.get("pair_counts") or {}).items()), key=lambda kv: (-kv[1], kv[0]))[:10]
        return {"queries_in_window": qn, "queries_per_window": self.s.adaptation_query_batch,
                "next_run_after_queries": max(0, self.s.adaptation_query_batch - qn),
                "window_id": w.get("window_id"), "window_started_at": w.get("started_at"),
                "running": self._running or bool(cur and cur["kind"] in ("adaptation", "restore")),
                "current_operation": cur, "auto_adaptation": self.s.auto_adaptation,
                "auto_apply": self.s.adaptation_auto_apply, "quality_evaluation_enabled": True,
                "rollback_enabled": True,
                "last_operation": last[0]["operation"] if last else None,
                "last_operation_status": last[0]["status"] if last else None,
                "last_operation_at": last[0]["started_at"] if last else None,
                "last_window_run": self.mongo.get_state("last_window_run"),
                "window_pairs": [{"modules": k.split("|"), "count": v,
                                  "eligible": v >= self.s.coaccess_min_count} for k, v in pairs],
                "window_fallback_rate": (w.get("fallback_count", 0) / qn) if qn else None,
                "window_mean_candidates": (w.get("candidate_sum", 0) / qn) if qn else None,
                "thresholds": {**self.policy.thresholds(), "coaccess_min_count": self.s.coaccess_min_count,
                               "coaccess_min_window_queries": self.s.coaccess_min_window_queries,
                               "split_capacity_threshold": self.s.split_capacity_threshold,
                               "split_mixed_min_records": self.s.split_mixed_min_records,
                               "merge_profile_similarity": self.s.merge_profile_similarity,
                               "merge_overlap_min": self.s.merge_overlap_min, "max_edges_total": self.s.max_edges_total}}

    def candidates(self) -> dict[str, Any]:
        ctx = self.planning_context()
        window = self.tracker.window()
        proposals, notes = self.detector.detect(ctx, window, "manual_inspection")
        return {"proposals": [p.to_dict() for p in proposals], "not_proposed": notes[:25],
                "window": {"queries": window.get("query_count", 0), "window_id": window.get("window_id"),
                           "complete": window.get("query_count", 0) >= self.s.adaptation_query_batch},
                "checked_at": iso()}

    # ---------------------------------------------------------- core pipeline
    def process(self, operation: str, module_ids: list[str], *, apply: bool, trigger: str = "manual",
                override: bool = False, event_id: str | None = None, window: dict[str, Any] | None = None,
                plan_transform: Callable[[ArchitectureState], ArchitectureState] | None = None,
                fault: str | None = None) -> dict[str, Any]:
        """`plan_transform` / `fault` are test and benchmark-safety seams: they replace the candidate state after planning
        (to inject a harmful candidate) or raise inside the commit (to prove recovery). Neither is reachable from the API."""
        if operation not in OPERATIONS:
            raise ValidationFailure(f"operation must be one of {OPERATIONS}")
        want = 1 if operation == SPLIT else 2
        if len(module_ids) != want:
            raise ValidationFailure(f"{operation} needs exactly {want} module id(s)")
        if override and not (self.s.allow_validation_override and not self.s.is_production):
            raise ValidationFailure("validation override is disabled (set ALLOW_VALIDATION_OVERRIDE=true in "
                                    "development to bypass missing-data rejection only)", code="override_disabled")
        event_id = event_id or new_id("adapt")
        if self.events.get_adaptation(event_id):
            return self.events.get_adaptation(event_id)  # idempotent retry
        t0 = time.perf_counter()
        self.events.start_adaptation(operation=operation, trigger=trigger, reason="planning…", status="running",
                                     event_id=event_id, affected_modules=module_ids, stage="planning",
                                     mode="apply" if apply else "evaluate")
        try:
            return self._process(event_id, operation, module_ids, apply, trigger, override, window, t0, plan_transform, fault)
        except MorpheError as exc:
            self._finish(event_id, t0, "failed", error=exc.message, stage_error=exc.code)
            raise
        except Exception as exc:
            self._finish(event_id, t0, "failed", error=f"{type(exc).__name__}: {exc}"[:300])
            raise

    def _finish(self, event_id: str, t0: float, status: str, note: str | None = None, **fields: Any) -> dict[str, Any]:
        self.events.update_adaptation(event_id, note or status, status=status, finished_at=iso(),
                                      duration_ms=round((time.perf_counter() - t0) * 1000, 1), **fields)
        return self.events.get_adaptation(event_id)  # type: ignore[return-value]

    def _process(self, event_id: str, operation: str, module_ids: list[str], apply: bool, trigger: str,
                 override: bool, window: dict[str, Any] | None, t0: float, plan_transform=None,
                 fault: str | None = None) -> dict[str, Any]:
        ctx = self.planning_context()
        window = window if window is not None else self.tracker.window()
        proposal, diag = self.detector.build(operation, module_ids, ctx, window, trigger, event_id)
        if proposal is None:
            return self._finish(event_id, t0, "rejected", "not eligible: " + "; ".join(diag.get("blockers", [])),
                                reason="Not eligible: " + "; ".join(diag.get("blockers", ["no valid plan"])),
                                evidence=diag, rejection_stage="planning", integrity_check="not_run",
                                decision="reject")
        plan = proposal.plan
        if plan_transform is not None:
            plan.new_state = plan_transform(plan.new_state)
        self.events.update_adaptation(
            event_id, "plan built; evaluating on the labeled dataset", reason=proposal.reason, evidence=proposal.evidence,
            thresholds=proposal.thresholds, affected_modules=proposal.module_ids, proposal_id=proposal.proposal_id,
            affected_record_count=proposal.record_ids, stage="evaluation",
            before_version=self.versions.active_id())
        problems = plan.new_state.validate(self.qdrant.dimension)
        if problems:
            return self._finish(event_id, t0, "rejected", "candidate failed integrity validation",
                                reason="Candidate architecture failed integrity validation: " + "; ".join(problems[:3]),
                                rejection_stage="integrity", integrity_check="failed", decision="reject")

        eval_ctx = self.evaluator.build_context()
        reps = self.evaluator.evaluate_states([("baseline", ctx.state), ("candidate", plan.new_state)], eval_ctx)
        base_rep, cand_rep = reps["baseline"], reps["candidate"]
        reference = self._reference_report(eval_ctx, ctx.state)
        base_doc = self.evaluator.persist(base_rep, event_id=event_id, role="baseline")
        cand_doc = self.evaluator.persist(cand_rep, event_id=event_id, role="candidate")
        decision = self.policy.decide(base_rep, cand_rep, override, reference=reference)
        d = diff_states(ctx.state, plan.new_state)
        evaluation = {"baseline": base_rep["metrics"], "candidate": cand_rep["metrics"], "decision": decision,
                      "baseline_run_id": base_rep["run_id"], "candidate_run_id": cand_rep["run_id"],
                      "by_split": {"baseline": base_rep["by_split"], "candidate": cand_rep["by_split"]},
                      "conditions": cand_rep["conditions"]}
        self.events.update_adaptation(event_id, f"evaluation: {decision['decision']}", evaluation=evaluation,
                                      decision=decision["decision"], decision_reason=decision["reason"],
                                      structural_summary=summarize_diff(d), diff=d,
                                      created_module_ids=[m["module_id"] for m in d["modules_added"]],
                                      removed_module_ids=[m["module_id"] for m in d["modules_removed"]])
        if not decision["accepted"]:
            return self._finish(event_id, t0, "rejected", decision["reason"], rejection_stage="pre_commit",
                                integrity_check="passed", note_outcome="candidate rejected before commit; the active "
                                "architecture was not modified")
        if not apply:
            return self._finish(event_id, t0, "proposed", "evaluated; awaiting explicit apply",
                                integrity_check="passed")
        return self._commit(event_id, t0, operation, proposal, ctx, plan, eval_ctx, base_rep, cand_rep, evaluation, d, fault)

    # ----------------------------------------------------------------- commit
    def _commit(self, event_id: str, t0: float, operation: str, proposal: Proposal, ctx: PlanningContext, plan,
                eval_ctx, base_rep: dict[str, Any], cand_rep: dict[str, Any], evaluation: dict[str, Any],
                d: dict[str, Any], fault: str | None = None) -> dict[str, Any]:
        state = {"committed": False, "checkpoint": None, "new_version": None}
        try:
            with self.coordinator.mutate("adaptation", event_id, {"operation": operation}, fail_fast=True) as op:
                try:
                    self.events.update_adaptation(event_id, "applying", status="running", stage="commit")
                    live = self.persistence.export_live_state()
                    if live.checksum() != ctx.state.checksum():
                        raise ConflictError("the architecture changed while the candidate was being evaluated; "
                                            "re-run the operation", code="stale_plan")
                    checkpoint = state["checkpoint"] = self.lifecycle.ensure_checkpoint(
                        f"checkpoint before {operation}", event_id)
                    self.coordinator.set_phase(op, "applying", before_version=checkpoint["version_id"])
                    self.events.update_adaptation(event_id, "checkpoint saved", before_version=checkpoint["version_id"])
                    self.persistence.converge_to(plan.new_state, operation_id=event_id)
                    state["committed"] = True
                    integrity = self.validator.check()
                    if not integrity["passed"]:
                        raise IntegrityError("post-commit integrity check failed: " + "; ".join(integrity["problems"][:3]),
                                             code="integrity_failed")
                    if fault == "post_commit":
                        raise PostCommitVerificationError("injected post-commit verification failure")
                    verification = self._verify_post_commit(eval_ctx, base_rep, cand_rep)
                    new_version = state["new_version"] = self.versions.create(
                        self.persistence.export_live_state(), reason=proposal.reason, operation_type=operation,
                        operation_id=event_id, parent_version_id=checkpoint["version_id"],
                        evaluation={"baseline": evaluation["baseline"], "candidate": evaluation["candidate"],
                                    "decision": evaluation["decision"]["decision"],
                                    "decision_reason": evaluation["decision"]["reason"],
                                    "post_commit": verification},
                        validation={"integrity_check": "passed", "post_commit_verification": verification["status"],
                                    "decision": evaluation["decision"]["decision"]},
                        summary={"diff": summarize_diff(d), "modules": proposal.module_ids})
                    op["result"] = {"version_id": new_version["version_id"]}
                except ConflictError:
                    raise
                except Exception as exc:
                    # recover while the exclusive lock is still held, then let the coordinator record the failure
                    info = self._recover(event_id, state)
                    raise _AdaptationFailure(exc, info) from exc
            return self._finish(
                event_id, t0, "completed", f"committed as {new_version['version_id']}",
                new_version=new_version["version_id"], integrity_check="passed", rejection_stage=None,
                post_commit_verification=verification, rollback_available=True, stage="done",
                before_version=checkpoint["version_id"])
        except _AdaptationFailure as f:
            return self._finish_failure(event_id, t0, f.cause, f.info)

    def _recover(self, event_id: str, state: dict[str, Any]) -> dict[str, Any]:
        checkpoint, new_version = state["checkpoint"], state["new_version"]
        info: dict[str, Any] = {"committed": state["committed"], "recovered": False, "recovery_error": None,
                                "checkpoint": checkpoint["version_id"] if checkpoint else None}
        if not checkpoint:
            info["recovered"] = not state["committed"]
            return info
        try:
            self.lifecycle.recover(checkpoint["version_id"])
            info["recovered"] = self.validator.check()["passed"]
            if new_version:
                self.versions.update_manifest(new_version["version_id"], {"status": "rolled_back"})
        except Exception as rexc:
            info["recovery_error"] = f"{type(rexc).__name__}: {rexc}"[:200]
            self.mongo.set_state("needs_recovery", {"operation_id": event_id,
                                                    "before_version": checkpoint["version_id"], "at": iso()})
        return info

    def _finish_failure(self, event_id: str, t0: float, exc: Exception, info: dict[str, Any]) -> dict[str, Any]:
        # `rolled_back` = a committed change was reverted; `failed` = it never became the active architecture
        status = "rolled_back" if info["committed"] and info["recovered"] else "failed"
        msg = f"{type(exc).__name__}: {exc}"[:400]
        log_event(log, "adaptation_failed", level=40, event_id=event_id, committed=info["committed"],
                  recovered=info["recovered"], error_type=type(exc).__name__)
        return self._finish(
            event_id, t0, status, msg, error=msg, stage="recovery",
            integrity_check="failed" if isinstance(exc, IntegrityError) else "passed" if info["committed"] else "not_run",
            rolled_back_to=info["checkpoint"] if info["recovered"] else None, recovered=info["recovered"],
            recovery_error=info["recovery_error"],
            post_commit_verification={"status": "failed", "reason": str(exc)[:300]} if info["committed"] else None)

    def _verify_post_commit(self, eval_ctx, base_rep: dict[str, Any], cand_rep: dict[str, Any]) -> dict[str, Any]:
        if not self.s.post_commit_verification:
            return {"status": "skipped", "reason": "POST_COMMIT_VERIFICATION is disabled"}
        live = self.evaluator.evaluate_live(eval_ctx, "post_commit")
        pm, cm = live["metrics"], cand_rep["metrics"]
        parity = {k: (pm[k], cm[k]) for k in ("recall_at_5", "mrr", "mean_candidate_count", "fallback_rate")}
        mismatches = {k: v for k, v in parity.items() if v[0] is None or v[1] is None or abs(v[0] - v[1]) > 1e-6}
        if mismatches:
            raise PostCommitVerificationError(
                "live retrieval does not reproduce the evaluated candidate: " +
                ", ".join(f"{k} live={v[0]} expected={v[1]}" for k, v in mismatches.items()))
        # quality must not have regressed against the baseline beyond the same thresholds (latency is monitored separately)
        b = base_rep["metrics"]
        if b["recall_at_5"] - pm["recall_at_5"] > self.s.max_recall_regression + 1e-9 or \
                b["mrr"] - pm["mrr"] > self.s.max_mrr_regression + 1e-9:
            raise PostCommitVerificationError("live quality regressed beyond the allowed thresholds after commit")
        self.evaluator.persist(live, role="post_commit")
        return {"status": "passed", "live_metrics": pm, "matches_prediction": True}

    # --------------------------------------------------------- scheduled path
    def on_query_completed(self, event: dict[str, Any]) -> None:
        if not self.s.auto_adaptation:
            return
        if self.tracker.window().get("query_count", 0) < self.s.adaptation_query_batch:
            return
        with self._run_lock:
            if self._running:
                return
            self._running = True
        if self.s.adaptation_inline:
            self._run_window_guarded("query_window")
        else:
            self._executor.submit(self._run_window_guarded, "query_window")

    def _run_window_guarded(self, trigger: str) -> None:
        try:
            self.run_window(trigger, _already_flagged=True)
        except Exception as exc:
            log_event(log, "window_run_failed", level=40, error_type=type(exc).__name__)
        finally:
            self._running = False

    def run_window(self, trigger: str = "manual_window", *, _already_flagged: bool = False) -> dict[str, Any]:
        """Close the current window, detect weaknesses and process up to N proposals."""
        if not _already_flagged:
            with self._run_lock:
                if self._running:
                    raise ConflictError("an adaptation window is already being processed", code="adaptation_running")
                self._running = True
        try:
            t0 = time.perf_counter()
            window = self.tracker.reset_window()
            seq = self.mongo.next_counter("adaptation_window")
            ctx = self.planning_context()
            proposals, notes = self.detector.detect(ctx, window, trigger)
            if self.s.adaptation_round_robin:
                proposals = _round_robin(proposals)
            cooldown = self.mongo.get_state("rejected_proposals", {}) or {}
            results, applied, tried = [], 0, 0
            for p in proposals:
                if applied >= self.s.adaptation_max_ops_per_window or tried >= self.s.adaptation_max_proposals_per_window:
                    break
                seen = cooldown.get(p.proposal_id)
                if seen is not None and seq - seen < self.s.adaptation_rejected_cooldown_windows:
                    notes.append({"operation": p.operation, "module_ids": p.module_ids, "blockers":
                                  [f"recently rejected (cooldown {self.s.adaptation_rejected_cooldown_windows} windows)"]})
                    continue
                tried += 1
                eid = f"adapt_w{window.get('window_id', 'x')[-8:]}_{p.proposal_id[5:]}"
                try:
                    ev = self.process(p.operation, p.module_ids, apply=self.s.adaptation_auto_apply, trigger=trigger,
                                      event_id=eid, window=window)
                except MorpheError as exc:
                    ev = {"event_id": eid, "status": "failed", "error": exc.message}
                results.append({"event_id": ev.get("event_id"), "operation": p.operation, "status": ev.get("status")})
                if ev.get("status") == "completed":
                    applied += 1  # later proposals are re-planned against the new architecture by process()
                if ev.get("status") == "rejected":
                    cooldown[p.proposal_id] = seq
            self.mongo.set_state("rejected_proposals", cooldown)
            monitor = None
            try:
                monitor = self.monitor_regression()
            except Exception as exc:
                monitor = {"status": "error", "error": type(exc).__name__}
            summary = {"window_id": window.get("window_id"), "queries": window.get("query_count", 0),
                       "proposals_detected": len(proposals), "evaluated": results, "applied": applied,
                       "monitoring": monitor, "trigger": trigger, "at": iso(),
                       "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
            self.mongo.set_state("last_window_run", summary)
            log_event(log, "adaptation_window_processed", queries=summary["queries"], proposals=len(proposals),
                      applied=applied)
            return summary
        finally:
            if not _already_flagged:
                self._running = False

    # ------------------------------------------------------------- monitoring
    def monitor_regression(self, auto_rollback: bool = True) -> dict[str, Any]:
        """Compare the active architecture with its parent on the *current* memories and dataset; roll back on regression."""
        active_id = self.versions.active_id()
        if not active_id:
            return {"status": "not_applicable", "reason": "no active architecture version"}
        m = self.versions.manifest(active_id)
        if m["operation_type"] not in (SPLIT, MERGE, SHORTCUT) or not m.get("parent_version_id"):
            return {"status": "not_applicable", "reason": f"active version {active_id} was not created by adaptation",
                    "version_id": active_id}
        parent = m["parent_version_id"]
        eval_ctx = self.evaluator.build_context()
        parent_state, _ = self.lifecycle.reconcile_state(self.versions.snapshot_state(parent))
        cur_state = self.persistence.export_live_state()
        reps = self.evaluator.evaluate_states([("monitor_parent", parent_state), ("monitor_active", cur_state)], eval_ctx)
        base, cur = reps["monitor_parent"], reps["monitor_active"]
        decision = self.policy.decide(base, cur, latency_factor=3.0, check_benefit=False)  # monitoring tolerates sandbox jitter
        result = {"status": "ok" if decision["accepted"] else "regression", "version_id": active_id,
                  "compared_to": parent, "decision": decision, "checked_at": iso()}
        self.mongo.set_state("last_monitoring", {k: v for k, v in result.items() if k != "decision"} |
                             {"reason": decision["reason"]})
        if not decision["accepted"] and decision["baseline_metrics"]["sample_size"] >= self.s.min_eval_queries \
                and auto_rollback:
            if not (base["valid"] and cur["valid"]):
                result["status"] = "insufficient_data"
                return result
            rb = self.lifecycle.restore(parent, reason=f"Automatic rollback: monitoring detected a regression "
                                        f"({decision['reason']})", evaluate=False)
            result.update({"status": "rolled_back", "restore": {"new_version": rb["new_version"]["version_id"],
                                                                 "operation_id": rb["operation_id"]}})
        return result
