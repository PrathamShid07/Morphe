"""Architecture version routes: history, comparison, restore preview and restoration."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...container import AppContext
from ..deps import get_ctx, require_admin
from ..schemas.models import RestoreRequest

router = APIRouter(prefix="/versions", tags=["versions"], dependencies=[Depends(require_admin)])


@router.get("")
def list_versions(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  ctx: AppContext = Depends(get_ctx)):
    items, total = ctx.versions.list_versions(limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "active_version": ctx.versions.active_id()}


@router.get("/active")
def active(ctx: AppContext = Depends(get_ctx)):
    vid = ctx.versions.active_id()
    return ctx.versions.describe(ctx.versions.manifest(vid)) if vid else {"version_id": None}


@router.get("/compare")
def compare(from_version: str, to_version: str, evaluate: bool = True, ctx: AppContext = Depends(get_ctx)):
    return ctx.evaluation.compare_versions(from_version, to_version, evaluate)


@router.post("/checkpoint")
def checkpoint(ctx: AppContext = Depends(get_ctx)):
    """Save the current live architecture as a restorable version (no-op if it equals the active version)."""
    with ctx.coordinator.mutate("checkpoint"):
        m = ctx.lifecycle.ensure_checkpoint("manual checkpoint")
    return ctx.versions.describe(m)


@router.get("/{version_id}")
def get_version(version_id: str, ctx: AppContext = Depends(get_ctx)):
    m = ctx.versions.manifest(version_id)
    out = ctx.versions.describe(m)
    snap = ctx.versions.snapshot_state(version_id) if out["restorable"] else None
    out["modules"] = ([{"module_id": k, "name": v.name, "parent_id": v.parent_id, "records": snap.record_count(k)}
                       for k, v in snap.modules.items()] if snap else [])
    out["edges"] = ([{"edge_id": e.edge_id, "relation_type": e.relation_type, "source": e.source, "target": e.target,
                      "weight": e.weight, "co_access_count": e.co_access_count} for e in snap.edges.values()]
                    if snap else [])
    out["event"] = ctx.events.get_adaptation(m["operation_id"]) if m.get("operation_id") else None
    return out


@router.get("/{version_id}/evaluation")
def version_evaluation(version_id: str, ctx: AppContext = Depends(get_ctx)):
    m = ctx.versions.manifest(version_id)
    return {"version_id": version_id, "evaluation": m.get("evaluation"), "validation": m.get("validation")}


@router.get("/{version_id}/restore-preview")
def restore_preview(version_id: str, ctx: AppContext = Depends(get_ctx)):
    """What restoring this version would change (shown in the confirmation dialog)."""
    return ctx.lifecycle.preview_restore(version_id)


@router.post("/{version_id}/restore", responses={409: {"description": "another mutation is running"},
                                                  500: {"description": "restoration failed; previous state re-applied"}})
def restore(version_id: str, body: RestoreRequest | None = None, ctx: AppContext = Depends(get_ctx)):
    """Real cross-store rollback: Qdrant payloads + profiles, MongoDB documents, live graph, version pointer."""
    body = body or RestoreRequest()
    return ctx.lifecycle.restore(version_id, body.operation_id, body.reason, body.evaluate)
