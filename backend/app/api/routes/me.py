"""The signed-in user's own space: save and browse private memories, ask questions, inspect the personal graph.

Every handler depends on `require_user`; the `user_id` it yields is the only owner these routes ever use. Request bodies cannot carry an owner
(`extra="forbid"`), and no route takes a user id in its path, so there is nothing to tamper with. Objects that exist but belong to someone else
answer exactly like objects that do not exist (404).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ...auth.history import QueryHistory
from ...auth.service import Identity
from ...common import MorpheError, NotFoundError, iso
from ...container import AppContext
from ...graph.manager import export_graph, neighbors_of
from ..deps import get_ctx, require_user

router = APIRouter(prefix="/me", tags=["me"])


class SaveMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an `owner_user_id` / `visibility` in the body is rejected: ownership is derived from the session
    text: str = Field(..., min_length=1, max_length=100_000)
    title: str | None = Field(None, max_length=120, description="Optional label shown as the source name")
    topic: str | None = Field(None, max_length=120, description="Optional module (topic) to file this under")
    group: str | None = Field(None, max_length=120, description="Optional parent group for the topic")
    tags: list[str] = Field(default_factory=list, max_length=10)


class MyQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=50)
    debug_trace: bool = True
    mode: Literal["graph", "global", "progressive", "soft"] = "progressive"


def _audit(ctx: AppContext, ident: Identity, action: str, *, status: str = "success", **kw: Any) -> None:
    ctx.audit.record(action, status=status, actor_user_id=ident.user_id, actor_role="user", actor_label=ident.email, **kw)


def _touch(ctx: AppContext, ident: Identity) -> None:
    ctx.mongo.col("users").update_one({"user_id": ident.user_id}, {"$set": {"last_activity_at": iso()}})


# ------------------------------------------------------------------------------------------------------------------ memories
@router.post("/memories", status_code=201)
def save_memory(body: SaveMemoryRequest, ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    """Explicitly save text as a PRIVATE memory of the signed-in user (chunked and embedded by the real ingestion pipeline)."""
    try:
        out = ctx.personal.save(ident.user_id, body.text, title=body.title, topic=body.topic, group=body.group, tags=body.tags)
    except MorpheError as exc:
        _audit(ctx, ident, "memory.created", status="failure", target_type="memory", metadata={"error_code": exc.code, "chars": len(body.text)})
        raise
    except Exception as exc:  # a storage failure: nothing stayed active (the service rolled back); tell the user honestly
        _audit(ctx, ident, "memory.created", status="failure", target_type="memory", metadata={"error_code": "storage_error", "error_type": type(exc).__name__})
        raise MorpheError("your memory could not be saved and nothing was stored; please try again", code="save_failed", status_code=500) from exc
    _touch(ctx, ident)
    _audit(ctx, ident, "memory.created", status="success", target_type="source", target_id=out["source_id"],
           metadata={"chunks": out["chunks_created"], "modules": len(out["module_ids"]), "new_modules": len(out["new_module_ids"]), "tokens": out["tokens_added"],
                     "duplicate": out["status"] == "duplicate"})
    return out


@router.get("/memories")
def list_memories(module_id: str | None = None, search: str | None = Query(None, max_length=200), limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0), ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    return ctx.personal.list_memories(ident.user_id, module_id=module_id, search=search, limit=limit, offset=offset)


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    try:
        out = ctx.personal.delete_memory(ident.user_id, memory_id)
    except MorpheError as exc:
        _audit(ctx, ident, "memory.deleted", status="failure", target_type="memory", target_id=memory_id, metadata={"error_code": exc.code})
        raise
    _audit(ctx, ident, "memory.deleted", target_type="memory", target_id=memory_id, metadata={"module_id": out["module_id"]})
    return out


@router.get("/summary")
def summary(ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    """Counts for the signed-in user's empty states and account menu."""
    scope = ctx.personal_retrieval.scope(ident.user_id)
    return {**ctx.personal.stats(ident.user_id), "shared_records": scope.shared_records}


