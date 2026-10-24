from __future__ import annotations

from fastapi import Request

from ..common import MorpheError
from ..container import AppContext


def get_ctx(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise MorpheError("service is not ready", code="not_ready", status_code=503)
    return ctx


def require_dev(ctx: AppContext) -> None:
    if ctx.settings.is_production or not ctx.settings.allow_admin_endpoints:
        raise MorpheError("this development/demo endpoint is disabled", code="admin_disabled", status_code=403)
