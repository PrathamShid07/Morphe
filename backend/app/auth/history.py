"""A user's own recent query results, scoped by the owner's `user_id`. One user can never read another's."""
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

    def save(self, owner: str, query: str, body: dict[str, Any]) -> None:
        self._col.create_index("query_id", unique=True)
        self._col.create_index([("owner_user_id", 1), ("created_at", -1)])
        self._col.insert_one({"query_id": body["query_id"], "owner_user_id": owner, "query": query, "mode": body.get("mode"),
                              "top_k": body.get("top_k"), "created_at": iso(), "response": body})
        old = list(self._col.find({"owner_user_id": owner}, {"query_id": 1}).sort("created_at", -1).skip(self.keep))
        if old:
            self._col.delete_many({"owner_user_id": owner, "query_id": {"$in": [o["query_id"] for o in old]}})

    def list(self, owner: str, limit: int) -> list[dict[str, Any]]:
        rows = self._col.find({"owner_user_id": owner}).sort("created_at", -1).limit(limit)
        return [{"query_id": r["query_id"], "query": r["query"], "mode": r.get("mode"), "top_k": r.get("top_k"), "created_at": r["created_at"],
                 "result_count": len(r["response"].get("results", [])), "latency_ms": r["response"].get("latency_ms")} for r in rows]

    def get(self, owner: str, query_id: str) -> dict[str, Any]:
        doc = clean(self._col.find_one({"query_id": query_id, "owner_user_id": owner}))  # the owner filter IS the authorization check
        if not doc:
            raise NotFoundError("query not found", code="query_not_found")  # 404 for both 'absent' and 'someone else's'
        return {"query": doc["query"], "created_at": doc["created_at"], **doc["response"]}

    def delete_owner(self, owner: str) -> int:
        return int(self._col.delete_many({"owner_user_id": owner}).deleted_count)

    # ---- administrator view: who asked what, never the retrieved memory text
    def admin_list(self, owner: str, limit: int, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        total = self._col.count_documents({"owner_user_id": owner})
        rows = self._col.find({"owner_user_id": owner}).sort("created_at", -1).skip(offset).limit(limit)
        return [{"query_id": r["query_id"], "query": r["query"], "mode": r.get("mode"), "created_at": r["created_at"],
                 "result_count": len(r["response"].get("results", [])), "latency_ms": r["response"].get("latency_ms")} for r in rows], total

    def admin_get(self, owner: str, query_id: str) -> dict[str, Any]:
        doc = clean(self._col.find_one({"query_id": query_id, "owner_user_id": owner}))
        if not doc:
            raise NotFoundError("query not found", code="query_not_found")
        resp = doc["response"]
        results = [{k: r.get(k) for k in ("memory_id", "score", "module_id", "module_name", "source_name", "scope")} for r in resp.get("results", [])]  # no memory text
        return {"query_id": doc["query_id"], "user_id": owner, "query": doc["query"], "mode": doc.get("mode"), "top_k": doc.get("top_k"), "created_at": doc["created_at"],
                "latency_ms": resp.get("latency_ms"), "fallback_used": resp.get("fallback_used"), "results": results,
                "trace": {k: v for k, v in (resp.get("trace") or {}).items() if k in ("mode", "seed_modules", "searched_modules", "fallback_used", "fallback_reason")}}
