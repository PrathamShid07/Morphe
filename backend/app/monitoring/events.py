"""Event persistence helpers. Events are durable audit records; they are never deleted when an operation is rolled back."""
from __future__ import annotations

from typing import Any

from ..common import get_logger, iso, log_event, new_id
from ..storage.mongo_store import MongoStore

log = get_logger("events")

ADAPTATION = "adaptation_events"
COMPRESSION = "compression_events"


class EventLog:
    def __init__(self, mongo: MongoStore):
        self.mongo = mongo

    # --------------------------------------------------------------- adaptation
    def start_adaptation(self, *, operation: str, trigger: str, reason: str, status: str = "running",
                         event_id: str | None = None, **fields: Any) -> dict[str, Any]:
        doc = {"event_id": event_id or new_id("adapt"), "operation": operation, "trigger": trigger, "reason": reason,
               "status": status, "started_at": iso(), "finished_at": None, "duration_ms": None, "error": None,
               "timeline": [{"at": iso(), "status": status, "note": "operation recorded"}], **fields}
        self.mongo.insert_event(ADAPTATION, doc)
        return doc

    def update_adaptation(self, event_id: str, note: str | None = None, **fields: Any) -> None:
        update: dict[str, Any] = {"$set": fields}
        if note or "status" in fields:
            update["$push"] = {"timeline": {"at": iso(), "status": fields.get("status"), "note": note}}
        self.mongo.col(ADAPTATION).update_one({"event_id": event_id}, update)

    def get_adaptation(self, event_id: str) -> dict[str, Any] | None:
        return self.mongo.get_event(ADAPTATION, "event_id", event_id)

    # -------------------------------------------------------------- compression
    def start_compression(self, *, operation: str, trigger: str, reason: str, status: str = "running",
                          **fields: Any) -> dict[str, Any]:
        doc = {"event_id": new_id("cmp"), "operation": operation, "trigger": trigger, "reason": reason,
               "status": status, "timestamp": iso(), "finished_at": None, "tokens_before": None, "tokens_after": None,
               "tokens_reclaimed": None, "error": None, **fields}
        self.mongo.insert_event(COMPRESSION, doc)
        return doc

    def update_compression(self, event_id: str, **fields: Any) -> None:
        self.mongo.col(COMPRESSION).update_one({"event_id": event_id}, {"$set": fields})
