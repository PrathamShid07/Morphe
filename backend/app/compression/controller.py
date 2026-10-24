"""Compression controller: thresholds, cooldown, overlap prevention, normal vs emergency jobs.

Compression is independent of structural adaptation: it reduces redundant active text (tokens), it does not change
module organisation. It shares only the mutation coordinator.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..common import ConflictError, MorpheError, ValidationFailure, get_logger, iso, log_event, new_id
from ..coordinator import MutationCoordinator
from ..memory.budget_tracker import BudgetTracker
from ..monitoring.events import COMPRESSION, EventLog
from ..storage.mongo_store import MongoStore
from .cleanup import CompressionExecutor, Job

log = get_logger("compression.controller")
STATE_KEY = "compression_state"


class CompressionService:
    def __init__(self, settings, mongo: MongoStore, budget: BudgetTracker, executor: CompressionExecutor,
                 coordinator: MutationCoordinator, events: EventLog):
        self.s = settings
        self.mongo, self.budget, self.exec = mongo, budget, executor
        self.coordinator, self.events = coordinator, events
        self._guard = threading.Lock()
        self._running = False
        self._queued = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="morphe-compress")

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        usage = self.budget.usage()
        st = self.mongo.get_state(STATE_KEY, {}) or {}
        state = "running" if self._running else "queued" if self._queued else st.get("state", "idle")
        if state in ("completed", "failed") and st.get("finished_ts") and time.time() - st["finished_ts"] > 600:
            state = "idle"
        last = st.get("last_run")
        cooldown_left = 0.0
        if st.get("finished_ts"):
            cooldown_left = max(0.0, self.s.compression_cooldown_seconds - (time.time() - st["finished_ts"]))
        return {**usage, "state": state, "band": self.budget.band(usage["usage_percent"]),
                "last_run_status": (last or {}).get("status"), "last_run": last,
                "cooldown_remaining_seconds": round(cooldown_left, 1), "mode": self.s.compression_mode,
                "min_summary_saving_percent": self.s.min_summary_saving_pct,
                "storage_note": "Budget counts active-text tokens only; RAM and disk are measured separately."}

    def events_list(self, limit: int = 20, offset: int = 0, scope: str | None = None,
                    status: str | None = None) -> tuple[list[dict[str, Any]], int]:
        q: dict[str, Any] = {}
        if scope:
            q["scope"] = scope
        if status:
            q["status"] = status
        return self.mongo.list_events(COMPRESSION, "timestamp", limit, offset, q)

    # ---------------------------------------------------------------- trigger
    def check(self, trigger: str = "mutation") -> dict[str, Any]:
        """Called after ingestion and other successful mutations. Never raises into the caller."""
        usage = self.budget.usage()
        pct = usage["usage_percent"]
        if pct < self.s.compression_start_pct:
            return {"triggered": False, "state": "idle", "reason": f"usage {pct:.1f}% is below the "
                    f"{self.s.compression_start_pct:g}% threshold", "usage_percent": pct}
        mode = "emergency" if pct >= self.s.emergency_cleanup_pct else "normal"
        if self.s.compression_mode == "off":
            return {"triggered": False, "state": "idle", "reason": "automatic compression is disabled",
                    "usage_percent": pct, "mode": mode}
        st = self.mongo.get_state(STATE_KEY, {}) or {}
        if mode == "normal" and st.get("finished_ts") and \
                time.time() - st["finished_ts"] < self.s.compression_cooldown_seconds:
            return {"triggered": False, "state": "cooling_down", "reason": "within the compression cooldown",
                    "usage_percent": pct, "mode": mode}
        with self._guard:
            if self._running or self._queued:
                return {"triggered": False, "state": "running" if self._running else "queued",
                        "reason": "a compression job is already active", "usage_percent": pct, "mode": mode}
            self._queued = True
        trig = "threshold_90_percent" if mode == "emergency" else "threshold_75_percent"
        if self.s.compression_mode == "inline":
            try:
                res = self._run_guarded(mode, trig)
                return {"triggered": True, "state": res.get("status", "completed"), "mode": mode,
                        "usage_percent": pct, "job": res}
            except MorpheError as exc:
                return {"triggered": True, "state": "failed", "mode": mode, "error": exc.message}
        self._pool.submit(self._run_guarded_safe, mode, trig)
        return {"triggered": True, "state": "queued", "mode": mode, "usage_percent": pct,
                "reason": f"usage {pct:.1f}% reached the {'emergency' if mode == 'emergency' else 'compression'} threshold"}

    def _run_guarded_safe(self, mode: str, trigger: str) -> None:
        try:
            self._run_guarded(mode, trigger)
        except Exception as exc:
            log_event(log, "compression_job_error", level=40, error_type=type(exc).__name__)

    def _run_guarded(self, mode: str, trigger: str) -> dict[str, Any]:
        with self._guard:
            self._queued = False
            self._running = True
        try:
            return self.run(mode, trigger=trigger)
        finally:
            self._running = False

    # -------------------------------------------------------------------- run
    def run(self, mode: str = "auto", *, trigger: str = "manual", dry_run: bool = False,
            operation_id: str | None = None) -> dict[str, Any]:
        if mode not in ("auto", "normal", "emergency"):
            raise ValidationFailure("mode must be auto, normal or emergency")
        usage = self.budget.usage()
        if mode == "auto":
            mode = "emergency" if usage["usage_percent"] >= self.s.emergency_cleanup_pct else "normal"
        job_id = operation_id or new_id("cmpjob")
        target = self.s.cleanup_target_pct if mode == "emergency" else self.s.compression_target_pct
        t0 = time.perf_counter()
        job_event = self.events.start_compression(
            operation="compression_job", trigger=trigger, scope="job", job_id=job_id, mode=mode,
            reason=f"{mode} compression at {usage['usage_percent']:.1f}% of the {usage['budget_tokens']}-token budget "
                   f"(target ≤ {target:g}%)", status="simulated" if dry_run else "running",
            tokens_before=usage["active_tokens"], usage_before_pct=usage["usage_percent"], target_pct=target)
        try:
            with self.coordinator.mutate("compression", job_id, {"mode": mode, "dry_run": dry_run},
                                         fail_fast=True) as op:
                docs, vectors = self.exec.load()
                job = Job(job_id, mode, trigger, target, usage["budget_tokens"], dry_run, docs, vectors,
                          sum(d["token_count"] for d in docs.values()))
                start_tokens = job.tokens
                for step in (self.exec.step_exact_duplicates, self.exec.step_near_duplicates,
                             self.exec.step_summaries, self.exec.step_shorten):
                    if job.reached_target():
                        break
                    step(job)
                if mode == "emergency" and not job.reached_target():
                    self.exec.step_emergency_delete(job)
                final = self.budget.usage() if not dry_run else None
                after_tokens = final["active_tokens"] if final else job.tokens
                after_pct = final["usage_percent"] if final else round(job.pct, 2)
                failed = [e for e in job.events if e["status"] == "failed"]
                status = "simulated" if dry_run else ("failed" if failed and not any(
                    e["status"] == "completed" for e in job.events) else "completed")
                summary = {"job_id": job_id, "mode": mode, "status": status, "dry_run": dry_run,
                           "tokens_before": start_tokens, "tokens_after": after_tokens,
                           "tokens_reclaimed": start_tokens - after_tokens, "usage_before_pct": usage["usage_percent"],
                           "usage_after_pct": after_pct, "duplicates_removed": job.duplicates_removed,
                           "summaries_created": job.summaries_created, "records_deleted": job.deleted,
                           "operations": job.events, "target_reached": after_pct <= target,
                           "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}
                self.events.update_compression(
                    job_event["event_id"], status=status, finished_at=iso(), tokens_after=after_tokens,
                    tokens_reclaimed=start_tokens - after_tokens, usage_after_pct=after_pct,
                    duplicates_removed=job.duplicates_removed, summaries_created=job.summaries_created,
                    records_deleted=job.deleted, operation_count=len(job.events), target_reached=after_pct <= target)
                op["result"] = {k: summary[k] for k in ("status", "tokens_reclaimed")}
        except Exception as exc:
            msg = exc.message if isinstance(exc, MorpheError) else f"{type(exc).__name__}: {exc}"[:300]
            self.events.update_compression(job_event["event_id"], status="failed", finished_at=iso(), error=msg)
            if not dry_run:
                self._save_state("failed", {"status": "failed", "error": msg, "job_id": job_id})
            raise
        if not dry_run:
            self._save_state(status, {k: summary[k] for k in ("job_id", "mode", "status", "tokens_before",
                                                              "tokens_after", "tokens_reclaimed")} | {"at": iso()})
        log_event(log, "compression_job_finished", job_id=job_id, mode=mode, status=status,
                  tokens_reclaimed=summary["tokens_reclaimed"])
        return summary

    def _save_state(self, state: str, last: dict[str, Any]) -> None:
        self.mongo.set_state(STATE_KEY, {"state": state, "last_run": last, "finished_ts": time.time()})
