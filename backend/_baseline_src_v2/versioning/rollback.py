"""Checkpointing, state reconciliation and cross-store restoration of architecture versions.

Restoration never rewrites a frontend/in-memory graph only: it converges Qdrant payloads and profile vectors,
MongoDB module/assignment/edge documents and the live NetworkX graph to the target state, then verifies all stores.
Records that were compressed away or deleted since the snapshot are NOT revived (only currently active memories are
placed); memories created after the snapshot are placed in their current module if it survives, otherwise in the
nearest module by profile similarity.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from ..common import IntegrityError, MorpheError, NotFoundError, get_logger, iso, log_event, slugify
from ..coordinator import MutationCoordinator
from ..graph.persistence import ArchitecturePersistence
from ..monitoring.events import EventLog
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from .compare import diff_states, summarize_diff
from .snapshots import VersionStore
from .state import (ArchitectureState, ModuleSpec, centroid, cosine,
                    hierarchy_edges_from_parents)
from .validator import LiveValidator

log = get_logger("versioning.rollback")


class ArchitectureLifecycle:
    def __init__(self, settings, qdrant: QdrantStore, mongo: MongoStore, persistence: ArchitecturePersistence,
                 versions: VersionStore, validator: LiveValidator, coordinator: MutationCoordinator, events: EventLog):
        self.s = settings
        self.qdrant = qdrant
        self.mongo = mongo
        self.persistence = persistence
        self.versions = versions
        self.validator = validator
        self.coordinator = coordinator
        self.events = events
        self.evaluator = None  # injected by the container (avoids a construction cycle)

    # ------------------------------------------------------- reconciliation
    def active_vectors(self) -> dict[str, np.ndarray]:
        return {mid: vec for mid, _, vec in self.qdrant.scroll_memories(with_vectors=True) if vec is not None}

    def reconcile_state(self, state: ArchitectureState, live: ArchitectureState | None = None,
                        vectors: dict[str, np.ndarray] | None = None) -> tuple[ArchitectureState, dict[str, Any]]:
        """Align a (possibly old) architecture with the memories that are active *now*."""
        live = live or self.persistence.export_live_state()
        vectors = vectors if vectors is not None else self.active_vectors()
        active = set(vectors)
        new = state.copy()
        live_of = live.module_of()
        dropped = 0
        changed: set[str] = set()
        for m in list(new.assignments):
            keep = [i for i in new.assignments[m] if i in active]
            if len(keep) != len(new.assignments[m]):
                dropped += len(new.assignments[m]) - len(keep)
                changed.add(m)
            new.assignments[m] = keep
        placed = {i for ids in new.assignments.values() for i in ids}
        unplaced = sorted(active - placed)
        placed_current = placed_nearest = 0
        profiles = {m: np.asarray(s.profile_vector) for m, s in new.modules.items()
                    if s.profile_vector is not None and new.assignments.get(m)}
        for mid in unplaced:
            cur = live_of.get(mid)
            if cur and cur in new.modules:
                target = cur
                placed_current += 1
            elif profiles:
                target = max(sorted(profiles), key=lambda m: cosine(vectors[mid], profiles[m]))
                placed_nearest += 1
            else:
                target = "mod_unassigned"
                if target not in new.modules:
                    new.modules[target] = ModuleSpec(target, "Unassigned", description="Memories without a module",
                                                     created_at=iso(), created_by_op="reconcile")
            new.assignments.setdefault(target, []).append(mid)
            changed.add(target)
        # drop empty leaf modules (and anything hanging off them) so no orphan nodes remain
        while True:
            parents = {s.parent_id for s in new.modules.values() if s.parent_id}
            empties = [m for m in new.modules if not new.assignments.get(m) and m not in parents]
            if not empties:
                break
            for m in empties:
                del new.modules[m]
                new.assignments.pop(m, None)
        for e in [e for e, ed in new.edges.items() if ed.source not in new.modules or ed.target not in new.modules]:
            del new.edges[e]
        for m, spec in new.modules.items():
            if spec.parent_id and spec.parent_id not in new.modules:
                spec.parent_id = None
        hierarchy_edges_from_parents(new, iso(), None)
        for m in new.modules:
            ids = new.assignments.get(m)
            if ids and (m in changed or new.modules[m].profile_vector is None):
                new.modules[m].profile_vector = centroid([vectors[i] for i in ids])
        report = {"inactive_memories_not_revived": dropped, "new_memories_kept_in_current_module": placed_current,
                  "new_memories_placed_by_similarity": placed_nearest}
        return new, report

    # ------------------------------------------------------------ checkpoint
    def ensure_checkpoint(self, reason: str, operation_id: str | None = None) -> dict[str, Any]:
        """Persist the current live architecture as a version unless the active version already equals it."""
        live = self.persistence.export_live_state()
        active = self.versions.active_id()
        if active:
            m = self.versions.manifest(active)
            if m["checksum"] == live.checksum() and self.versions.verify(active)["valid"]:
                return m
        return self.versions.create(live, reason=reason, operation_type="checkpoint", operation_id=operation_id,
                                    parent_version_id=active, summary={"note": "state captured before a mutation"})

    # --------------------------------------------------------------- recovery
    def recover(self, before_version_id: str) -> dict[str, Any]:
        """Converge every store back to a previously saved version (idempotent; safe to re-run)."""
        target, report = self.reconcile_state(self.versions.snapshot_state(before_version_id))
        summary = self.persistence.converge_to(target)
        self.versions.set_active(before_version_id)
        return {**summary, **report}

    # ---------------------------------------------------------------- preview
    def preview_restore(self, version_id: str) -> dict[str, Any]:
        manifest = self.versions.manifest(version_id)
        check = self.versions.verify(version_id)
        active_id = self.versions.active_id()
        current = self.versions.manifest(active_id) if active_id else None
        out: dict[str, Any] = {"target_version": self.versions.describe(manifest, active_id, check=False),
                               "current_version": current, "restorable": check["valid"],
                               "problems": check["problems"], "is_already_active": version_id == active_id}
        if not check["valid"]:
            return out
        live = self.persistence.export_live_state()
        target, report = self.reconcile_state(self.versions.snapshot_state(version_id), live)
        d = diff_states(live, target)
        out.update({"diff": d, "summary": summarize_diff(d), "reconciliation": report,
                    "notes": ["Compressed, superseded or deleted records are not revived.",
                              "The current architecture is checkpointed first, so this restore can itself be undone."]})
        return out

    # ---------------------------------------------------------------- restore
    def restore(self, version_id: str, operation_id: str | None = None, reason: str | None = None,
                evaluate: bool = True) -> dict[str, Any]:
        t0 = time.perf_counter()
        from ..common import new_id
        operation_id = operation_id or new_id("adapt")
        reason = reason or f"Restore architecture {version_id}"
        self.versions.manifest(version_id)  # 404 if unknown
        prior = self.events.get_adaptation(operation_id)
        if prior:  # idempotent retry: report the recorded outcome instead of restoring twice
            return {"operation_id": operation_id, "status": prior["status"], "already_recorded": True,
                    "restored_from": prior.get("to_version"), "new_version": prior.get("new_version"),
                    "summary": prior.get("summary"), "error": prior.get("error")}
        event = self.events.start_adaptation(operation="restore_version", trigger="manual", reason=reason,
                                             event_id=operation_id, to_version=version_id,
                                             from_version=self.versions.active_id())
        try:
            with self.coordinator.mutate("restore", operation_id, {"version_id": version_id}, fail_fast=True) as op:
                check = self.versions.verify(version_id)
                if not check["valid"]:
                    raise IntegrityError(f"version {version_id} is not restorable: " + "; ".join(check["problems"][:3]),
                                         code="snapshot_invalid")
                replaced = self.versions.active_id()
                checkpoint = self.ensure_checkpoint(f"checkpoint before restoring {version_id}", operation_id)
                self.coordinator.set_phase(op, "applying", before_version=checkpoint["version_id"])
                self.events.update_adaptation(operation_id, "applying", status="running",
                                              before_version=checkpoint["version_id"])
                before_eval = self._live_metrics() if evaluate else None
                target, report = self.reconcile_state(self.versions.snapshot_state(version_id))
                try:
                    live_before = self.persistence.export_live_state()
                    summary = self.persistence.converge_to(target, operation_id=operation_id)
                    problems = self.validator.check()
                    if not problems["passed"]:
                        raise IntegrityError("post-restore verification failed: " + "; ".join(problems["problems"][:3]))
                except Exception as exc:
                    recovered = self._try_recover(checkpoint["version_id"], operation_id)
                    raise IntegrityError(
                        f"restoration of {version_id} failed ({exc}); " +
                        ("the previous architecture was re-applied" if recovered else
                         "automatic recovery ALSO failed — recovery will be retried at next startup"),
                        code="restore_failed") from exc
                d = diff_states(live_before, target)
                after_eval = self._live_metrics() if evaluate else None
                new_v = self.versions.create(
                    self.persistence.export_live_state(), reason=reason, operation_type="restore",
                    operation_id=operation_id, parent_version_id=checkpoint["version_id"], restored_from=version_id,
                    evaluation={"before": before_eval, "after": after_eval} if evaluate else None,
                    validation={"integrity_check": "passed", "post_restore_checks": problems["stats"]},
                    summary={"diff": summarize_diff(d), "reconciliation": report})
                rolled = self._mark_rolled_back(version_id, checkpoint["version_id"], operation_id)
                dur = round((time.perf_counter() - t0) * 1000, 1)
                self.events.update_adaptation(
                    operation_id, "restore completed", status="completed", finished_at=iso(), duration_ms=dur,
                    new_version=new_v["version_id"], integrity_check="passed", diff=d, summary=summarize_diff(d),
                    reconciliation=report, evaluation={"before": before_eval, "after": after_eval},
                    rolled_back_events=rolled, affected_record_count=d["memories_moved"])
                op["result"] = {"new_version": new_v["version_id"]}
        except MorpheError as exc:
            self.events.update_adaptation(operation_id, f"failed: {exc.message}", status="failed", finished_at=iso(),
                                          error=exc.message, integrity_check="failed" if exc.code in
                                          ("restore_failed", "snapshot_invalid") else "not_run",
                                          duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            raise
        except Exception as exc:
            self.events.update_adaptation(operation_id, "failed", status="failed", finished_at=iso(),
                                          error=f"{type(exc).__name__}: {exc}"[:300])
            raise
        log_event(log, "architecture_restored", target=version_id, new_version=new_v["version_id"], duration_ms=dur)
        return {"operation_id": operation_id, "status": "completed", "restored_from": version_id,
                "replaced_version": replaced, "checkpoint_version": checkpoint["version_id"],
                "new_version": self.versions.describe(new_v), "diff": d, "summary": summarize_diff(d),
                "reconciliation": report, "evaluation": {"before": before_eval, "after": after_eval},
                "rolled_back_events": rolled, "duration_ms": dur}

    # ---------------------------------------------------------------- helpers
    def _try_recover(self, before_version_id: str, operation_id: str) -> bool:
        try:
            self.recover(before_version_id)
            return self.validator.check()["passed"]
        except Exception as exc:
            self.mongo.set_state("needs_recovery", {"operation_id": operation_id, "before_version": before_version_id,
                                                    "at": iso(), "error": str(exc)[:200]})
            log_event(log, "recovery_failed", level=40, operation_id=operation_id, error_type=type(exc).__name__)
            return False

    def _live_metrics(self) -> dict[str, Any] | None:
        if self.evaluator is None:
            return None
        try:
            ctx = self.evaluator.build_context()
            rep = self.evaluator.evaluate_live(ctx, "live")
            return {"valid": rep["valid"], "reason": rep["invalid_reason"], "metrics": rep["metrics"],
                    "dataset_hash": ctx.dataset.dataset_hash}
        except Exception as exc:
            return {"valid": False, "reason": f"evaluation failed: {type(exc).__name__}", "metrics": None}

    def _mark_rolled_back(self, target_version: str, replaced_version: str | None, by_operation: str) -> list[str]:
        """Adaptation events whose versions lie between the target and the replaced version are now rolled back."""
        if not replaced_version:
            return []
        marked: list[str] = []
        try:
            chain = self.versions.lineage(replaced_version)
        except NotFoundError:
            return []
        if target_version not in chain:
            return []
        for vid in chain[: chain.index(target_version)]:
            ev_id = self.versions.manifest(vid).get("operation_id")
            ev = self.events.get_adaptation(ev_id) if ev_id else None
            if ev and ev.get("status") == "completed" and ev.get("operation") != "restore_version":
                self.events.update_adaptation(ev_id, f"rolled back by {by_operation}", status="rolled_back",
                                              rolled_back_by=by_operation, rolled_back_at=iso(),
                                              rolled_back_to=target_version)
                marked.append(ev_id)
        return marked
