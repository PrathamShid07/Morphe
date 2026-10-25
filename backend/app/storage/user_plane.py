"""The personal-memory plane: private vectors (Qdrant) and metadata (MongoDB), always addressed through an owner.

Why a separate plane (and not extra payload fields in the shared collections)? The shared collections are governed by whole-architecture
machinery -- versioning, restore/rollback, compression, adaptation, the integrity validator and the benchmarks all read or rewrite
*every* record. Keeping private data in its own collections guarantees that none of that machinery can read, move or roll back a user's
memories. Every point still carries `owner_user_id` and `visibility`, and the adapters below make an unowned call impossible:

* `UserVectorStore` methods take the owner as their first argument and ALWAYS add `owner_user_id == owner` to the Qdrant filter. There is no
  method without it, and no `module_ids=None` shortcut: "all modules" means "all of this owner's modules".
* `UserDataStore` (MongoDB) likewise requires the owner for every read and write, and every unique index includes it.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Sequence

import numpy as np
from pymongo import ASCENDING, DESCENDING

from ..common import iso
from .qdrant_store import ACTIVE, Hit, ModuleHit, QdrantStore, models, point_id

PRIVATE = "private"


def owner_token(user_id: str) -> str:
    """Short, stable, id-safe token embedded in every id the user plane mints, so two users can never collide on an id or a point."""
    return re.sub(r"[^A-Za-z0-9]", "", user_id)[-10:].lower() or "x"


class UserVectorStore:
    def __init__(self, base: QdrantStore, memory_collection: str, module_collection: str):
        self.base = base
        self.client = base.client
        self.memory_collection = memory_collection
        self.module_collection = module_collection
        self.dimension = base.dimension
        self.is_local = base.is_local
        self.ensure()

    # ----------------------------------------------------------------------------------------------------------------- schema
    def ensure(self) -> None:
        for name in (self.memory_collection, self.module_collection):
            if not self.client.collection_exists(name):
                self.client.create_collection(name, vectors_config=models.VectorParams(size=self.dimension, distance=models.Distance.COSINE))
        if not self.is_local:  # payload indexes have no effect in local mode, but filtered search on a server needs them
            for coll, fields in ((self.memory_collection, ("owner_user_id", "status", "module_id", "source_id", "visibility")),
                                 (self.module_collection, ("owner_user_id", "module_id"))):
                for f in fields:
                    self.client.create_payload_index(coll, f, models.PayloadSchemaType.KEYWORD)

    def payload_indexes(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for coll in (self.memory_collection, self.module_collection):
            info = self.client.get_collection(coll)
            out[coll] = sorted((info.payload_schema or {}).keys())
        return out

    # --------------------------------------------------------------------------------------------------------------- filters
    @staticmethod
    def _flt(owner: str, module_ids: Sequence[str] | None = None, status: str | None = ACTIVE, source_id: str | None = None) -> models.Filter:
        if not owner:
            raise ValueError("an owner is required for every personal-memory operation")
        must: list[models.Condition] = [models.FieldCondition(key="owner_user_id", match=models.MatchValue(value=owner))]
        if status:
            must.append(models.FieldCondition(key="status", match=models.MatchValue(value=status)))
        if module_ids is not None:
            must.append(models.FieldCondition(key="module_id", match=models.MatchAny(any=list(module_ids))))
        if source_id:
            must.append(models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)))
        return models.Filter(must=must)

    @staticmethod
    def _check_items(owner: str, items) -> None:
        for _, _, payload in items:
            if payload.get("owner_user_id") != owner:  # a caller can never write a point for somebody else
                raise ValueError("payload owner does not match the owner of the operation")

    # --------------------------------------------------------------------------------------------------------------- memories
    def upsert_memories(self, owner: str, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]], batch: int = 256) -> None:
        self._check_items(owner, items)
        for i in range(0, len(items), batch):
            self.client.upsert(self.memory_collection, wait=True, points=[
                models.PointStruct(id=point_id(mid), vector=list(map(float, vec)), payload=pl) for mid, vec, pl in items[i:i + batch]])

    def search_memories(self, owner: str, vector: Sequence[float], module_ids: Sequence[str] | None, limit: int, status: str | None = ACTIVE) -> list[Hit]:
        if module_ids is not None and len(module_ids) == 0:
            return []
        flt, q = self._flt(owner, module_ids, status), list(map(float, vector))
        pts = self.base._tie_safe(lambda n: self.client.query_points(self.memory_collection, query=q, limit=n, query_filter=flt, with_payload=True).points,
                                  limit, "memory_id")
        return [Hit(p.payload["memory_id"], p.payload.get("module_id", ""), float(p.score), p.payload) for p in pts]

    def count_memories(self, owner: str, module_ids: Sequence[str] | None = None, status: str | None = ACTIVE) -> int:
        if module_ids is not None and len(module_ids) == 0:
            return 0
        return self.client.count(self.memory_collection, count_filter=self._flt(owner, module_ids, status), exact=True).count

    def scroll_memories(self, owner: str, module_id: str | None = None, status: str | None = ACTIVE, with_vectors: bool = False,
                        source_id: str | None = None, batch: int = 512) -> Iterator[tuple[str, dict[str, Any], np.ndarray | None]]:
        flt, offset = self._flt(owner, [module_id] if module_id else None, status, source_id), None
        while True:
            pts, offset = self.client.scroll(self.memory_collection, scroll_filter=flt, limit=batch, offset=offset, with_payload=True, with_vectors=with_vectors)
            for p in pts:
                yield p.payload["memory_id"], p.payload, (np.asarray(p.vector, dtype=np.float32) if with_vectors and p.vector is not None else None)
            if offset is None:
                break

    def get_memories(self, owner: str, memory_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Payloads of the given ids that belong to `owner`. Ids owned by anyone else are silently absent (same answer as 'does not exist')."""
        out: dict[str, dict[str, Any]] = {}
        ids = list(memory_ids)
        for i in range(0, len(ids), 512):
            for p in self.client.retrieve(self.memory_collection, ids=[point_id(m) for m in ids[i:i + 512]], with_payload=True, with_vectors=False):
                if p.payload.get("owner_user_id") == owner:
                    out[p.payload["memory_id"]] = p.payload
        return out

    def set_status(self, owner: str, memory_ids: Sequence[str], status: str) -> None:
        mine = list(self.get_memories(owner, memory_ids))
        for i in range(0, len(mine), 512):
            self.client.set_payload(self.memory_collection, payload={"status": status}, points=[point_id(m) for m in mine[i:i + 512]], wait=True)

    def delete_memories(self, owner: str, memory_ids: Sequence[str]) -> None:
        mine = list(self.get_memories(owner, memory_ids))
        for i in range(0, len(mine), 512):
            self.client.delete(self.memory_collection, points_selector=models.PointIdsList(points=[point_id(m) for m in mine[i:i + 512]]), wait=True)

    # ---------------------------------------------------------------------------------------------------------------- modules
    def upsert_module_profiles(self, owner: str, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]]) -> None:
        if not items:
            return
        self.client.upsert(self.module_collection, wait=True, points=[
            models.PointStruct(id=point_id(mid), vector=list(map(float, vec)),
                               payload={**pl, "module_id": mid, "owner_user_id": owner, "visibility": PRIVATE}) for mid, vec, pl in items])

    def search_modules(self, owner: str, vector: Sequence[float], limit: int) -> list[ModuleHit]:
        q, flt = list(map(float, vector)), self._flt(owner, None, None)
        pts = self.base._tie_safe(lambda n: self.client.query_points(self.module_collection, query=q, limit=n, query_filter=flt, with_payload=True).points,
                                  limit, "module_id")
        return [ModuleHit(p.payload["module_id"], float(p.score), p.payload) for p in pts]

    def count_modules(self, owner: str) -> int:
        return self.client.count(self.module_collection, count_filter=self._flt(owner, None, None), exact=True).count

    def delete_module_profiles(self, owner: str, module_ids: Sequence[str]) -> None:
        ids = [m for m in module_ids if m]
        if not ids:
            return
        flt = models.Filter(must=[models.FieldCondition(key="owner_user_id", match=models.MatchValue(value=owner)),
                                  models.FieldCondition(key="module_id", match=models.MatchAny(any=ids))])
        self.client.delete(self.module_collection, points_selector=models.FilterSelector(filter=flt), wait=True)

    def delete_owner(self, owner: str) -> None:
        """Erase everything an owner has in the vector store (account deletion / tests)."""
        sel = models.FilterSelector(filter=self._flt(owner, None, None))
        self.client.delete(self.memory_collection, points_selector=sel, wait=True)
        self.client.delete(self.module_collection, points_selector=sel, wait=True)


