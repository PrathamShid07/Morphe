"""Shared mutation coordinator.

Every operation that changes memory records, module assignments, graph state or architecture versions
(ingestion, deletion, compression, structural adaptation, restoration) runs inside `mutate()`:

* exclusive: only one mutation at a time, and queries (shared `read()`) never observe a half-applied change;
* journaled: an operation document is written at start and given a terminal status at the end;
* idempotent: re-submitting an operation_id that already completed returns the stored result instead of re-running.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .common import ConflictError, get_logger, iso, log_event, new_id, operation_id_var
from .storage.mongo_store import MongoStore

log = get_logger("coordinator")


class Busy(Exception):
    pass


class AlreadyCompleted(Exception):
    def __init__(self, operation: dict[str, Any]):
        self.operation = operation
        super().__init__(operation.get("operation_id"))


class RWLock:
    """Writer-preferring, writer-reentrant readers/writer lock. Do not nest read() inside read()."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer: int | None = None
        self._depth = 0
        self._writers_waiting = 0

    @contextmanager
    def read(self) -> Iterator[None]:
        me = threading.get_ident()
        nested = False
        with self._cond:
            if self._writer == me:
                nested = True
            else:
                while self._writer is not None or self._writers_waiting:
                    self._cond.wait()
                self._readers += 1
        try:
            yield
        finally:
            if not nested:
                with self._cond:
                    self._readers -= 1
                    self._cond.notify_all()

    @contextmanager
    def write(self, blocking: bool = True, timeout: float | None = None) -> Iterator[None]:
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                self._depth += 1
            else:
                self._writers_waiting += 1
                deadline = None if timeout is None else time.monotonic() + timeout
                try:
                    while self._writer is not None or self._readers > 0:
                        if not blocking:
                            raise Busy()
                        remaining = None if deadline is None else deadline - time.monotonic()
                        if remaining is not None and remaining <= 0:
                            raise Busy()
                        self._cond.wait(remaining)
                finally:
                    self._writers_waiting -= 1
                self._writer, self._depth = me, 1
        try:
            yield
        finally:
            with self._cond:
                self._depth -= 1
                if self._depth == 0:
                    self._writer = None
                    self._cond.notify_all()

    @property
    def writer_active(self) -> bool:
        return self._writer is not None


class MutationCoordinator:
    def __init__(self, mongo: MongoStore, lock_timeout: float = 60.0):
        self.mongo = mongo
        self.lock = RWLock()
        self.lock_timeout = lock_timeout
        self._current: dict[str, Any] | None = None
        self._meta = threading.Lock()

    def read(self):
        return self.lock.read()

    def current(self) -> dict[str, Any] | None:
        with self._meta:
            return dict(self._current) if self._current else None

    def completed_result(self, operation_id: str) -> dict[str, Any] | None:
        op = self.mongo.get_operation(operation_id)
        return op if op and op.get("status") == "completed" else None

    @contextmanager
    def mutate(self, kind: str, operation_id: str | None = None, meta: dict[str, Any] | None = None,
               fail_fast: bool = False) -> Iterator[dict[str, Any]]:
        """Exclusive section. `fail_fast=True` raises ConflictError instead of waiting for another mutation."""
        operation_id = operation_id or new_id("op")
        existing = self.mongo.get_operation(operation_id)
        if existing:
            if existing.get("status") == "completed":
                raise AlreadyCompleted(existing)
            if existing.get("status") in ("running", "applying") and self.lock.writer_active:
                raise ConflictError(f"operation {operation_id} is already running", code="operation_in_progress")
        try:
            ctx = self.lock.write(blocking=not fail_fast, timeout=None if fail_fast else self.lock_timeout)
            ctx.__enter__()
        except Busy:
            cur = self.current()
            raise ConflictError(
                f"another mutation is running ({cur['kind'] if cur else 'unknown'}); retry shortly"
                if fail_fast else "timed out waiting for the mutation lock",
                code="mutation_in_progress", details=cur)
        token = operation_id_var.set(operation_id)
        op = {"operation_id": operation_id, "kind": kind, "status": "running", "started_at": iso(),
              "meta": meta or {}, "finished_at": None, "error": None, "result": None, "phase": "started"}
        self.mongo.col("operations").replace_one({"operation_id": operation_id}, op, upsert=True)
        with self._meta:
            self._current = {"operation_id": operation_id, "kind": kind, "started_at": op["started_at"]}
        log_event(log, "mutation_started", kind=kind)
        t0 = time.perf_counter()
        try:
            yield op
        except BaseException as exc:
            self.mongo.col("operations").update_one(
                {"operation_id": operation_id},
                {"$set": {"status": "failed", "finished_at": iso(), "error": f"{type(exc).__name__}: {exc}"[:500],
                          "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}})
            log_event(log, "mutation_failed", kind=kind, error_type=type(exc).__name__)
            raise
        else:
            self.mongo.col("operations").update_one(
                {"operation_id": operation_id},
                {"$set": {"status": "completed", "finished_at": iso(), "result": op.get("result"),
                          "phase": op.get("phase", "done"),
                          "duration_ms": round((time.perf_counter() - t0) * 1000, 1)}})
            log_event(log, "mutation_completed", kind=kind, duration_ms=round((time.perf_counter() - t0) * 1000, 1))
        finally:
            with self._meta:
                self._current = None
            operation_id_var.reset(token)
            ctx.__exit__(None, None, None)

    def set_phase(self, op: dict[str, Any], phase: str, **fields: Any) -> None:
        op["phase"] = phase
        self.mongo.col("operations").update_one({"operation_id": op["operation_id"]},
                                                {"$set": {"phase": phase, **fields}})
