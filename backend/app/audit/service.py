"""Durable audit trail (MongoDB `audit_events`).

One document per meaningful action: who (actor id and role), what (`action`), on what (`target_type`/`target_id`), when, whether it worked
(`status`: success | failure | denied) and which request it belonged to. `metadata` carries small, non-secret facts (strategy, counts,
latency, error code). Passwords, tokens, signing secrets, embeddings and memory text are never written here; a submitted question is kept
only as a truncated preview, as disclosed in the privacy notice. Retention is configurable (`AUDIT_RETENTION_DAYS`, 0 = keep).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from pymongo import ASCENDING, DESCENDING

from ..common import get_logger, iso, log_event, new_id, request_id_var

log = get_logger("audit")
COLLECTION = "audit_events"
STATUSES = ("success", "failure", "denied")
_SECRET_KEY = re.compile(r"pass(word)?|token|secret|cookie|authorization|hash|embedding|vector|csrf|api[_-]?key", re.I)
_MAX_STR, _MAX_ITEMS, _MAX_DEPTH = 300, 50, 3


def scrub(value: Any, depth: int = 0) -> Any:
    """Make metadata safe to store: drop secret-looking keys, cap string length, list size and nesting."""
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            return "…"
        return {str(k)[:60]: scrub(v, depth + 1) for k, v in list(value.items())[:_MAX_ITEMS] if not _SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple, set)):
        return [scrub(v, depth + 1) for v in list(value)[:_MAX_ITEMS]]
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR - 1] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_STR]


class AuditService:
    def __init__(self, mongo, retention_days: int = 365, query_chars: int = 300):
        self.mongo = mongo
        self.col = mongo.col(COLLECTION)
        self.retention_days = retention_days
        self.query_chars = query_chars
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        c = self.col
        c.create_index("event_id", unique=True)
        c.create_index([("actor_user_id", ASCENDING), ("timestamp", DESCENDING)])
        c.create_index([("action", ASCENDING), ("timestamp", DESCENDING)])
        c.create_index([("status", ASCENDING), ("timestamp", DESCENDING)])
        c.create_index([("target_type", ASCENDING), ("target_id", ASCENDING)])
        c.create_index([("timestamp", DESCENDING)])
        c.create_index([("subject_user_id", ASCENDING), ("timestamp", DESCENDING)])
        if self.retention_days > 0:  # MongoDB removes expired events itself; `expires_at` is a real datetime for the TTL monitor
            try:
                c.create_index("expires_at", expireAfterSeconds=0)
            except Exception:  # pragma: no cover - a pre-existing index with other options; purge() still works
                pass

    def record(self, action: str, *, status: str = "success", actor_role: str = "user", actor_user_id: str | None = None,
               actor_label: str | None = None, subject_user_id: str | None = None, target_type: str | None = None,
               target_id: str | None = None, metadata: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any] | None:
        """Append one event. `subject_user_id` is the account the action concerns when it differs from the actor (an administrator acting on a user).
        Auditing never breaks the request it observes; a write failure is logged loudly instead."""
        now = datetime.now(timezone.utc)
        doc: dict[str, Any] = {
            "event_id": new_id("evt"), "actor_user_id": actor_user_id, "actor_role": actor_role, "actor_label": actor_label,
            "subject_user_id": subject_user_id or (actor_user_id if actor_role == "user" else None), "action": action,
            "target_type": target_type, "target_id": target_id, "timestamp": iso(now), "status": status if status in STATUSES else "failure",
            "request_id": request_id or request_id_var.get(), "metadata": scrub(metadata or {})}
        if self.retention_days > 0:
            doc["expires_at"] = now + timedelta(days=self.retention_days)
        try:
            self.col.insert_one(dict(doc))
        except Exception as exc:
            log_event(log, "audit_write_failed", level=40, action=action, error_type=type(exc).__name__)
            return None
        doc.pop("expires_at", None)
        return doc

    # ------------------------------------------------------------------------------------------------------------------ queries
    @staticmethod
    def _filter(*, user_id: str | None = None, action: str | None = None, status: str | None = None, target_type: str | None = None,
                since: str | None = None, until: str | None = None, role: str | None = None, search: str | None = None) -> dict[str, Any]:
        q: dict[str, Any] = {}
        if user_id:
            q["$or"] = [{"actor_user_id": user_id}, {"subject_user_id": user_id}]
        if action:  # exact action, or a prefix family such as "query" / "memory"
            q["action"] = action if "." in action else {"$regex": f"^{re.escape(action)}\\."}
        if status:
            q["status"] = status
        if target_type:
            q["target_type"] = target_type
        if role:
            q["actor_role"] = role
        if since or until:
            q["timestamp"] = {**({"$gte": since} if since else {}), **({"$lte": until} if until else {})}
        if search:
            q["$and"] = [{"$or": [{"action": {"$regex": re.escape(search), "$options": "i"}},
                                  {"target_id": {"$regex": re.escape(search), "$options": "i"}},
                                  {"metadata.query": {"$regex": re.escape(search), "$options": "i"}},
                                  {"metadata.strategy": {"$regex": re.escape(search), "$options": "i"}},
                                  {"actor_label": {"$regex": re.escape(search), "$options": "i"}}]}]
        return q

    def list(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> dict[str, Any]:
        q = self._filter(**filters)
        total = self.col.count_documents(q)
        rows = [self._public(d) for d in self.col.find(q).sort([("timestamp", DESCENDING), ("event_id", DESCENDING)]).skip(offset).limit(limit)]
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def get(self, event_id: str) -> dict[str, Any] | None:
        d = self.col.find_one({"event_id": event_id})
        return self._public(d) if d else None

    @staticmethod
    def _public(d: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in d.items() if k not in ("_id", "expires_at")}

    def user_summaries(self, user_ids: Iterable[str]) -> dict[str, dict[str, int]]:
        """Per-account counters derived from the durable log (not from in-memory state)."""
        ids = list(user_ids)
        out = {u: {"queries_submitted": 0, "queries_succeeded": 0, "queries_failed": 0, "memory_saves": 0, "memory_save_failures": 0,
                   "memory_deletions": 0, "failed_operations": 0, "failed_logins": 0, "events": 0} for u in ids}
        if not ids:
            return out
        for r in self.col.aggregate([{"$match": {"subject_user_id": {"$in": ids}}},
                                     {"$group": {"_id": {"u": "$subject_user_id", "a": "$action", "s": "$status", "r": "$actor_role"}, "n": {"$sum": 1}}}]):
            u, a, st, role, n = r["_id"]["u"], r["_id"]["a"], r["_id"]["s"], r["_id"]["r"], r["n"]
            if role != "user":  # administrators acting on an account are not the account's own activity
                continue
            o = out[u]
            o["events"] += n
            if a == "auth.login":
                o["failed_logins"] += n if st != "success" else 0
            elif a == "query.submitted":
                o["queries_submitted"] += n
            elif a == "query.completed":
                o["queries_succeeded" if st == "success" else "queries_failed"] += n
            elif a == "memory.created":
                o["memory_saves" if st == "success" else "memory_save_failures"] += n
            elif a == "memory.deleted" and st == "success":
                o["memory_deletions"] += n
            if st != "success" and a != "auth.login":
                o["failed_operations"] += n
        return out

    def purge(self, older_than_days: int | None = None) -> int:
        days = self.retention_days if older_than_days is None else older_than_days
        if days <= 0:
            return 0
        cutoff = iso(datetime.now(timezone.utc) - timedelta(days=days))
        return int(self.col.delete_many({"timestamp": {"$lt": cutoff}}).deleted_count)
