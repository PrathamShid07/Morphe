"""A visitor's own recent query results, scoped by their server-issued session id. One visitor can never read another's."""
from __future__ import annotations

from typing import Any

from ..common import NotFoundError, iso
from ..storage.mongo_store import MongoStore, clean

COLLECTION = "user_query_results"


class QueryHistory:
    def __init__(self, mongo: MongoStore, keep: int):
        self.mongo = mongo
        self.keep = max(1, keep)
        self._col = mongo.col(COLLECTION)

    def save(self, sid: str, query: str, body: dict[str, Any]) -> None:
        self._col.create_index("query_id", unique=True)
        self._col.create_index([("session_id", 1), ("created_at", -1)])
        self._col.insert_one({"query_id": body["query_id"], "session_id": sid, "query": query, "mode": body.get("mode"),
                              "top_k": body.get("top_k"), "created_at": iso(), "response": body})
        old = list(self._col.find({"session_id": sid}, {"query_id": 1}).sort("created_at", -1).skip(self.keep))
        if old:
            self._col.delete_many({"query_id": {"$in": [o["query_id"] for o in old]}})

    def list(self, sid: str, limit: int) -> list[dict[str, Any]]:
        rows = self._col.find({"session_id": sid}).sort("created_at", -1).limit(limit)
        return [{"query_id": r["query_id"], "query": r["query"], "mode": r.get("mode"), "top_k": r.get("top_k"), "created_at": r["created_at"],
                 "result_count": len(r["response"].get("results", [])), "latency_ms": r["response"].get("latency_ms")} for r in rows]

    def get(self, sid: str, query_id: str) -> dict[str, Any]:
        doc = clean(self._col.find_one({"query_id": query_id, "session_id": sid}))  # the session filter IS the authorization check
        if not doc:
            raise NotFoundError("query not found", code="query_not_found")  # 404 for both 'absent' and 'someone else's'
        return {"query": doc["query"], "created_at": doc["created_at"], **doc["response"]}
