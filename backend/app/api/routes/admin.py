"""Development/demo-only routes (disabled in production or with ALLOW_ADMIN_ENDPOINTS=false)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...common import ValidationFailure
from ...container import AppContext
from ...demo import DemoService
from ..deps import get_ctx, require_admin, require_dev
from ..schemas.models import ResetRequest

router = APIRouter(prefix="/admin", tags=["admin (dev/demo)"], dependencies=[Depends(require_admin)])


@router.get("/demo/scenarios")
def scenarios(ctx: AppContext = Depends(get_ctx)):
    require_dev(ctx)
    return DemoService(ctx).scenarios()


@router.post("/reset")
def reset(body: ResetRequest, ctx: AppContext = Depends(get_ctx)):
    """Wipe MORPHE's own demo data (Qdrant collections + MongoDB database). Requires confirm="RESET"."""
    require_dev(ctx)
    if body.confirm != "RESET":
        raise ValidationFailure('confirm must equal "RESET"', code="confirmation_required")
    return DemoService(ctx).reset(body.reload_dataset)


@router.post("/demo/seed")
def seed(ctx: AppContext = Depends(get_ctx)):
    require_dev(ctx)
    return DemoService(ctx).seed()


@router.post("/demo/scenario/{name}")
def scenario(name: str, ctx: AppContext = Depends(get_ctx)):
    require_dev(ctx)
    return DemoService(ctx).scenario(name)


@router.get("/integrity")
def integrity(ctx: AppContext = Depends(get_ctx)):
    """Cross-store integrity check (Qdrant vs MongoDB vs live graph)."""
    return ctx.validator.check()