# ---------------------------------------------------------------------------------------------------------------------- graph
def _module_rows(g, counts: dict[str, int]) -> list[dict[str, Any]]:
    kids: dict[str | None, list[str]] = {}
    for m, d in g.nodes(data=True):
        kids.setdefault(d.get("parent_id"), []).append(m)
    rows = []
    for m, d in sorted(g.nodes(data=True)):
        n = d.get("active_record_count", 0)
        rows.append({"module_id": m, "name": d.get("name", m), "parent_id": d.get("parent_id") if d.get("parent_id") in g else None,
                     "children": sorted(kids.get(m, [])), "record_count": n, "is_container": n == 0, "keywords": d.get("keywords", []),
                     "description": d.get("description", ""), "scope": d.get("scope"),
                     "shortcut_edges": sum(1 for x in neighbors_of(g, m) if x["relation_type"] == "co_access")})
    return rows


@router.get("/graph")
def my_graph(ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    """The user's own module graph plus the shared modules that hold published records. Read-only; nothing here can reach another user's modules."""
    scope = ctx.personal_retrieval.scope(ident.user_id)
    out = export_graph(scope.graph)
    for n in out["nodes"]:
        n["data"].update(lastChangeOp=None, lastChangeAt=None, utilization=None)
    for e in out["edges"]:
        e["data"].update(createdAt=None, evidence={}, operationId=None)
    _audit(ctx, ident, "graph.viewed", target_type="graph", metadata={"nodes": len(out["nodes"]), "edges": len(out["edges"])})
    return {**out, "architecture_version": None, "counts": {"nodes": len(out["nodes"]), "edges": len(out["edges"])},
            "memory_scope": {"personal_records": scope.personal_records, "shared_records": scope.shared_records,
                             "empty": scope.personal_records + scope.shared_records == 0}}


@router.get("/modules")
def my_modules(ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    scope = ctx.personal_retrieval.scope(ident.user_id)
    return {"items": _module_rows(scope.graph, {})}


@router.get("/modules/{module_id}")
def my_module(module_id: str, ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    g = ctx.personal_retrieval.scope(ident.user_id).graph
    if module_id not in g:  # absent and out-of-scope look identical
        raise NotFoundError("module not found", code="module_not_found")
    rows = {r["module_id"]: r for r in _module_rows(g, {})}
    base = rows[module_id]
    neighbors = [{**n, "name": rows[n["module_id"]]["name"], "record_count": rows[n["module_id"]]["record_count"]}
                 for n in neighbors_of(g, module_id) if n["module_id"] in rows]
    return {**base, "parent": rows.get(base["parent_id"]) if base["parent_id"] else None, "neighbors": neighbors}


# ---------------------------------------------------------------------------------------------------------------------- query
@router.post("/query")
def my_query(body: MyQueryRequest, ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    """Retrieval inside the caller's own scope only (their private memories + published shared knowledge), on all four strategies."""
    meta = {"strategy": body.mode, "top_k": body.top_k, "query": body.query.strip()[:ctx.settings.audit_query_text_chars]}
    _audit(ctx, ident, "query.submitted", target_type="query", metadata=meta)
    try:
        resp = ctx.personal_retrieval.query(ident.user_id, body.query, body.top_k, body.debug_trace, body.mode)
    except MorpheError as exc:
        _audit(ctx, ident, "query.completed", status="failure", target_type="query", metadata={**meta, "error_code": exc.code})
        raise
    except Exception as exc:
        _audit(ctx, ident, "query.completed", status="failure", target_type="query", metadata={**meta, "error_code": "internal_error", "error_type": type(exc).__name__})
        raise MorpheError("the question could not be answered right now; please try again", code="query_failed", status_code=500) from exc
    QueryHistory(ctx.mongo, ctx.settings.user_history_limit).save(ident.user_id, body.query.strip(), resp)
    _touch(ctx, ident)
    _audit(ctx, ident, "query.completed", target_type="query", target_id=resp["query_id"],
           metadata={**meta, "result_count": len(resp["results"]), "latency_ms": resp["latency_ms"], "fallback_used": resp["fallback_used"],
                     "personal_results": sum(1 for r in resp["results"] if r["scope"] == "personal"),
                     "shared_results": sum(1 for r in resp["results"] if r["scope"] == "shared")})
    return resp


@router.get("/queries")
def my_queries(limit: int = Query(20, ge=1, le=50), ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    return {"items": QueryHistory(ctx.mongo, ctx.settings.user_history_limit).list(ident.user_id, limit)}


@router.get("/queries/{query_id}")
def my_query_by_id(query_id: str, ident: Identity = Depends(require_user), ctx: AppContext = Depends(get_ctx)):
    return QueryHistory(ctx.mongo, ctx.settings.user_history_limit).get(ident.user_id, query_id)
