"""Structural adaptation routes: status, candidates, evaluation, application and history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...common import NotFoundError
from ...container import AppContext
from ..deps import get_ctx, require_admin
from ..schemas.models import AdaptationRequest

router = APIRouter(prefix="/adaptation", tags=["adaptation"], dependencies=[Depends(require_admin)])


@router.get("/status")
def status(ctx: AppContext = Depends(get_ctx)):
    return ctx.adaptation.status()


@router.get("/candidates")
def candidates(ctx: AppContext = Depends(get_ctx)):
    """Detected structural weaknesses as explainable proposals (nothing is changed)."""
    return ctx.adaptation.candidates()


@router.get("/events")
def events(limit: int = Query(20, ge=1, le=200), offset: int = Query(0, ge=0),
           status: str | None = Query(None, pattern="^(running|proposed|completed|rejected|failed|rolled_back)$"),
           operation: str | None = None, ctx: AppContext = Depends(get_ctx)):
    q = {k: v for k, v in (("status", status), ("operation", operation)) if v}
    items, total = ctx.mongo.list_events("adaptation_events", "started_at", limit, offset, q)
    counts = {s: ctx.mongo.col("adaptation_events").count_documents({"status": s})
              for s in ("running", "proposed", "completed", "rejected", "failed", "rolled_back")}
    return {"items": items, "total": total, "limit": limit, "offset": offset, "status_counts": counts}


@router.get("/events/{event_id}")
def event(event_id: str, ctx: AppContext = Depends(get_ctx)):
    ev = ctx.events.get_adaptation(event_id)
    if not ev:
        raise NotFoundError(f"adaptation event {event_id} not found", code="event_not_found")
    return ev


@router.post("/evaluate")
def evaluate(body: AdaptationRequest, ctx: AppContext = Depends(get_ctx)):
    """Plan and evaluate a proposal on the labeled dataset WITHOUT committing it (status `proposed`/`rejected`)."""
    return ctx.adaptation.process(body.operation, body.module_ids, apply=False, event_id=body.operation_id)


@router.post("/apply", responses={409: {"description": "stale plan or another mutation is running"}})
def apply(body: AdaptationRequest, ctx: AppContext = Depends(get_ctx)):
    """Evaluate and, only if the acceptance policy passes, commit. Rejected candidates never change the architecture."""
    return ctx.adaptation.process(body.operation, body.module_ids, apply=True, override=body.validation_override,
                                  event_id=body.operation_id)


@router.post("/events/{event_id}/apply")
def apply_event(event_id: str, ctx: AppContext = Depends(get_ctx)):
    """Apply a previously evaluated (`proposed`) event; the plan is rebuilt and re-evaluated against current state."""
    ev = ctx.events.get_adaptation(event_id)
    if not ev:
        raise NotFoundError(f"adaptation event {event_id} not found", code="event_not_found")
    return ctx.adaptation.process(ev["operation"], ev["affected_modules"], apply=True)


@router.post("/run-window")
def run_window(ctx: AppContext = Depends(get_ctx)):
    """Close the current query window now and process detected weaknesses (what the 50-query schedule does)."""
    return ctx.adaptation.run_window("manual_window")


@router.post("/monitor")
def monitor(ctx: AppContext = Depends(get_ctx)):
    """Compare the active architecture with its parent on current data; roll back on material regression."""
    return ctx.adaptation.monitor_regression()
