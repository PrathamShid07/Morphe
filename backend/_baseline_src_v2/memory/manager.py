"""Memory record lifecycle: browsing, inspection, pin/importance edits and user-requested deletion."""
from __future__ import annotations

from typing import Any

from ..common import ConflictError, NotFoundError, ValidationFailure, get_logger, iso, log_event
from ..coordinator import MutationCoordinator
from ..storage.mongo_store import MongoStore, clean
from ..storage.qdrant_store import QdrantStore
from .budget_tracker import BudgetTracker
from .models import ACTIVE, DELETED, IMPORTANCE_LEVELS, is_protected
from .modules import ModuleService

log = get_logger("memory.manager")


class MemoryManager:
    def __init__(self, qdrant: QdrantStore, mongo: MongoStore, modules: ModuleService,
                 coordinator: MutationCoordinator, budget: BudgetTracker):
        self.qdrant = qdrant
        self.mongo = mongo
        self.modules = modules
        self.coordinator = coordinator
        self.budget = budget

    # ----------------------------------------------------------------- reads
    def list_memories(self, *, module_id: str | None = None, source_id: str | None = None, status: str | None = ACTIVE,
                      pinned: bool | None = None, search: str | None = None, limit: int = 20, offset: int = 0
                      ) -> dict[str, Any]:
        q: dict[str, Any] = {}
        if module_id:
            q["module_id"] = module_id
        if source_id:
            q["source_id"] = source_id
        if status and status != "all":
            q["status"] = status
        if pinned is not None:
            q["pinned"] = pinned
        if search:
            import re
            q["text_preview"] = {"$regex": re.escape(search), "$options": "i"}
        items, total = self.mongo.page("memory_metadata", q, [("source_name", 1), ("chunk_index", 1), ("memory_id", 1)],
                                       limit, offset)
        names = {m["module_id"]: m.get("name") for m in self.mongo.list_modules(include_retired=True)}
        for it in items:
            it["module_name"] = names.get(it.get("module_id"))
        return {"items": items, "limit": limit, "offset": offset, "total": total}

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        meta = self.mongo.get_memory_metadata(memory_id)
        if not meta:
            raise NotFoundError(f"memory {memory_id} not found", code="memory_not_found")
        payload = self.qdrant.get_memories([memory_id]).get(memory_id, ({}, None))[0]
        module = self.mongo.col("modules").find_one({"module_id": meta.get("module_id")}) or {}
        sources = []
        for sid in meta.get("summary_of", []):
            sm = self.mongo.get_memory_metadata(sid)
            if sm:
                sources.append({"memory_id": sid, "source_name": sm.get("source_name"), "status": sm.get("status"),
                                "text_preview": sm.get("text_preview")})
        out = {**meta, "text": payload.get("text") if payload else None, "module_name": module.get("name"),
               "summary_sources": sources}
        out.pop("_id", None)
        return out

    # ---------------------------------------------------------------- writes
    def update_flags(self, memory_id: str, *, pinned: bool | None, importance: str | None) -> dict[str, Any]:
        if importance is not None and importance not in IMPORTANCE_LEVELS:
            raise ValidationFailure(f"importance must be one of {IMPORTANCE_LEVELS}")
        with self.coordinator.mutate("memory_update", meta={"memory_id": memory_id}):
            meta = self.mongo.get_memory_metadata(memory_id)
            if not meta or meta.get("status") != ACTIVE:
                raise NotFoundError(f"active memory {memory_id} not found", code="memory_not_found")
            fields: dict[str, Any] = {}
            if pinned is not None:
                fields["pinned"] = pinned
            if importance is not None:
                fields["importance"] = importance
            if fields:
                self.qdrant.set_payload([memory_id], fields)
                self.mongo.update_memory_metadata([memory_id], fields)
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str, *, force: bool = False) -> dict[str, Any]:
        with self.coordinator.mutate("memory_delete", meta={"memory_id": memory_id}) as op:
            meta = self.mongo.get_memory_metadata(memory_id)
            if not meta or meta.get("status") != ACTIVE:
                raise NotFoundError(f"active memory {memory_id} not found", code="memory_not_found")
            if is_protected(meta) and not force:
                raise ConflictError("this memory is pinned or critical; deletion needs force=true",
                                    code="protected_memory", status_code=409)
            tokens = int(meta.get("token_count", 0))
            module_id = meta["module_id"]
            self.qdrant.delete_memories([memory_id])
            self.mongo.update_memory_metadata(
                [memory_id], {"status": DELETED, "deleted_at": iso(), "text_preview": "", "pinned": False})
            retired = self.modules.refresh_profiles([module_id])
            op["result"] = {"memory_id": memory_id, "tokens": tokens}
        log_event(log, "memory_deleted", memory_id=memory_id, tokens=tokens)
        return {"memory_id": memory_id, "status": "deleted", "active_tokens_reclaimed": tokens,
                "module_id": module_id, "modules_retired": retired}
