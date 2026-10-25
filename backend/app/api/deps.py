from __future__ import annotations

from fastapi import Request, Response

from ..auth.service import ADMIN_COOKIE, USER_COOKIE, AuthError, AuthService, Identity
from ..common import MorpheError
from ..container import AppContext

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


def get_ctx(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise MorpheError("service is not ready", code="not_ready", status_code=503)
    return ctx


def require_dev(ctx: AppContext) -> None:
    if ctx.settings.is_production or not ctx.settings.allow_admin_endpoints:
        raise MorpheError("this development/demo endpoint is disabled", code="admin_disabled", status_code=403)


# ------------------------------------------------------------------------------------------------------------------- auth
def get_auth(request: Request) -> AuthService:
    return request.app.state.auth


def set_session_cookie(response: Response, auth: AuthService, name: str, value: str, max_age: float) -> None:
    # Admin cookie: SameSite=Strict (never sent on cross-site requests). User cookie: Lax (so a link from elsewhere still opens signed in).
    # Both HttpOnly; Secure outside plain-HTTP development. Cookie-authenticated writes also need the CSRF header.
    response.set_cookie(name, value, max_age=int(max_age), httponly=True, secure=auth.cookie_secure,
                        samesite="strict" if name == ADMIN_COOKIE else "lax", path="/")


def clear_cookie(response: Response, auth: AuthService, name: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=auth.cookie_secure,
                           samesite="strict" if name == ADMIN_COOKIE else "lax")


def _check_csrf(request: Request, auth: AuthService, sid: str) -> None:
    if request.method not in SAFE_METHODS and not auth.csrf_ok(sid, request.headers.get(CSRF_HEADER)):
        raise MorpheError("missing or invalid CSRF token", code="csrf_failed", status_code=403)


def try_admin(request: Request) -> Identity | None:
    """Admin identity if the request carries a valid administrator session; never raises (used for role-aware responses)."""
    try:
        token = request.cookies.get(ADMIN_COOKIE)
        return get_auth(request).verify_admin(token) if token else None
    except MorpheError:
        return None


def require_admin(request: Request) -> Identity:
    """Authorization gate for every administrative route. The role comes only from a verified, unexpired, unrevoked server session.

    401: no valid session at all, or the administrator session has expired / been revoked / has a bad signature.
    403: a valid ordinary-user session without administrator rights, or a state-changing request without a valid CSRF token.
    """
    auth = get_auth(request)
    token = request.cookies.get(ADMIN_COOKIE)
    ident = auth.verify_admin(token) if token else None
    if ident is None:
        if token:
            raise AuthError("your administrator session has expired or is invalid; sign in again", code="session_expired")
        user_token = request.cookies.get(USER_COOKIE)
        if user_token and auth.verify_user(user_token):
            raise MorpheError("administrator privileges are required for this operation", code="admin_required", status_code=403)
        raise AuthError("authentication is required")
    _check_csrf(request, auth, ident.sid)
    request.state.identity = ident
    return ident


def require_user(request: Request) -> Identity:
    """Gate for every personal-memory route. The `user_id` it returns is the ONLY source of ownership for what follows.

    401: no valid user session (missing, expired, revoked, tampered). 403: the account was disabled, or a state-changing request lacks the CSRF token.
    An administrator session alone is not a user session: administrators have no personal memory space.
    """
    auth = get_auth(request)
    token = request.cookies.get(USER_COOKIE)
    ident = auth.verify_user(token) if token else None  # raises AccountDisabled (403) for a disabled account
    if ident is None:
        if token:
            raise AuthError("your session has expired; sign in again", code="session_expired")
        if try_admin(request) is not None:
            raise MorpheError("this area belongs to ordinary user accounts; administrators have no personal memory",
                              code="user_account_required", status_code=403)
        raise AuthError("authentication is required")
    _check_csrf(request, auth, ident.sid)
    request.state.identity = ident
    return ident


def current_identity(request: Request) -> Identity | None:
    """Who the cookies say the caller is (administrator first, then user); None for an unauthenticated visitor. Raises for disabled accounts."""
    admin = try_admin(request)
    if admin is not None:
        return admin
    token = request.cookies.get(USER_COOKIE)
    return get_auth(request).verify_user(token) if token else None
