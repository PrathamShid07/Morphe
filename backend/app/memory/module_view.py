"""Read models for modules (list + detail) used by the Graph Explorer and module API."""
from __future__ import annotations

from typing import Any

from ..common import NotFoundError
from ..graph.manager import GraphManager
from ..storage.mongo_store import MongoStore, clean


class ModuleView:
    def __init__(self, mongo: MongoStore, graph: GraphManager):
        self.mongo = mongo
        self.graph = graph

    def _usage(self) -> dict[str, dict[str, Any]]:
        rows = self.mongo.col("memory_metadata").aggregate([
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$module_id", "access": {"$sum": "$access_count"}, "tokens": {"$sum": "$token_count"},
                        "last": {"$max": "$last_accessed_at"}}}])
        return {r["_id"]: r for r in rows}

    def list(self) -> list[dict[str, Any]]:
        usage = self._usage()
        docs = self.mongo.list_modules()
        by_parent: dict[str | None, list[str]] = {}
        for d in docs:
            by_parent.setdefault(d.get("parent_id"), []).append(d["module_id"])
        out = []
        for d in docs:
            mid = d["module_id"]
            u = usage.get(mid, {})
            nb = self.graph.neighbors(mid)
            out.append({
                "module_id": mid, "name": d.get("name", mid), "parent_id": d.get("parent_id"),
                "children": sorted(by_parent.get(mid, [])), "record_count": d.get("active_record_count", 0),
                "is_container": d.get("active_record_count", 0) == 0, "keywords": d.get("keywords", []),
                "description": d.get("description", ""), "token_count": int(u.get("tokens", 0)),
                "total_access_count": int(u.get("access", 0)), "last_accessed_at": u.get("last"),
                "shortcut_edges": sum(1 for n in nb if n["relation_type"] == "co_access"),
                "created_at": d.get("created_at"), "last_adapted_at": d.get("last_change_at"),
                "last_change_op": d.get("last_change_op"), "profile_status":
                    "container (not indexed)" if d.get("active_record_count", 0) == 0 else "indexed",
                "profile_version": d.get("profile_version")})
        return out

    def detail(self, module_id: str, memory_limit: int = 25) -> dict[str, Any]:
        mods = {m["module_id"]: m for m in self.list()}
        if module_id not in mods:
            raise NotFoundError(f"module {module_id} not found", code="module_not_found")
        base = mods[module_id]
        nb = []
        for n in self.graph.neighbors(module_id):
            other = mods.get(n["module_id"], {})
            nb.append({**n, "name": other.get("name"), "record_count": other.get("record_count", 0)})
        mems, _ = self.mongo.page("memory_metadata", {"module_id": module_id, "status": "active"},
                                  [("source_name", 1), ("chunk_index", 1)], memory_limit, 0)
        events, _ = self.mongo.list_events("adaptation_events", "started_at", 10, 0, {"$or": [
            {"affected_modules": module_id}, {"created_module_ids": module_id}, {"removed_module_ids": module_id}]})
        recent = self.mongo.col("query_events").count_documents({"tracked": True, "modules_accessed": module_id})
        return {**base, "parent": mods.get(base["parent_id"]) if base["parent_id"] else None,
                "neighbors": nb, "memories": mems, "adaptation_history": events, "queries_touching_module": recent}
