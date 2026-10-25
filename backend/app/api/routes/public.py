"""Endpoints for ordinary visitors: query, own query history, and a sanitised read-only view of the module graph.

Everything else in the API is administrator-only (see `require_admin` on every other router).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ...auth.history import QueryHistory
from ...common import ValidationFailure
from ...container import AppContext
from ...memory.module_view import ModuleView
from ..deps import Actor, get_actor, get_ctx
from ..sanitize import public_graph, public_module, public_module_detail, public_query_response
from ..schemas.models import QueryRequest, QueryResponse

router = APIRouter()


def visitor(request: Request, response: Response) -> Actor:
    return get_actor(request, response)


@router.post("/query", response_model=QueryResponse, tags=["query"])
def query(body: QueryRequest, actor: Actor = Depends(visitor), ctx: AppContext = Depends(get_ctx)):
    """Graph-guided semantic retrieval on the real engine. Visitors get a sanitised response scoped to their own session;
    administrators get the complete diagnostic response (the stored visitor-history copy is always the sanitised one)."""
    if not actor.is_admin and body.top_k > ctx.settings.user_top_k_max:
        raise ValidationFailure(f"top_k must be at most {ctx.settings.user_top_k_max}", code="top_k_too_large")
    full = ctx.retrieval.query(body.query, body.top_k, body.debug_trace, body.mode, session_id=actor.sid, actor=actor.role)
    safe = public_query_response(full)
    QueryHistory(ctx.mongo, ctx.settings.user_history_limit).save(actor.sid, body.query.strip(), safe)
    return full if actor.is_admin else safe


@router.get("/me/queries", tags=["query"])
def my_queries(limit: int = Query(20, ge=1, le=50), actor: Actor = Depends(visitor), ctx: AppContext = Depends(get_ctx)):
    """The caller's own recent queries (never anyone else's)."""
    return {"items": QueryHistory(ctx.mongo, ctx.settings.user_history_limit).list(actor.sid, limit)}


@router.get("/me/queries/{query_id}", tags=["query"])
def my_query(query_id: str, actor: Actor = Depends(visitor), ctx: AppContext = Depends(get_ctx)):
    return QueryHistory(ctx.mongo, ctx.settings.user_history_limit).get(actor.sid, query_id)


# ------------------------------------------------------------------------------ read-only, sanitised graph for visitors
@router.get("/explore/graph", tags=["explore"])
def explore_graph(ctx: AppContext = Depends(get_ctx)):
    out = ctx.graph.export()
    out["counts"] = {"nodes": len(out["nodes"]), "edges": len(out["edges"])}
    return public_graph(out)


@router.get("/explore/modules", tags=["explore"])
def explore_modules(ctx: AppContext = Depends(get_ctx)):
    return {"items": [public_module(m) for m in ModuleView(ctx.mongo, ctx.graph).list()]}


@router.get("/explore/modules/{module_id}", tags=["explore"])
def explore_module(module_id: str, ctx: AppContext = Depends(get_ctx)):
    return public_module_detail(ModuleView(ctx.mongo, ctx.graph).detail(module_id, memory_limit=1))
