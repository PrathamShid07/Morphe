"""Module lifecycle helpers: creation, routing-profile refresh, keyword extraction, empty-module retirement."""
from __future__ import annotations

from typing import Any, Sequence

import numpy as np

from ..common import get_logger, humanize_name, iso, log_event, sha256_hex, slugify
from ..graph.persistence import ArchitecturePersistence
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from ..versioning.state import HIERARCHY, centroid, edge_id_for

log = get_logger("memory.modules")


def top_keywords(texts: Sequence[str], n: int = 4) -> list[str]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    texts = [t for t in texts if t and t.strip()]
    if not texts:
        return []
    try:
        vec = TfidfVectorizer(stop_words="english", token_pattern=r"(?u)\b[A-Za-z][A-Za-z0-9_\-]{2,}\b",
                              sublinear_tf=True, max_features=2000)
        mat = vec.fit_transform(texts)
    except ValueError:  # empty vocabulary
        return []
    scores = np.asarray(mat.sum(axis=0)).ravel()
    terms = vec.get_feature_names_out()
    order = sorted(range(len(terms)), key=lambda i: (-scores[i], terms[i]))[:n]
    return [str(terms[i]) for i in order]


def module_id_for(name: str) -> str:
    return f"mod_{slugify(name)}"


class ModuleService:
    def __init__(self, qdrant: QdrantStore, mongo: MongoStore, persistence: ArchitecturePersistence):
        self.qdrant = qdrant
        self.mongo = mongo
        self.persistence = persistence

    def get(self, module_id: str) -> dict[str, Any] | None:
        return self.mongo.col("modules").find_one({"module_id": module_id, "status": "active"})

    def ensure_module(self, module_id: str, name: str, *, parent_id: str | None = None, description: str = "",
                      operation_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """Create the module (and its hierarchy edge) if it is not active yet. Returns (doc, created)."""
        existing = self.get(module_id)
        now = iso()
        if existing:
            if parent_id and not existing.get("parent_id") and parent_id != module_id:
                self.mongo.upsert_modules([{"module_id": module_id, "parent_id": parent_id, "updated_at": now}])
                self._ensure_hierarchy_edge(parent_id, module_id, operation_id)
                existing["parent_id"] = parent_id
            return existing, False
        doc = {"module_id": module_id, "name": name, "parent_id": parent_id, "description": description,
               "keywords": [], "created_at": now, "created_by_op": operation_id, "last_change_op": "created",
               "last_change_at": now, "status": "active", "retired_at": None, "merged_into": None,
               "active_record_count": 0, "updated_at": now, "profile_version": None}
        self.mongo.upsert_modules([doc])
        if parent_id:
            self._ensure_hierarchy_edge(parent_id, module_id, operation_id)
        return doc, True

    def _ensure_hierarchy_edge(self, parent_id: str, child_id: str, operation_id: str | None) -> None:
        eid = edge_id_for(HIERARCHY, parent_id, child_id)
        self.mongo.col("graph_edges").update_one(
            {"edge_id": eid},
            {"$set": {"edge_id": eid, "source": parent_id, "target": child_id, "relation_type": HIERARCHY,
                      "weight": 1.0, "co_access_count": 0, "created_at": iso(), "operation_id": operation_id,
                      "evidence": {"kind": "parent_child"}}}, upsert=True)

    def refresh_profiles(self, module_ids: Sequence[str], *, refresh_keywords: bool = False) -> list[str]:
        """Recompute counts and routing profiles from active member vectors; retire empty leaf modules.

        Returns the module IDs retired because they became empty."""
        now = iso()
        retired: list[str] = []
        for m in dict.fromkeys(module_ids):
            doc = self.mongo.col("modules").find_one({"module_id": m})
            if not doc or doc.get("status") != "active":
                continue
            vecs, texts = [], []
            for _, payload, vec in self.qdrant.scroll_memories(module_id=m, with_vectors=True):
                vecs.append(vec)
                if refresh_keywords:
                    texts.append(payload.get("text", ""))
            update: dict[str, Any] = {"module_id": m, "active_record_count": len(vecs), "updated_at": now}
            if refresh_keywords and texts:
                update["keywords"] = top_keywords(texts)
            if not vecs:
                self.qdrant.delete_module_profiles([m])
                update["profile_version"] = None
            else:
                c = centroid(vecs)
                kws = update.get("keywords", doc.get("keywords", []))
                description = doc.get("description") or ", ".join(kws)
                self.qdrant.upsert_module_profiles([(m, c, {
                    "display_name": doc.get("name", m), "description": description, "parent_id": doc.get("parent_id"),
                    "active_record_count": len(vecs), "updated_at": now})])
                update["profile_version"] = sha256_hex(",".join(f"{x:.4f}" for x in c))[:10]
            self.mongo.upsert_modules([update])
        retired = self._retire_empty(list(dict.fromkeys(module_ids)))
        self.persistence.reload_graph()
        return retired

    def _retire_empty(self, candidates: Sequence[str]) -> list[str]:
        """A module with no records and no children has no role in the architecture; retire it with its edges."""
        retired: list[str] = []
        pending = list(candidates)
        while pending:
            m = pending.pop()
            doc = self.mongo.col("modules").find_one({"module_id": m})
            if not doc or doc.get("status") != "active" or doc.get("active_record_count", 0) > 0:
                continue
            if self.mongo.col("modules").count_documents({"parent_id": m, "status": "active"}) > 0:
                continue
            now = iso()
            self.mongo.upsert_modules([{"module_id": m, "status": "retired", "retired_at": now,
                                        "retired_reason": "empty", "updated_at": now}])
            self.qdrant.delete_module_profiles([m])
            self.mongo.col("graph_edges").delete_many({"$or": [{"source": m}, {"target": m}]})
            retired.append(m)
            if doc.get("parent_id"):
                pending.append(doc["parent_id"])
            log_event(log, "module_retired_empty", module_id=m)
        return retired
