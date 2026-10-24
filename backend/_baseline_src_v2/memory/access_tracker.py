"""Query/access tracking: per-query events, memory access counts, and the adaptation window counters."""
from __future__ import annotations

from itertools import combinations
from typing import Any

from ..common import iso, new_id
from ..storage.mongo_store import MongoStore, clean

WINDOW_KEY = "adaptation_window"


def pair_key(a: str, b: str) -> str:
    a, b = sorted((a, b))
    return f"{a}|{b}"


def _new_window() -> dict[str, Any]:
    return {"window_id": new_id("win"), "started_at": iso(), "query_count": 0, "success_count": 0,
            "fallback_count": 0, "candidate_sum": 0, "latency_sum": 0.0, "module_counts": {}, "pair_counts": {}}


class AccessTracker:
    def __init__(self, mongo: MongoStore):
        self.mongo = mongo

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        """Persist the query event, bump access stats, update window counters. Returns the updated window."""
        window = self._bump_window(event)
        self.mongo.insert_event("query_events", {**event, "window_id": window["window_id"]})
        if event["result_ids"]:
            self.mongo.record_access(event["result_ids"], event["timestamp"])
        return window

    def _bump_window(self, event: dict[str, Any]) -> dict[str, Any]:
        inc: dict[str, Any] = {"value.query_count": 1, "value.fallback_count": int(event["fallback_used"]),
                               "value.candidate_sum": int(event["candidate_count"]),
                               "value.latency_sum": float(event["latency_ms"]),
                               "value.success_count": int(event["success"])}
        if event["success"]:  # modules used together in a successful query -> co-access evidence
            mods = event["modules_accessed"]
            for m in mods:
                inc[f"value.module_counts.{m}"] = 1
            for a, b in combinations(mods, 2):
                inc[f"value.pair_counts.{pair_key(a, b)}"] = 1
        fresh = _new_window()
        doc = self.mongo.col("system_state").find_one_and_update(
            {"_id": WINDOW_KEY},
            {"$inc": inc, "$setOnInsert": {"value.window_id": fresh["window_id"], "value.started_at": fresh["started_at"]},
             "$set": {"updated_at": iso()}},
            upsert=True, return_document=True)
        return doc["value"]

    def window(self) -> dict[str, Any]:
        doc = clean(self.mongo.col("system_state").find_one({"_id": WINDOW_KEY}))
        return doc["value"] if doc else _new_window() | {"window_id": None, "started_at": None}

    def reset_window(self) -> dict[str, Any]:
        """Atomically start a new window; returns the finished one."""
        old = self.mongo.col("system_state").find_one_and_update(
            {"_id": WINDOW_KEY}, {"$set": {"value": _new_window(), "updated_at": iso()}}, upsert=True)
        return (old or {}).get("value") or _new_window()
