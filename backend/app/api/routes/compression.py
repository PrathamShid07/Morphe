"""Compression routes (adaptive memory compression is separate from structural adaptation)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...container import AppContext
from ..deps import get_ctx, require_admin, require_dev
from ..schemas.models import BudgetRequest, CompressionRunRequest

router = APIRouter(prefix="/compression", tags=["compression"], dependencies=[Depends(require_admin)])


@router.get("/status")
def status(ctx: AppContext = Depends(get_ctx)):
    return ctx.compression.status()


@router.get("/events")
def events(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
           scope: str | None = Query(None, pattern="^(job|operation)$"),
           status: str | None = Query(None, pattern="^(running|completed|failed|skipped|simulated)$"),
           ctx: AppContext = Depends(get_ctx)):
    items, total = ctx.compression.events_list(limit, offset, scope, status)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/run", responses={409: {"description": "another mutation or compression job is running"}})
def run(body: CompressionRunRequest, ctx: AppContext = Depends(get_ctx)):
    """Run (or simulate with `dry_run`) a compression job synchronously. Safe: originals stay active until verified."""
    return ctx.compression.run(body.mode, trigger="manual" if not body.dry_run else "manual_simulation",
                               dry_run=body.dry_run, operation_id=body.operation_id)


@router.put("/budget")
def set_budget(body: BudgetRequest, ctx: AppContext = Depends(get_ctx)):
    """Development/demo only: override the token budget (e.g. a low budget to demonstrate thresholds)."""
    require_dev(ctx)
    ctx.budget.set_override(body.budget_tokens)
    return ctx.compression.status()
