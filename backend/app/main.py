"""FastAPI application factory.

Run with a SINGLE worker: the local persistent Qdrant store must only be opened by one process.
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.deps import SAFE_METHODS, get_ctx, require_admin, try_admin
from .api.routes import adaptation, admin, auth as auth_routes, benchmarks, compression, core, metrics, public, versions
from .auth.service import AuthService
from .common import (DependencyUnavailable, MorpheError, configure_logging, get_logger, log_event, request_id_var)
from .config import Settings, get_settings
from .benchmark.service import BenchmarkService
from .container import AppContext, build_context

log = get_logger("api")


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message,
                                                               "request_id": request_id_var.get(), "details": details}})


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if settings.is_production and "*" in settings.cors_origin_list:
        raise ValueError("CORS_ORIGINS must list explicit origins in production")
    state: dict[str, Any] = {"startup_error": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if ctx is not None:
            app.state.ctx = ctx
            app.state.benchmarks = BenchmarkService(ctx)
        else:
            try:
                app.state.ctx = build_context(settings)
                app.state.benchmarks = BenchmarkService(app.state.ctx)
            except DependencyUnavailable as exc:  # keep serving /health so the UI can explain what is wrong
                state["startup_error"] = {"code": exc.code, "message": exc.message}
                log_event(log, "startup_failed", level=40, code=exc.code)
                app.state.ctx = None
        yield
        if getattr(app.state, "benchmarks", None) is not None:
            app.state.benchmarks.close()
        if ctx is None and getattr(app.state, "ctx", None) is not None:
            app.state.ctx.close()

    app = FastAPI(title="MORPHE API", version="1.0.0", lifespan=lifespan,
                  description="Memory-Oriented Reconfigurable Plasticity and Hierarchical Evolution")
    # Session cookies need credentialed CORS, which browsers only honour with explicit origins (never "*").
    explicit = "*" not in settings.cors_origin_list
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=explicit,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"] if explicit else ["*"],
                       expose_headers=["X-Request-ID"])
    app.state.auth = AuthService(settings, lambda: getattr(getattr(app.state, "ctx", None), "mongo", None))

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        token = request_id_var.set(rid)
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_request_bytes:
                return _error(413, "request_too_large", f"request body exceeds {settings.max_request_bytes} bytes")
            origin = request.headers.get("origin")
            if origin and request.method not in SAFE_METHODS and origin not in settings.cors_origin_list \
                    and origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
                return _error(403, "origin_not_allowed", "cross-site state-changing requests are not accepted")  # defence in depth next to CSRF tokens
            t0 = time.perf_counter()
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            if not request.url.path.endswith(("/health", "/status")):
                log_event(log, "request", method=request.method, path=request.url.path, status=response.status_code,
                          duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(MorpheError)
    async def morphe_error(request: Request, exc: MorpheError):
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errs = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return _error(422, "invalid_request", "request validation failed", errs)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log_event(log, "unhandled_error", level=40, path=request.url.path, error_type=type(exc).__name__)
        return _error(500, "internal_error", "unexpected server error; see logs with this request_id")

    @app.get("/health", tags=["health"])
    def health(request: Request):
        """Readiness of the backend, MongoDB, Qdrant and the embedding model."""
        c: AppContext | None = getattr(request.app.state, "ctx", None)
        is_admin = try_admin(request) is not None
        if c is None:
            err = state["startup_error"] if is_admin else {"code": (state["startup_error"] or {}).get("code", "not_ready"),
                                                           "message": "The MORPHE service is starting or a dependency is unavailable."}
            return JSONResponse(status_code=503, content={"status": "unavailable", "ready": False, "error": err})
        checks: dict[str, Any] = {}
        ok = True
        for name, fn in (("mongodb", lambda: c.mongo.ping() and {"database": settings.mongodb_database}),
                         ("qdrant", c.qdrant.health),
                         ("embedding_model", lambda: {"model": c.embedder.model_name, "dimension": c.embedder.dimension})):
            try:
                checks[name] = {"ok": True, **(fn() or {})}
            except Exception as exc:
                ok = False
                checks[name] = {"ok": False, "error": type(exc).__name__}
        if not is_admin:  # visitors only learn whether the service is ready, not what it is built from
            return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "ready": ok})
        rec = c.mongo.get_state("startup_reconciliation") or {}
        needs = c.mongo.get_state("needs_recovery")
        body = {"status": "ok" if ok and not needs else "degraded", "ready": ok, "checks": checks,
                "architecture_version": c.versions.active_id(), "environment": settings.app_env,
                "startup_reconciliation": {"at": rec.get("at"), "actions": len(rec.get("actions", [])),
                                           "integrity_passed": (rec.get("validation") or {}).get("passed")},
                "needs_recovery": needs}
        return JSONResponse(status_code=200 if ok else 503, content=body)

    prefix = settings.api_prefix
    for r in (auth_routes.router, public.router, core.router, compression.router, adaptation.router, metrics.router,
              versions.router, benchmarks.router, admin.router):
        app.include_router(r, prefix=prefix)

    @app.get(f"{prefix}/integrity", tags=["health"], dependencies=[Depends(require_admin)])
    def integrity(request: Request):
        return get_ctx(request).validator.check()

    @app.get(f"{prefix}/config", tags=["health"], dependencies=[Depends(require_admin)])
    def public_config(request: Request):
        """Non-secret configuration the dashboard displays (thresholds, defaults)."""
        c = get_ctx(request)
        return {"environment": settings.app_env, "embedding_model": settings.embedding_model,
                "embedding_dimension": settings.embedding_dimension, "top_k_default": settings.top_k_default,
                "seed_module_count": settings.seed_module_count, "max_candidate_modules": settings.max_candidate_modules,
                "fallback_min_score": settings.fallback_min_score, "adaptation_query_batch": settings.adaptation_query_batch,
                "auto_adaptation": settings.auto_adaptation, "admin_endpoints": settings.allow_admin_endpoints and not
                settings.is_production, "acceptance_thresholds": c.policy.thresholds(),
                "compression": {"start_pct": settings.compression_start_pct, "emergency_pct": settings.emergency_cleanup_pct,
                                "cleanup_target_pct": settings.cleanup_target_pct}}

    return app


def _default_app() -> FastAPI:
    return create_app()


app = _default_app() if __name__ != "__main__" else None  # `uvicorn app.main:app`
