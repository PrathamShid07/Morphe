"""Ingestion pipeline: validate -> normalize -> chunk -> embed -> assign modules -> stage -> activate -> refresh.

Memories are first written with status `pending` (invisible to retrieval) and only flipped to `active` after every
store has accepted them, so a failure never leaves partially active chunks behind.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ..common import ValidationFailure, get_logger, humanize_name, iso, log_event, sha256_hex, slugify
from ..coordinator import AlreadyCompleted, MutationCoordinator
from ..memory.budget_tracker import BudgetTracker
from ..memory.models import ACTIVE, IMPORTANCE_LEVELS, PENDING, MemoryRecord
from ..memory.modules import ModuleService, module_id_for, top_keywords
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from .chunker import chunk_text, normalize_text
from .embedder import Embedder

log = get_logger("ingestion")


class IngestionService:
    def __init__(self, settings, embedder: Embedder, qdrant: QdrantStore, mongo: MongoStore,
                 modules: ModuleService, coordinator: MutationCoordinator, budget: BudgetTracker,
                 after_mutation: Callable[[str], None] | None = None):
        self.s = settings
        self.embedder = embedder
        self.qdrant = qdrant
        self.mongo = mongo
        self.modules = modules
        self.coordinator = coordinator
        self.budget = budget
        self.after_mutation = after_mutation or (lambda trigger: None)

    # ------------------------------------------------------------ validation
    def _validate(self, source_name: str, text: str, importance: str) -> str:
        if not source_name or not source_name.strip():
            raise ValidationFailure("source_name must not be empty", code="empty_source_name")
        if len(source_name) > 200:
            raise ValidationFailure("source_name is longer than 200 characters")
        if not text or not text.strip():
            raise ValidationFailure("text must not be empty", code="empty_text")
        if len(text) > self.s.max_ingest_chars:
            raise ValidationFailure(f"text exceeds the {self.s.max_ingest_chars} character limit",
                                    code="text_too_large", status_code=413)
        if importance not in IMPORTANCE_LEVELS:
            raise ValidationFailure(f"importance must be one of {IMPORTANCE_LEVELS}")
        normalized = normalize_text(text)
        if not normalized:
            raise ValidationFailure("text is empty after normalization", code="empty_text")
        return normalized

    # ----------------------------------------------------------- assignment
    def _assign_modules(self, n_chunks: int, vectors: np.ndarray, source_name: str, metadata: dict[str, Any],
                        operation_id: str) -> tuple[list[str], list[str]]:
        """Returns (module_id per chunk, newly created module ids)."""
        created: list[str] = []
        parent_id = None
        parent_topic = metadata.get("parent_topic")
        if parent_topic:
            parent_id = module_id_for(str(parent_topic))
            _, was_new = self.modules.ensure_module(parent_id, humanize_name(str(parent_topic)),
                                                    description=f"Group: {parent_topic}", operation_id=operation_id)
            if was_new:
                created.append(parent_id)

        def ensure(name: str, mid: str) -> str:
            _, was_new = self.modules.ensure_module(mid, name, parent_id=parent_id, operation_id=operation_id)
            if was_new:
                created.append(mid)
            return mid

        topic = metadata.get("topic")
        if topic:
            mid = ensure(str(metadata.get("module_name") or humanize_name(str(topic))), module_id_for(str(topic)))
            return [mid] * n_chunks, created

        assigned: list[str | None] = []
        for v in vectors:
            hits = self.qdrant.search_modules(v, 1)
            assigned.append(hits[0].module_id if hits and hits[0].score >= self.s.module_assign_threshold else None)
        if any(a is None for a in assigned):
            stem = humanize_name(source_name)
            fallback = ensure(stem, module_id_for(stem))
            assigned = [a or fallback for a in assigned]
        return [str(a) for a in assigned], created

    # ---------------------------------------------------------------- ingest
    def ingest(self, source_name: str, text: str, metadata: dict[str, Any] | None = None, tags: list[str] | None = None,
               importance: str = "normal", pinned: bool = False, operation_id: str | None = None) -> dict[str, Any]:
        metadata = dict(metadata or {})
        normalized = self._validate(source_name, text, importance)
        content_hash = sha256_hex(normalized)
        source_id = f"src_{content_hash[:12]}"

        existing = self.mongo.col("sources").find_one({"source_id": source_id})
        if existing and self.mongo.col("memory_metadata").count_documents({"source_id": source_id, "status": ACTIVE}):
            usage = self.budget.usage()
            return {"source_id": source_id, "status": "duplicate", "chunks_created": 0, "module_ids": [],
                    "new_module_ids": [], "tokens_added": 0, "active_tokens": usage["active_tokens"],
                    "usage_percent": usage["usage_percent"], "operation_id": None,
                    "message": f"Identical content is already ingested as {existing.get('source_name')}.",
                    "compression": None}

        chunks = chunk_text(normalized, self.embedder.count_tokens, self.s.chunk_max_tokens, self.s.chunk_overlap_tokens)
        if not chunks:
            raise ValidationFailure("no usable content after chunking", code="empty_text")
        vectors = self.embedder.encode([c.text for c in chunks])  # validated: dimension + finite

        try:
            with self.coordinator.mutate("ingestion", operation_id, {"source_id": source_id}) as op:
                result = self._write(op["operation_id"], source_id, source_name, normalized, content_hash, chunks,
                                     vectors, metadata, tags or [], importance, pinned)
                op["result"] = {k: result[k] for k in ("source_id", "chunks_created", "tokens_added")}
        except AlreadyCompleted as done:
            usage = self.budget.usage()
            return {**(done.operation.get("result") or {}), "status": "completed", "operation_id": done.operation["operation_id"],
                    "module_ids": [], "new_module_ids": [], "active_tokens": usage["active_tokens"],
                    "usage_percent": usage["usage_percent"], "message": "operation already completed", "compression": None}
        result["compression"] = self._after(result)
        return result

    def _after(self, result: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self.after_mutation("ingestion")  # type: ignore[return-value]
        except Exception as exc:  # compression problems never fail a committed ingestion
            log_event(log, "after_mutation_failed", level=40, error_type=type(exc).__name__)
            return {"state": "failed", "error": str(exc)[:200]}

    def _write(self, operation_id: str, source_id: str, source_name: str, normalized: str, content_hash: str,
               chunks, vectors: np.ndarray, metadata: dict[str, Any], tags: list[str], importance: str,
               pinned: bool) -> dict[str, Any]:
        now = iso()
        module_ids, created = self._assign_modules(len(chunks), vectors, source_name, metadata, operation_id)
        base = source_id[4:]
        records = [
            MemoryRecord(memory_id=f"mem_{base}_{c.index:04d}", module_id=module_ids[i], text=c.text,
                         source_id=source_id, source_name=source_name, created_at=now, token_count=c.token_count,
                         content_hash=sha256_hex(c.text), section=c.section, chunk_index=c.index, status=PENDING,
                         importance=importance, pinned=pinned, tags=tags)
            for i, c in enumerate(chunks)]
        ids = [r.memory_id for r in records]
        touched = sorted(set(module_ids))
        try:
            self.qdrant.upsert_memories([(r.memory_id, vectors[i], r.qdrant_payload()) for i, r in enumerate(records)])
            self.mongo.upsert_memory_metadata([{**r.mongo_doc(), "access_count": 0, "last_accessed_at": None}
                                               for r in records])
            self.mongo.col("sources").update_one(
                {"source_id": source_id},
                {"$set": {"source_id": source_id, "source_name": source_name, "content_hash": content_hash,
                          "created_at": now, "metadata": metadata, "tags": tags, "chunk_count": len(records),
                          "token_count": sum(r.token_count for r in records), "status": "active",
                          "operation_id": operation_id, "module_ids": touched}}, upsert=True)
            # activation: only now do the chunks become retrievable
            self.qdrant.set_status(ids, ACTIVE)
            self.mongo.update_memory_metadata(ids, {"status": ACTIVE})
            self.mongo.set_state("tokenizer_name", self.embedder.tokenizer_name)
            kw_modules = [m for m in touched if m in created or not (self.modules.get(m) or {}).get("keywords")]
            self.modules.refresh_profiles(touched)
            for m in kw_modules:  # keywords only for new/unlabelled modules
                texts = [r.text for r in records if r.module_id == m]
                self.mongo.upsert_modules([{"module_id": m, "keywords": top_keywords(texts)}])
        except Exception:
            self._rollback_ingest(source_id, ids, created, touched)
            raise
        usage = self.budget.usage()
        log_event(log, "ingestion_completed", source_id=source_id, chunks=len(records), modules=len(touched),
                  tokens_added=sum(r.token_count for r in records))
        return {"source_id": source_id, "status": "completed", "chunks_created": len(records),
                "module_ids": touched, "new_module_ids": created, "memory_ids": ids,
                "tokens_added": sum(r.token_count for r in records), "active_tokens": usage["active_tokens"],
                "usage_percent": usage["usage_percent"], "operation_id": operation_id, "message": "ingested"}

    def _rollback_ingest(self, source_id: str, ids: list[str], created: list[str], touched: list[str]) -> None:
        """Undo a failed ingestion: nothing from it may remain active."""
        for step in (lambda: self.qdrant.delete_memories(ids),
                     lambda: self.mongo.col("memory_metadata").delete_many({"memory_id": {"$in": ids}, "status": PENDING}),
                     lambda: self.mongo.col("memory_metadata").delete_many({"memory_id": {"$in": ids}}),
                     lambda: self.mongo.col("sources").delete_one({"source_id": source_id}),
                     lambda: self.modules.refresh_profiles(touched + created)):
            try:
                step()
            except Exception as exc:
                log_event(log, "ingest_cleanup_step_failed", level=40, error_type=type(exc).__name__)
