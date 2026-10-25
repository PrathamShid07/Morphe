"""Personal memory: every user's private memories, modules and graph. All work happens inside one owner's scope.

The owner is always an argument (taken by the API layer from the verified session), every id minted here embeds an owner token, and the
storage adapters filter on the owner for every read and write -- see `storage/user_plane.py`.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Sequence

import networkx as nx
import numpy as np

from ..common import ConflictError, NotFoundError, ValidationFailure, get_logger, humanize_name, iso, log_event, sha256_hex, slugify
from ..graph.manager import graph_from_parts
from ..graph.persistence import edge_spec_from_doc, module_spec_from_doc
from ..ingestion.chunker import chunk_text, normalize_text
from ..storage.user_plane import PRIVATE, UserDataStore, UserVectorStore, owner_token
from ..versioning.state import CO_ACCESS, HIERARCHY, centroid, edge_id_for
from .models import ACTIVE, DELETED, PENDING, preview
from .modules import top_keywords

log = get_logger("memory.personal")
MAX_TITLE = 120


class PersonalMemoryService:
    def __init__(self, settings, embedder, vectors: UserVectorStore, data: UserDataStore):
        self.s = settings
        self.embedder = embedder
        self.vectors = vectors
        self.data = data
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # one writer per user; users never block each other

    # ------------------------------------------------------------------------------------------------------------------ ids
    @staticmethod
    def module_id_for(owner: str, name: str) -> str:
        return f"umod_{owner_token(owner)}_{slugify(name)}"

    # -------------------------------------------------------------------------------------------------------------------- save
    def save(self, owner: str, text: str, *, title: str | None = None, topic: str | None = None, group: str | None = None,
             tags: list[str] | None = None) -> dict[str, Any]:
        """Chunk, embed and store `text` as the owner's private memory. Atomic from the user's point of view: nothing from a failed save stays active."""
        title = (title or "").strip()[:MAX_TITLE] or None
        if not text or not text.strip():
            raise ValidationFailure("write something to remember", code="empty_text")
        if len(text) > self.s.user_memory_max_chars:
            raise ValidationFailure(f"a single memory can hold at most {self.s.user_memory_max_chars:,} characters", code="text_too_large", status_code=413)
        normalized = normalize_text(text)
        if not normalized:
            raise ValidationFailure("the text is empty after cleaning", code="empty_text")
        tok = owner_token(owner)
        digest = sha256_hex(owner + "\n" + normalized)
        source_id = f"usrc_{tok}_{digest[:12]}"
        existing = self.data.get_source(owner, source_id)
        if existing and existing.get("status") == "active":
            return {"source_id": source_id, "status": "duplicate", "chunks_created": 0, "memory_ids": [], "module_ids": [], "new_module_ids": [],
                    "tokens_added": 0, "message": "You already saved this exact text."}
        if self.data.stats(owner)["memories"] >= self.s.user_memory_max_records:
            raise ConflictError("your personal memory is full; delete some memories first", code="memory_quota_exceeded", status_code=409)
        chunks = chunk_text(normalized, self.embedder.count_tokens, self.s.chunk_max_tokens, self.s.chunk_overlap_tokens)
        if not chunks:
            raise ValidationFailure("no usable content after chunking", code="empty_text")
        vectors = self.embedder.encode([c.text for c in chunks])
        with self._locks[owner]:
            return self._write(owner, source_id, digest, title or humanize_name(chunks[0].text[:40]), title, topic, group, tags or [], normalized, chunks, vectors)

    def _assign(self, owner: str, chunks, vectors: np.ndarray, title: str | None, topic: str | None, group: str | None) -> tuple[list[str], list[str]]:
        created: list[str] = []
        parent_id = None
        if group and group.strip():
            gname = humanize_name(group.strip()[:MAX_TITLE])
            parent_id = self.module_id_for(owner, gname)
            if self._ensure_module(owner, parent_id, gname, None, f"Group: {gname}"):
                created.append(parent_id)

        def ensure(name: str) -> str:
            mid = self.module_id_for(owner, name)
            if self._ensure_module(owner, mid, name, parent_id, ""):
                created.append(mid)
            return mid

        if topic and topic.strip():
            mid = ensure(humanize_name(topic.strip()[:MAX_TITLE]))
            return [mid] * len(chunks), created
        assigned: list[str | None] = []
        for v in vectors:
            hits = self.vectors.search_modules(owner, v, 1)
            assigned.append(hits[0].module_id if hits and hits[0].score >= self.s.module_assign_threshold else None)
        if any(a is None for a in assigned):
            texts = [c.text for c, a in zip(chunks, assigned) if a is None]
            name = humanize_name(title) if title else " ".join(w.capitalize() for w in top_keywords(texts, 2)) or "General Notes"
            fallback = ensure(name)
            assigned = [a or fallback for a in assigned]
        return [str(a) for a in assigned], created

    def _ensure_module(self, owner: str, module_id: str, name: str, parent_id: str | None, description: str) -> bool:
        existing = self.data.get_module(owner, module_id)
        now = iso()
        if existing:
            if parent_id and not existing.get("parent_id") and parent_id != module_id:
                self.data.put_module(owner, {"module_id": module_id, "parent_id": parent_id, "updated_at": now})
                self._hierarchy_edge(owner, parent_id, module_id)
            return False
        self.data.put_module(owner, {"module_id": module_id, "name": name, "parent_id": parent_id, "description": description, "keywords": [],
                                     "created_at": now, "updated_at": now, "last_change_op": "created", "last_change_at": now, "status": "active",
                                     "active_record_count": 0, "profile_version": None})
        if parent_id:
            self._hierarchy_edge(owner, parent_id, module_id)
        return True

    def _hierarchy_edge(self, owner: str, parent: str, child: str) -> None:
        eid = edge_id_for(HIERARCHY, parent, child)
        self.data.put_edge(owner, {"edge_id": eid, "source": parent, "target": child, "relation_type": HIERARCHY, "weight": 1.0, "co_access_count": 0,
                                   "created_at": iso(), "operation_id": None, "evidence": {"kind": "parent_child"}})

    def _write(self, owner: str, source_id: str, digest: str, default_title: str, title: str | None, topic: str | None, group: str | None,
               tags: list[str], normalized: str, chunks, vectors: np.ndarray) -> dict[str, Any]:
        now = iso()
        source_name = title or default_title
        module_ids, created = self._assign(owner, chunks, vectors, title, topic, group)
        base = source_id.split("_", 2)[2]
        tok = owner_token(owner)
        ids = [f"umem_{tok}_{base}_{c.index:04d}" for c in chunks]
        touched = sorted(set(module_ids))
        payloads, docs = [], []
        for i, c in enumerate(chunks):
            payload = {"memory_id": ids[i], "owner_user_id": owner, "visibility": PRIVATE, "module_id": module_ids[i], "text": c.text, "source_id": source_id,
                       "source_name": source_name, "section": c.section, "chunk_index": c.index, "created_at": now, "updated_at": now,
                       "token_count": c.token_count, "status": PENDING, "importance": "normal", "pinned": False, "tags": tags,
                       "content_hash": sha256_hex(c.text), "kind": "chunk"}
            payloads.append(payload)
            doc = {k: v for k, v in payload.items() if k != "text"}
            doc["text_preview"] = preview(c.text)
            docs.append(doc)
        try:
            self.vectors.upsert_memories(owner, [(ids[i], vectors[i], payloads[i]) for i in range(len(ids))])
            self.data.put_memories(owner, docs)
            self.data.put_source(owner, {"source_id": source_id, "source_name": source_name, "content_hash": digest, "created_at": now, "status": "active",
                                         "chunk_count": len(ids), "token_count": sum(c.token_count for c in chunks), "tags": tags, "module_ids": touched})
            self.vectors.set_status(owner, ids, ACTIVE)  # only now do the chunks become retrievable
            self.data.set_memory_fields(owner, ids, {"status": ACTIVE, "updated_at": iso()})
            kw = [m for m in touched if m in created or not (self.data.get_module(owner, m) or {}).get("keywords")]
            self.refresh_profiles(owner, touched)
            for m in kw:
                self.data.put_module(owner, {"module_id": m, "keywords": top_keywords([c.text for c, mid in zip(chunks, module_ids) if mid == m])})
        except Exception:
            self._rollback(owner, source_id, ids, created, touched)
            raise
        log_event(log, "personal_memory_saved", owner=owner, chunks=len(ids), modules=len(touched))
        return {"source_id": source_id, "status": "completed", "chunks_created": len(ids), "memory_ids": ids, "module_ids": touched,
                "new_module_ids": created, "tokens_added": sum(c.token_count for c in chunks), "message": "Saved to your private memory."}

    def _rollback(self, owner: str, source_id: str, ids: list[str], created: list[str], touched: list[str]) -> None:
        for step in (lambda: self.vectors.delete_memories(owner, ids), lambda: self.data.delete_memory_docs(owner, ids),
                     lambda: self.data.delete_source(owner, source_id), lambda: self.refresh_profiles(owner, touched + created),
                     lambda: self._retire_empty(owner, touched + created)):
            try:
                step()
            except Exception as exc:  # keep undoing the remaining steps
                log_event(log, "personal_save_cleanup_failed", level=40, error_type=type(exc).__name__)

    # --------------------------------------------------------------------------------------------------------------- profiles
    def refresh_profiles(self, owner: str, module_ids: Sequence[str]) -> list[str]:
        """Recompute counts and routing profiles from the owner's active vectors; retire modules left empty (and childless)."""
        now = iso()
        for m in dict.fromkeys(module_ids):
            doc = self.data.get_module(owner, m)
            if not doc:
                continue
            vecs = [v for _, _, v in self.vectors.scroll_memories(owner, module_id=m, with_vectors=True)]
            upd: dict[str, Any] = {"module_id": m, "active_record_count": len(vecs), "updated_at": now}
            if not vecs:
                self.vectors.delete_module_profiles(owner, [m])
                upd["profile_version"] = None
            else:
                c = centroid(vecs)
                kws = doc.get("keywords", [])
                self.vectors.upsert_module_profiles(owner, [(m, c, {"display_name": doc.get("name", m), "description": doc.get("description") or ", ".join(kws),
                                                                    "parent_id": doc.get("parent_id"), "active_record_count": len(vecs), "updated_at": now})])
                upd["profile_version"] = sha256_hex(",".join(f"{x:.4f}" for x in c))[:10]
            self.data.put_module(owner, upd)
        return self._retire_empty(owner, list(dict.fromkeys(module_ids)))

    def _retire_empty(self, owner: str, candidates: Sequence[str]) -> list[str]:
        retired, pending = [], list(candidates)
        while pending:
            m = pending.pop()
            doc = self.data.get_module(owner, m)
            if not doc or doc.get("active_record_count", 0) > 0:
                continue
            if any(d.get("parent_id") == m for d in self.data.list_modules(owner)):
                continue
            now = iso()
            self.data.put_module(owner, {"module_id": m, "status": "retired", "retired_at": now, "updated_at": now})
            self.vectors.delete_module_profiles(owner, [m])
            self.data.delete_edges_of(owner, m)
            retired.append(m)
            if doc.get("parent_id"):
                pending.append(doc["parent_id"])
        return retired

    # ---------------------------------------------------------------------------------------------------------------- browsing
    def list_memories(self, owner: str, *, module_id: str | None = None, search: str | None = None, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        items, total = self.data.page_memories(owner, module_id=module_id, search=search, limit=limit, offset=offset)
        names = {m["module_id"]: m.get("name") for m in self.data.list_modules(owner, include_retired=True)}
        for it in items:
            it["module_name"] = names.get(it.get("module_id"))
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_memory(self, owner: str, memory_id: str) -> dict[str, Any]:
        meta = self.data.get_memory(owner, memory_id)  # 404 for 'absent' and for 'somebody else's': the filter is the authorization
        if not meta or meta.get("status") != ACTIVE:
            raise NotFoundError("memory not found", code="memory_not_found")
        payload = self.vectors.get_memories(owner, [memory_id]).get(memory_id, {})
        return {**meta, "text": payload.get("text")}

    def delete_memory(self, owner: str, memory_id: str) -> dict[str, Any]:
        with self._locks[owner]:
            meta = self.data.get_memory(owner, memory_id)
            if not meta or meta.get("status") != ACTIVE:
                raise NotFoundError("memory not found", code="memory_not_found")
            self.vectors.delete_memories(owner, [memory_id])
            self.data.set_memory_fields(owner, [memory_id], {"status": DELETED, "deleted_at": iso(), "text_preview": ""})
            retired = self.refresh_profiles(owner, [meta["module_id"]])
        return {"memory_id": memory_id, "status": "deleted", "module_id": meta["module_id"], "modules_retired": retired}

    def stats(self, owner: str) -> dict[str, Any]:
        return self.data.stats(owner)

    # ------------------------------------------------------------------------------------------------------------------ graph
    def graph(self, owner: str) -> nx.MultiGraph:
        """The owner's own module graph. Co-access shortcuts appear once enough of the owner's queries have touched both modules."""
        mods = {d["module_id"]: module_spec_from_doc(d, None) for d in self.data.list_modules(owner)}
        edges = [edge_spec_from_doc(d) for d in self.data.list_edges(owner)
                 if d["relation_type"] == HIERARCHY or int(d.get("co_access_count", 0)) >= self.s.user_coaccess_min_count]
        counts = {m: v["records"] for m, v in self.data.active_counts(owner).items()}
        g = graph_from_parts(mods, edges, counts)
        for n in g.nodes:
            g.nodes[n]["scope"] = "personal"
        return g

    def record_coaccess(self, owner: str, module_ids: Sequence[str]) -> int:
        """Count that these personal modules were retrieved together; returns the number of pairs updated."""
        mine = [m for m in dict.fromkeys(module_ids) if self.data.get_module(owner, m)][:6]
        n = 0
        for i, a in enumerate(mine):
            for b in mine[i + 1:]:
                eid = edge_id_for(CO_ACCESS, a, b)
                cur = self.data.get_edge(owner, eid) or {}
                count = int(cur.get("co_access_count", 0)) + 1
                self.data.put_edge(owner, {"edge_id": eid, "source": min(a, b), "target": max(a, b), "relation_type": CO_ACCESS, "co_access_count": count,
                                           "weight": round(min(1.0, count / 10), 3), "created_at": cur.get("created_at") or iso(), "operation_id": None,
                                           "evidence": {"kind": "co_retrieval", "queries": count}})
                n += 1
        return n
