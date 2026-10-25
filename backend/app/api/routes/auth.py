"""Administrator sign-in, session inspection and sign-out. There is deliberately no registration endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ...common import MorpheError
from ...auth.service import ADMIN_COOKIE
from ..deps import CSRF_HEADER, Actor, clear_cookie, get_actor, get_auth, set_session_cookie, try_admin
from ...auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a client-supplied "role" (or anything else) is rejected, never honoured
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


def _state(auth: AuthService, actor: Actor) -> dict[str, Any]:
    if actor.admin:
        return {"authenticated": True, "role": "admin", "username": actor.admin.username, "expires_at": actor.admin.expires_at,
                "csrf_token": auth.csrf_token(actor.admin.sid), "admin_configured": auth.admin_configured}
    return {"authenticated": False, "role": "user", "username": None, "expires_at": None, "csrf_token": None,
            "admin_configured": auth.admin_configured}


@router.post("/login", responses={401: {"description": "invalid credentials"}, 429: {"description": "too many attempts"},
                                  503: {"description": "administrator sign-in is not configured"}})
def login(body: LoginRequest, request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    token, ident = auth.login(body.username, body.password, request.client.host if request.client else "")
    set_session_cookie(response, auth, ADMIN_COOKIE, token, auth.admin_ttl)
    return {"authenticated": True, "role": "admin", "username": ident.username, "expires_at": ident.expires_at,
            "csrf_token": auth.csrf_token(ident.sid), "admin_configured": True}


@router.get("/session")
def session(request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    """Who the server believes the caller is. The SPA asks this on load and after any 401, and never decides roles itself."""
    return _state(auth, get_actor(request, response))


@router.post("/logout")
def logout(request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    """Revoke the administrator session server-side and clear the cookie. Idempotent for callers without a session."""
    ident = try_admin(request)
    if ident is not None and not auth.csrf_ok(ident.sid, request.headers.get(CSRF_HEADER)):
        raise MorpheError("missing or invalid CSRF token", code="csrf_failed", status_code=403)
    auth.logout(request.cookies.get(ADMIN_COOKIE))
    clear_cookie(response, auth, ADMIN_COOKIE)
    return {"authenticated": False, "role": "user", "username": None, "expires_at": None, "csrf_token": None,
            "admin_configured": auth.admin_configured}