class UserDataStore:
    """MongoDB side of the personal plane. Every method requires the owner; every query is built with it; unique keys include it."""

    SOURCES, MEMORIES, MODULES, EDGES = "user_sources", "user_memories", "user_modules", "user_edges"

    def __init__(self, mongo):
        self.mongo = mongo
        self._indexes()

    def _indexes(self) -> None:
        m = self.mongo.col
        m(self.SOURCES).create_index([("owner_user_id", ASCENDING), ("source_id", ASCENDING)], unique=True)
        m(self.MEMORIES).create_index([("owner_user_id", ASCENDING), ("memory_id", ASCENDING)], unique=True)
        m(self.MEMORIES).create_index([("owner_user_id", ASCENDING), ("status", ASCENDING), ("module_id", ASCENDING)])
        m(self.MEMORIES).create_index([("owner_user_id", ASCENDING), ("source_id", ASCENDING)])
        m(self.MEMORIES).create_index([("owner_user_id", ASCENDING), ("created_at", DESCENDING)])
        m(self.MODULES).create_index([("owner_user_id", ASCENDING), ("module_id", ASCENDING)], unique=True)
        m(self.EDGES).create_index([("owner_user_id", ASCENDING), ("edge_id", ASCENDING)], unique=True)

    @staticmethod
    def _own(owner: str, **extra: Any) -> dict[str, Any]:
        if not owner:
            raise ValueError("an owner is required for every personal-memory operation")
        return {"owner_user_id": owner, **extra}

    # ---------------------------------------------------------------------------------------------------------------- sources
    def get_source(self, owner: str, source_id: str) -> dict[str, Any] | None:
        return self.mongo.col(self.SOURCES).find_one(self._own(owner, source_id=source_id), {"_id": 0})

    def put_source(self, owner: str, doc: dict[str, Any]) -> None:
        d = {**doc, "owner_user_id": owner, "visibility": PRIVATE}
        self.mongo.col(self.SOURCES).update_one(self._own(owner, source_id=doc["source_id"]), {"$set": d}, upsert=True)

    def delete_source(self, owner: str, source_id: str) -> None:
        self.mongo.col(self.SOURCES).delete_one(self._own(owner, source_id=source_id))

    def list_sources(self, owner: str, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        q = self._own(owner, status="active")
        c = self.mongo.col(self.SOURCES)
        return list(c.find(q, {"_id": 0}).sort([("created_at", DESCENDING)]).skip(offset).limit(limit)), c.count_documents(q)

    # -------------------------------------------------------------------------------------------------------------- memories
    def put_memories(self, owner: str, docs: Sequence[dict[str, Any]]) -> None:
        col = self.mongo.col(self.MEMORIES)
        for d in docs:
            doc = {**d, "owner_user_id": owner, "visibility": PRIVATE}
            col.update_one(self._own(owner, memory_id=d["memory_id"]), {"$set": doc}, upsert=True)

    def set_memory_fields(self, owner: str, memory_ids: Sequence[str], fields: dict[str, Any]) -> None:
        self.mongo.col(self.MEMORIES).update_many(self._own(owner, memory_id={"$in": list(memory_ids)}), {"$set": fields})

    def delete_memory_docs(self, owner: str, memory_ids: Sequence[str]) -> None:
        self.mongo.col(self.MEMORIES).delete_many(self._own(owner, memory_id={"$in": list(memory_ids)}))

    def get_memory(self, owner: str, memory_id: str) -> dict[str, Any] | None:
        return self.mongo.col(self.MEMORIES).find_one(self._own(owner, memory_id=memory_id), {"_id": 0})

    def page_memories(self, owner: str, *, status: str | None = ACTIVE, module_id: str | None = None, search: str | None = None,
                      limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        q = self._own(owner)
        if status:
            q["status"] = status
        if module_id:
            q["module_id"] = module_id
        if search:
            q["text_preview"] = {"$regex": re.escape(search), "$options": "i"}
        c = self.mongo.col(self.MEMORIES)
        return list(c.find(q, {"_id": 0}).sort([("created_at", DESCENDING), ("memory_id", ASCENDING)]).skip(offset).limit(limit)), c.count_documents(q)

    def active_counts(self, owner: str) -> dict[str, dict[str, Any]]:
        rows = self.mongo.col(self.MEMORIES).aggregate([
            {"$match": self._own(owner, status="active")},
            {"$group": {"_id": "$module_id", "n": {"$sum": 1}, "tokens": {"$sum": "$token_count"}, "last": {"$max": "$created_at"}}}])
        return {r["_id"]: {"records": r["n"], "tokens": r["tokens"], "last": r["last"]} for r in rows}

    def stats(self, owner: str) -> dict[str, int]:
        c = self.mongo.col
        return {"memories": c(self.MEMORIES).count_documents(self._own(owner, status="active")),
                "modules": c(self.MODULES).count_documents(self._own(owner, status="active", active_record_count={"$gt": 0})),
                "sources": c(self.SOURCES).count_documents(self._own(owner, status="active")),
                "tokens": sum(v["tokens"] or 0 for v in self.active_counts(owner).values())}

    # ---------------------------------------------------------------------------------------------------------------- modules
    def get_module(self, owner: str, module_id: str, active_only: bool = True) -> dict[str, Any] | None:
        q = self._own(owner, module_id=module_id)
        if active_only:
            q["status"] = "active"
        return self.mongo.col(self.MODULES).find_one(q, {"_id": 0})

    def put_module(self, owner: str, doc: dict[str, Any]) -> None:
        d = {**doc, "owner_user_id": owner, "visibility": PRIVATE}
        self.mongo.col(self.MODULES).update_one(self._own(owner, module_id=doc["module_id"]), {"$set": d}, upsert=True)

    def list_modules(self, owner: str, include_retired: bool = False) -> list[dict[str, Any]]:
        q = self._own(owner)
        if not include_retired:
            q["status"] = "active"
        return list(self.mongo.col(self.MODULES).find(q, {"_id": 0}).sort("module_id", ASCENDING))

    # ------------------------------------------------------------------------------------------------------------------ edges
    def list_edges(self, owner: str) -> list[dict[str, Any]]:
        return list(self.mongo.col(self.EDGES).find(self._own(owner), {"_id": 0}).sort("edge_id", ASCENDING))

    def put_edge(self, owner: str, doc: dict[str, Any]) -> None:
        self.mongo.col(self.EDGES).update_one(self._own(owner, edge_id=doc["edge_id"]), {"$set": {**doc, "owner_user_id": owner}}, upsert=True)

    def get_edge(self, owner: str, edge_id: str) -> dict[str, Any] | None:
        return self.mongo.col(self.EDGES).find_one(self._own(owner, edge_id=edge_id), {"_id": 0})

    def delete_edges_of(self, owner: str, module_id: str) -> None:
        self.mongo.col(self.EDGES).delete_many(self._own(owner, **{"$or": [{"source": module_id}, {"target": module_id}]}))

    # ------------------------------------------------------------------------------------------------------------------ erase
    def delete_owner(self, owner: str) -> None:
        for coll in (self.SOURCES, self.MEMORIES, self.MODULES, self.EDGES):
            self.mongo.col(coll).delete_many(self._own(owner))
