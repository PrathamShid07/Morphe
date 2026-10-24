"""Qdrant adapter. All vector-store operations live here; nothing else imports qdrant_client."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
from qdrant_client import QdrantClient, models

from ..common import DependencyUnavailable

_NS = uuid.UUID("6f1d8b52-0f0e-4c67-9a53-6d6f72706865")
ACTIVE = "active"


def point_id(key: str) -> str:
    """Qdrant needs UUID/int IDs; map our stable string IDs deterministically."""
    return str(uuid.uuid5(_NS, key))


@dataclass
class Hit:
    memory_id: str
    module_id: str
    score: float
    payload: dict[str, Any]


@dataclass
class ModuleHit:
    module_id: str
    score: float
    payload: dict[str, Any]


class QdrantStore:
    def __init__(self, client: QdrantClient, memory_collection: str, module_collection: str, dimension: int,
                 local_path: str | None = None, is_local: bool = True, tie_safe: bool = True):
        self.tie_safe = tie_safe
        self.client = client
        self.memory_collection = memory_collection
        self.module_collection = module_collection
        self.dimension = dimension
        self.local_path = local_path
        self.is_local = is_local

    # ------------------------------------------------------------ factories
    @classmethod
    def from_settings(cls, s) -> "QdrantStore":
        try:
            if s.qdrant_mode == "server":
                client = QdrantClient(url=s.qdrant_url)
                local = False
                path = None
            elif s.qdrant_mode == "memory":
                client, local, path = QdrantClient(":memory:"), True, None
            else:
                Path(s.qdrant_local_path).mkdir(parents=True, exist_ok=True)
                client, local, path = QdrantClient(path=s.qdrant_local_path), True, s.qdrant_local_path
        except Exception as exc:  # local mode raises RuntimeError when the folder is locked by another process
            raise DependencyUnavailable(f"Qdrant could not be opened: {exc}", code="qdrant_unavailable") from exc
        store = cls(client, s.qdrant_memory_collection, s.qdrant_module_collection, s.embedding_dimension, path, local,
                    getattr(s, "tie_safe_search", True))
        store.ensure_collections()
        return store

    @classmethod
    def in_memory(cls, dimension: int, memory_collection: str = "m", module_collection: str = "p",
                  tie_safe: bool = True) -> "QdrantStore":
        store = cls(QdrantClient(":memory:"), memory_collection, module_collection, dimension, None, True, tie_safe)
        store.ensure_collections()
        return store

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # ----------------------------------------------------------- collections
    def ensure_collections(self) -> None:
        for name in (self.memory_collection, self.module_collection):
            if not self.client.collection_exists(name):
                self.client.create_collection(
                    name, vectors_config=models.VectorParams(size=self.dimension, distance=models.Distance.COSINE)
                )
        if not self.is_local:  # payload indexes have no effect in local mode
            for field in ("module_id", "status", "source_id"):
                self.client.create_payload_index(self.memory_collection, field, models.PayloadSchemaType.KEYWORD)
        info = self.client.get_collection(self.memory_collection)
        size = info.config.params.vectors.size  # type: ignore[union-attr]
        if size != self.dimension:
            raise DependencyUnavailable(
                f"Qdrant collection '{self.memory_collection}' has dimension {size} but the configured "
                f"embedding dimension is {self.dimension}. Change the collection name or reset the data.",
                code="dimension_mismatch",
            )

    def reset(self) -> None:
        for name in (self.memory_collection, self.module_collection):
            if self.client.collection_exists(name):
                self.client.delete_collection(name)
        self.ensure_collections()

    def health(self) -> dict[str, Any]:
        self.client.get_collections()
        return {"memories": self.count_memories(None), "modules": self.count_modules()}

    # --------------------------------------------------------------- filters
    @staticmethod
    def _filter(module_ids: Sequence[str] | None = None, status: str | None = ACTIVE,
                source_id: str | None = None) -> models.Filter | None:
        must: list[models.Condition] = []
        if status:
            must.append(models.FieldCondition(key="status", match=models.MatchValue(value=status)))
        if module_ids is not None:
            must.append(models.FieldCondition(key="module_id", match=models.MatchAny(any=list(module_ids))))
        if source_id:
            must.append(models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)))
        return models.Filter(must=must) if must else None

    # -------------------------------------------------------------- memories
    def upsert_memories(self, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]], batch: int = 256) -> None:
        """items: (memory_id, vector, payload). Batched."""
        for i in range(0, len(items), batch):
            chunk = items[i:i + batch]
            self.client.upsert(
                self.memory_collection,
                points=[models.PointStruct(id=point_id(mid), vector=list(map(float, vec)), payload=pl)
                        for mid, vec, pl in chunk],
                wait=True,
            )

    def search_memories(self, vector: Sequence[float], module_ids: Sequence[str] | None, limit: int,
                        status: str | None = ACTIVE) -> list[Hit]:
        if module_ids is not None and len(module_ids) == 0:
            return []
        flt, q = self._filter(module_ids, status), list(map(float, vector))
        points = self._tie_safe(lambda n: self.client.query_points(self.memory_collection, query=q, limit=n, query_filter=flt,
                                                                   with_payload=True).points, limit, "memory_id")
        return [Hit(p.payload["memory_id"], p.payload.get("module_id", ""), float(p.score), p.payload) for p in points]

    def _tie_safe(self, run, limit: int, id_key: str, cap: int = 20_000) -> list[Any]:
        """Top-`limit` points with a deterministic boundary.

        A vector store returns an arbitrary subset of a tie group (equal scores, e.g. duplicated documents) when the group
        straddles the limit; which members it picks depends on insertion order (verified for Qdrant local mode in
        docs/BEIR_PARITY_INVESTIGATION.md). We over-fetch until a strictly lower score proves the boundary group is complete,
        then order by (-score, id) and cut at `limit`. Costs one extra candidate in the common (no-tie) case."""
        if not self.tie_safe or limit <= 0:
            return list(run(limit))
        n = limit + 1
        while True:
            pts = list(run(n))
            if len(pts) < n or pts[-1].score < pts[limit - 1].score or n >= cap:
                break
            n = limit + 2 * (n - limit)  # the tie group may extend further: widen and retry
        pts.sort(key=lambda p: (-float(p.score), p.payload.get(id_key, "")))
        return pts[:limit]

    def count_memories(self, module_ids: Sequence[str] | None, status: str | None = ACTIVE) -> int:
        if module_ids is not None and len(module_ids) == 0:
            return 0
        return self.client.count(self.memory_collection, count_filter=self._filter(module_ids, status),
                                 exact=True).count

    def scroll_memories(self, module_id: str | None = None, status: str | None = ACTIVE, with_vectors: bool = False,
                        source_id: str | None = None, batch: int = 512
                        ) -> Iterator[tuple[str, dict[str, Any], np.ndarray | None]]:
        flt = self._filter([module_id] if module_id else None, status, source_id)
        offset = None
        while True:
            points, offset = self.client.scroll(
                self.memory_collection, scroll_filter=flt, limit=batch, offset=offset,
                with_payload=True, with_vectors=with_vectors,
            )
            for p in points:
                vec = np.asarray(p.vector, dtype=np.float32) if with_vectors and p.vector is not None else None
                yield p.payload["memory_id"], p.payload, vec
            if offset is None:
                break

    def get_memories(self, memory_ids: Sequence[str], with_vectors: bool = False
                     ) -> dict[str, tuple[dict[str, Any], np.ndarray | None]]:
        if not memory_ids:
            return {}
        out: dict[str, tuple[dict[str, Any], np.ndarray | None]] = {}
        ids = list(memory_ids)
        for i in range(0, len(ids), 512):
            pts = self.client.retrieve(self.memory_collection, ids=[point_id(m) for m in ids[i:i + 512]],
                                       with_payload=True, with_vectors=with_vectors)
            for p in pts:
                vec = np.asarray(p.vector, dtype=np.float32) if with_vectors and p.vector is not None else None
                out[p.payload["memory_id"]] = (p.payload, vec)
        return out

    def set_payload(self, memory_ids: Sequence[str], payload: dict[str, Any]) -> None:
        for i in range(0, len(memory_ids), 512):
            self.client.set_payload(self.memory_collection, payload=payload,
                                    points=[point_id(m) for m in memory_ids[i:i + 512]], wait=True)

    def set_module(self, memory_ids: Sequence[str], module_id: str) -> None:
        self.set_payload(memory_ids, {"module_id": module_id})

    def set_status(self, memory_ids: Sequence[str], status: str, extra: dict[str, Any] | None = None) -> None:
        self.set_payload(memory_ids, {"status": status, **(extra or {})})

    def delete_memories(self, memory_ids: Sequence[str]) -> None:
        for i in range(0, len(memory_ids), 512):
            self.client.delete(self.memory_collection,
                               points_selector=models.PointIdsList(points=[point_id(m) for m in memory_ids[i:i + 512]]),
                               wait=True)

    # --------------------------------------------------------------- modules
    def upsert_module_profiles(self, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]]) -> None:
        if not items:
            return
        self.client.upsert(
            self.module_collection,
            points=[models.PointStruct(id=point_id(mid), vector=list(map(float, vec)), payload={**pl, "module_id": mid})
                    for mid, vec, pl in items],
            wait=True,
        )

    def search_modules(self, vector: Sequence[float], limit: int) -> list[ModuleHit]:
        q = list(map(float, vector))
        points = self._tie_safe(lambda n: self.client.query_points(self.module_collection, query=q, limit=n, with_payload=True).points,
                                limit, "module_id")
        return [ModuleHit(p.payload["module_id"], float(p.score), p.payload) for p in points]

    def list_module_profiles(self, with_vectors: bool = True) -> dict[str, tuple[dict[str, Any], np.ndarray | None]]:
        out: dict[str, tuple[dict[str, Any], np.ndarray | None]] = {}
        offset = None
        while True:
            pts, offset = self.client.scroll(self.module_collection, limit=256, offset=offset,
                                             with_payload=True, with_vectors=with_vectors)
            for p in pts:
                vec = np.asarray(p.vector, dtype=np.float32) if with_vectors and p.vector is not None else None
                out[p.payload["module_id"]] = (p.payload, vec)
            if offset is None:
                break
        return out

    def count_modules(self) -> int:
        return self.client.count(self.module_collection, exact=True).count

    def delete_module_profiles(self, module_ids: Sequence[str]) -> None:
        if module_ids:
            self.client.delete(self.module_collection,
                               points_selector=models.PointIdsList(points=[point_id(m) for m in module_ids]), wait=True)

    # ---------------------------------------------------------------- misc
    def storage_size_bytes(self) -> int | None:
        if not self.local_path or not os.path.isdir(self.local_path):
            return None
        total = 0
        for root, _, files in os.walk(self.local_path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total
