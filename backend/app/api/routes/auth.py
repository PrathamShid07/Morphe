"""Account registration, user and administrator sign-in, session inspection and sign-out.

Public registration always creates a regular `user` account: the request model forbids unknown fields, so a client that sends
`role` (or any other privilege hint) gets a 422 and nothing is created. Administrators are configured out-of-band
(`backend/scripts/admin_credentials.py`); there is no endpoint that can create one.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ...accounts.users import normalize_email, public_user
from ...auth.service import ADMIN_COOKIE, USER_COOKIE, AuthService, Identity
from ...common import MorpheError, ValidationFailure
from ..deps import CSRF_HEADER, clear_cookie, current_identity, get_auth, set_session_cookie

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # `role`, `is_admin`, `user_id` ... are rejected, never honoured
    full_name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)
    confirm_password: str = Field(..., min_length=1, max_length=256)


class UserLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


def _audit(request: Request, action: str, **kw: Any) -> None:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is not None:
        ctx.audit.record(action, **kw)


def session_body(auth: AuthService, ident: Identity | None, user: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {"authenticated": False, "role": None, "user": None, "username": None, "expires_at": None, "csrf_token": None,
                            "admin_configured": auth.admin_configured, "registration_enabled": bool(auth.s.registration_enabled)}
    if ident is None:
        return base
    if ident.role == "admin":
        return {**base, "authenticated": True, "role": "admin", "username": ident.username, "expires_at": ident.expires_at,
                "csrf_token": auth.csrf_token(ident.sid)}
    doc = user or auth.users.by_id(ident.user_id or "")
    return {**base, "authenticated": True, "role": "user", "user": public_user(doc) if doc else None, "expires_at": ident.expires_at,
            "csrf_token": auth.csrf_token(ident.sid)}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/register", status_code=201, responses={409: {"description": "email already registered"}, 422: {"description": "validation failed"}})
def register(body: RegisterRequest, request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    mail = normalize_email(body.email)
    try:
        if body.password != body.confirm_password:
            raise ValidationFailure("the passwords do not match", code="password_mismatch")
        token, ident, doc = auth.register(body.full_name, body.email, body.password, _ip(request))
    except MorpheError as exc:
        _audit(request, "auth.register", status="failure", actor_role="user", actor_label=mail[:120], target_type="account",
               metadata={"error_code": exc.code, "email": mail[:120]})
        raise
    set_session_cookie(response, auth, USER_COOKIE, token, auth.user_ttl)
    clear_cookie(response, auth, ADMIN_COOKIE)  # a browser is one identity at a time
    _audit(request, "auth.registered", actor_user_id=doc["user_id"], actor_role="user", actor_label=doc["email"], target_type="account", target_id=doc["user_id"])
    return session_body(auth, ident, doc)


@router.post("/login", responses={401: {"description": "invalid credentials"}, 403: {"description": "account disabled"}, 429: {"description": "too many attempts"}})
def login(body: UserLoginRequest, request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    """Sign in a regular user account (email + password)."""
    mail = normalize_email(body.email)
    try:
        token, ident, doc = auth.user_login(body.email, body.password, _ip(request))
    except MorpheError as exc:
        known = auth.users.by_email(mail)
        _audit(request, "auth.login", status="denied" if exc.code == "account_disabled" else "failure", actor_role="user",
               subject_user_id=known["user_id"] if known else None, actor_label=mail[:120], target_type="account",
               metadata={"error_code": exc.code, "email": mail[:120]})
        raise
    set_session_cookie(response, auth, USER_COOKIE, token, auth.user_ttl)
    clear_cookie(response, auth, ADMIN_COOKIE)
    _audit(request, "auth.login", actor_user_id=doc["user_id"], actor_role="user", actor_label=doc["email"], target_type="account", target_id=doc["user_id"])
    return session_body(auth, ident, doc)


@router.post("/admin/login", responses={401: {"description": "invalid credentials"}, 429: {"description": "too many attempts"},
                                        503: {"description": "administrator sign-in is not configured"}})
def admin_login(body: AdminLoginRequest, request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    try:
        token, ident = auth.login(body.username, body.password, _ip(request))
    except MorpheError as exc:
        _audit(request, "admin.login", status="failure", actor_role="admin", actor_label=body.username[:80], target_type="admin_session",
               metadata={"error_code": exc.code})
        raise
    set_session_cookie(response, auth, ADMIN_COOKIE, token, auth.admin_ttl)
    clear_cookie(response, auth, USER_COOKIE)
    _audit(request, "admin.login", actor_role="admin", actor_label=ident.username, target_type="admin_session", target_id=ident.sid)
    return session_body(auth, ident)


def _identity_or_none(request: Request) -> Identity | None:
    try:
        return current_identity(request)
    except MorpheError:  # a disabled account is simply "nobody" here
        return None


@router.get("/me")
def me(request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    """Who the server believes the caller is. The SPA asks this on load (and after any 401) and never decides roles itself.
    A disabled account is reported as signed out, with its cookie cleared."""
    try:
        ident = current_identity(request)
    except MorpheError:
        clear_cookie(response, auth, USER_COOKIE)
        return {**session_body(auth, None), "account_disabled": True}
    return session_body(auth, ident)


@router.post("/logout")
def logout(request: Request, response: Response, auth: AuthService = Depends(get_auth)):
    """Revoke the caller's session server-side (whichever kind) and clear its cookie. Idempotent for callers without a session."""
    ident = _identity_or_none(request)
    if ident is not None and not auth.csrf_ok(ident.sid, request.headers.get(CSRF_HEADER)):
        raise MorpheError("missing or invalid CSRF token", code="csrf_failed", status_code=403)
    ctx = getattr(request.app.state, "ctx", None)
    if request.cookies.get(ADMIN_COOKIE) and auth.logout(request.cookies.get(ADMIN_COOKIE)) and ctx is not None:
        ctx.audit.record("admin.logout", actor_role="admin", actor_label=ident.username if ident and ident.role == "admin" else None, target_type="admin_session")
    if request.cookies.get(USER_COOKIE):
        u = auth.logout_user(request.cookies.get(USER_COOKIE))
        if u and u.user_id and ctx is not None:
            ctx.audit.record("auth.logout", actor_user_id=u.user_id, actor_role="user", target_type="account", target_id=u.user_id)
    clear_cookie(response, auth, ADMIN_COOKIE)
    clear_cookie(response, auth, USER_COOKIE)
    return session_body(auth, None)
