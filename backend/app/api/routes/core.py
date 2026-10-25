"""Core routes: ingestion, query, memories, modules, graph."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse

from ...common import ValidationFailure
from ...container import AppContext
from ...memory.module_view import ModuleView
from ..deps import get_ctx, require_admin
from ..schemas.models import IngestionRequest, IngestionResponse, MemoryPatch, QueryRequest, QueryResponse, VisibilityRequest

router = APIRouter(dependencies=[Depends(require_admin)])  # every route here is administrator-only (the visitor-facing routes are in public.py)


@router.post("/ingestion", response_model=IngestionResponse, tags=["ingestion"],
             responses={422: {"description": "empty/oversized/invalid input"}, 503: {"description": "dependency not ready"}})
def ingest(body: IngestionRequest, ctx: AppContext = Depends(get_ctx)):
    """Chunk, embed and store text. Identical content that is already active returns `status: duplicate`."""
    return ctx.ingestion.ingest(body.source_name, body.text, body.metadata, body.tags, body.importance, body.pinned,
                                body.operation_id, body.visibility)


@router.post("/ingestion/file", response_model=IngestionResponse, tags=["ingestion"])
async def ingest_file(file: UploadFile = File(...), topic: str | None = Form(None), parent_topic: str | None = Form(None),
                      visibility: str = Form("restricted"), ctx: AppContext = Depends(get_ctx)):
    """Ingest a UTF-8 text/markdown/code file (.txt, .md, .rst, .py, .json, ...)."""
    raw = await file.read(ctx.settings.max_ingest_chars * 4 + 1)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationFailure("only UTF-8 text files are supported", code="unsupported_file")
    meta: dict[str, Any] = {"filename": file.filename}
    if topic:
        meta["topic"] = topic
    if parent_topic:
        meta["parent_topic"] = parent_topic
    import anyio
    return await anyio.to_thread.run_sync(ctx.ingestion.ingest, file.filename or "upload.txt", text, meta, [], "normal",
                                          False, None, visibility)


@router.post("/query", response_model=QueryResponse, tags=["query"])
def query(body: QueryRequest, ctx: AppContext = Depends(get_ctx)):
    """Administrator playground on the shared architecture (complete diagnostic response). Ordinary users use POST /me/query, which is scoped to
    their own memories; this route never reads the personal plane."""
    return ctx.retrieval.query(body.query, body.top_k, body.debug_trace, body.mode)


@router.post("/sources/{source_id}/visibility", tags=["memories"])
def set_source_visibility(source_id: str, body: VisibilityRequest, ctx: AppContext = Depends(get_ctx)):
    """Publish a source to all users (`shared`) or withdraw it (`restricted`)."""
    return ctx.memories.set_source_visibility(source_id, body.visibility)


@router.get("/memories", tags=["memories"])
def list_memories(module_id: str | None = None, source_id: str | None = None,
                  status: str = Query("active", pattern="^(active|superseded|deleted|all)$"),
                  pinned: bool | None = None, search: str | None = Query(None, max_length=100),
                  limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  ctx: AppContext = Depends(get_ctx)):
    return ctx.memories.list_memories(module_id=module_id, source_id=source_id, status=status, pinned=pinned,
                                      search=search, limit=limit, offset=offset)


@router.get("/memories/{memory_id}", tags=["memories"])
def get_memory(memory_id: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.memories.get_memory(memory_id)


@router.patch("/memories/{memory_id}", tags=["memories"])
def patch_memory(memory_id: str, body: MemoryPatch, ctx: AppContext = Depends(get_ctx)):
    """Pin/unpin or change importance. Pinned and critical memories are never deleted automatically."""
    return ctx.memories.update_flags(memory_id, pinned=body.pinned, importance=body.importance)


@router.delete("/memories/{memory_id}", tags=["memories"],
               responses={409: {"description": "memory is pinned/critical; pass force=true"}})
def delete_memory(memory_id: str, force: bool = False, ctx: AppContext = Depends(get_ctx)):
    return ctx.memories.delete_memory(memory_id, force=force)


@router.get("/sources", tags=["memories"])
def list_sources(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 ctx: AppContext = Depends(get_ctx)):
    items, total = ctx.mongo.page("sources", {"status": "active"}, [("created_at", -1)], limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _capacity(ctx: AppContext, record_count: int) -> dict[str, Any]:
    """Utilisation against the split detector's capacity threshold (the point at which a module counts as overloaded)."""
    cap = int(ctx.settings.split_capacity_threshold)
    return {"capacity_threshold": cap, "utilization": round(record_count / cap, 4) if cap else None}


@router.get("/modules", tags=["graph"])
def list_modules(ctx: AppContext = Depends(get_ctx)):
    items = ModuleView(ctx.mongo, ctx.graph).list()
    return {"items": [{**m, **_capacity(ctx, m["record_count"])} for m in items]}


@router.get("/modules/{module_id}", tags=["graph"])
def module_detail(module_id: str, ctx: AppContext = Depends(get_ctx)):
    d = ModuleView(ctx.mongo, ctx.graph).detail(module_id)
    return {**d, **_capacity(ctx, d["record_count"]), "architecture_version": ctx.versions.active_id()}


@router.get("/graph", tags=["graph"])
def graph(ctx: AppContext = Depends(get_ctx)):
    """React Flow-ready nodes (modules) and typed edges (hierarchy / co_access) of the live architecture."""
    out = ctx.graph.export()
    cap = int(ctx.settings.split_capacity_threshold)
    for n in out["nodes"]:  # utilisation of real record counts against the real capacity threshold
        n["data"]["utilization"] = round(n["data"]["recordCount"] / cap, 4) if cap and not n["data"]["isContainer"] else None
    out["capacity_threshold"] = cap
    out["architecture_version"] = ctx.versions.active_id()
    out["counts"] = {"nodes": len(out["nodes"]), "edges": len(out["edges"])}
    return out
