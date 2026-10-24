"""MongoDB adapter: durable metadata, event logs, architecture versions and snapshots, operation journal."""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from pymongo import ASCENDING, DESCENDING, UpdateOne

from ..common import DependencyUnavailable, iso

COLLECTIONS = [
    "sources", "memory_metadata", "modules", "graph_edges", "query_events", "adaptation_events",
    "compression_events", "architecture_versions", "architecture_snapshots", "evaluation_queries",
    "evaluation_runs", "system_metrics", "operations", "system_state",
]


def clean(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


class MongoStore:
    def __init__(self, db: Any, client: Any = None, is_mock: bool = False):
        self.db = db
        self.client = client
        self.is_mock = is_mock

    def _bulk(self, collection: str, specs: Sequence[tuple[dict[str, Any], dict[str, Any], bool]]) -> None:
        """Batched updates: one bulk_write on MongoDB; a plain loop on the mongomock test double."""
        if not specs:
            return
        col = self.col(collection)
        if self.is_mock:
            for flt, upd, upsert in specs:
                col.update_one(flt, upd, upsert=upsert)
        else:
            col.bulk_write([UpdateOne(flt, upd, upsert=upsert) for flt, upd, upsert in specs], ordered=False)

    # ------------------------------------------------------------- factory
    @classmethod
    def from_settings(cls, s) -> "MongoStore":
        if s.mongo_backend == "mongomock":
            import mongomock
            client = mongomock.MongoClient()
            store = cls(client[s.mongodb_database], client, is_mock=True)
        else:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError
            client = MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=s.mongodb_timeout_ms, tz_aware=True)
            try:
                client.admin.command("ping")
            except PyMongoError as exc:
                raise DependencyUnavailable(
                    f"MongoDB is not reachable at {s.mongodb_uri}. Start it (scripts/dev_mongo.sh) or set MONGODB_URI. "
                    f"({type(exc).__name__})", code="mongodb_unavailable") from exc
            store = cls(client[s.mongodb_database], client)
        store.ensure_indexes()
        return store

    def close(self) -> None:
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass

    def ping(self) -> bool:
        if self.client is not None and hasattr(self.client, "admin"):
            self.client.admin.command("ping")
        return True

    def col(self, name: str):
        return self.db[name]

    def ensure_indexes(self) -> None:
        idx = [
            ("sources", [("source_id", ASCENDING)], True), ("sources", [("content_hash", ASCENDING)], False),
            ("memory_metadata", [("memory_id", ASCENDING)], True),
            ("memory_metadata", [("module_id", ASCENDING), ("status", ASCENDING)], False),
            ("memory_metadata", [("source_id", ASCENDING)], False),
            ("memory_metadata", [("status", ASCENDING), ("created_at", DESCENDING)], False),
            ("modules", [("module_id", ASCENDING)], True),
            ("graph_edges", [("edge_id", ASCENDING)], True),
            ("query_events", [("query_id", ASCENDING)], True), ("query_events", [("timestamp", DESCENDING)], False),
            ("adaptation_events", [("event_id", ASCENDING)], True),
            ("adaptation_events", [("started_at", DESCENDING)], False),
            ("compression_events", [("event_id", ASCENDING)], True),
            ("compression_events", [("timestamp", DESCENDING)], False),
            ("architecture_versions", [("version_id", ASCENDING)], True),
            ("architecture_snapshots", [("version_id", ASCENDING)], True),
            ("evaluation_queries", [("query_id", ASCENDING)], True),
            ("evaluation_runs", [("run_id", ASCENDING)], True),
            ("system_metrics", [("timestamp", DESCENDING)], False),
            ("operations", [("operation_id", ASCENDING)], True),
        ]
        for name, keys, unique in idx:
            self.col(name).create_index(keys, unique=unique)

    def reset(self) -> None:
        for name in COLLECTIONS:
            self.col(name).delete_many({})

    # ---------------------------------------------------------- system state
    def get_state(self, key: str, default: Any = None) -> Any:
        doc = self.col("system_state").find_one({"_id": key})
        return default if doc is None else doc.get("value")

    def set_state(self, key: str, value: Any) -> None:
        self.col("system_state").update_one({"_id": key}, {"$set": {"value": value, "updated_at": iso()}}, upsert=True)

    def next_counter(self, key: str) -> int:
        doc = self.col("system_state").find_one_and_update(
            {"_id": f"counter:{key}"}, {"$inc": {"value": 1}}, upsert=True, return_document=True)
        return int(doc["value"])

    # ---------------------------------------------------------------- events
    def insert_event(self, collection: str, doc: dict[str, Any]) -> None:
        self.col(collection).insert_one(dict(doc))

    def update_event(self, collection: str, key_field: str, key: str, fields: dict[str, Any]) -> None:
        self.col(collection).update_one({key_field: key}, {"$set": fields})

    def get_event(self, collection: str, key_field: str, key: str) -> dict[str, Any] | None:
        return clean(self.col(collection).find_one({key_field: key}))

    def list_events(self, collection: str, sort_field: str, limit: int = 20, offset: int = 0,
                    query: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int]:
        c = self.col(collection)
        total = c.count_documents(query or {})
        cur = c.find(query or {}).sort(sort_field, DESCENDING).skip(offset).limit(limit)
        return [clean(d) for d in cur], total  # type: ignore[misc]

    def page(self, collection: str, query: dict[str, Any], sort: list[tuple[str, int]], limit: int,
             offset: int) -> tuple[list[dict[str, Any]], int]:
        c = self.col(collection)
        total = c.count_documents(query)
        cur = c.find(query).sort(sort).skip(offset).limit(limit)
        return [clean(d) for d in cur], total  # type: ignore[misc]

    # -------------------------------------------------------- memory metadata
    def upsert_memory_metadata(self, docs: Sequence[dict[str, Any]]) -> None:
        self._bulk("memory_metadata", [({"memory_id": d["memory_id"]}, {"$set": d}, True) for d in docs])

    def get_memory_metadata(self, memory_id: str) -> dict[str, Any] | None:
        return clean(self.col("memory_metadata").find_one({"memory_id": memory_id}))

    def get_memory_metadata_many(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(ids)
        if not ids:
            return {}
        return {d["memory_id"]: clean(d) for d in self.col("memory_metadata").find({"memory_id": {"$in": ids}})}  # type: ignore[misc]

    def update_memory_metadata(self, memory_ids: Sequence[str], fields: dict[str, Any]) -> None:
        if memory_ids:
            self.col("memory_metadata").update_many({"memory_id": {"$in": list(memory_ids)}}, {"$set": fields})

    def active_memory_docs(self, projection: dict[str, int] | None = None) -> list[dict[str, Any]]:
        return [clean(d) for d in self.col("memory_metadata").find({"status": "active"}, projection)]  # type: ignore[misc]

    def active_token_total(self) -> tuple[int, int]:
        rows = list(self.col("memory_metadata").aggregate(
            [{"$match": {"status": "active"}}, {"$group": {"_id": None, "tokens": {"$sum": "$token_count"},
                                                            "count": {"$sum": 1}}}]))
        return (int(rows[0]["tokens"]), int(rows[0]["count"])) if rows else (0, 0)

    def module_active_counts(self) -> dict[str, int]:
        rows = self.col("memory_metadata").aggregate(
            [{"$match": {"status": "active"}}, {"$group": {"_id": "$module_id", "n": {"$sum": 1}}}])
        return {r["_id"]: int(r["n"]) for r in rows}

    def record_access(self, memory_ids: Sequence[str], when: str) -> None:
        self._bulk("memory_metadata", [({"memory_id": m}, {"$inc": {"access_count": 1},
                                                             "$set": {"last_accessed_at": when}}, False)
                                       for m in memory_ids])

    # --------------------------------------------------------------- modules
    def upsert_modules(self, docs: Sequence[dict[str, Any]]) -> None:
        self._bulk("modules", [({"module_id": d["module_id"]}, {"$set": d}, True) for d in docs])

    def list_modules(self, include_retired: bool = False) -> list[dict[str, Any]]:
        q = {} if include_retired else {"status": "active"}
        return [clean(d) for d in self.col("modules").find(q).sort("module_id", ASCENDING)]  # type: ignore[misc]

    def replace_edges(self, docs: Sequence[dict[str, Any]]) -> None:
        self.col("graph_edges").delete_many({})
        if docs:
            self.col("graph_edges").insert_many([dict(d) for d in docs])

    def list_edges(self) -> list[dict[str, Any]]:
        return [clean(d) for d in self.col("graph_edges").find({}).sort("edge_id", ASCENDING)]  # type: ignore[misc]

    # ------------------------------------------------------------ operations
    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        return clean(self.col("operations").find_one({"operation_id": operation_id}))

    def open_operations(self) -> list[dict[str, Any]]:
        return [clean(d) for d in self.col("operations").find({"status": {"$in": ["running", "applying"]}})]  # type: ignore[misc]
