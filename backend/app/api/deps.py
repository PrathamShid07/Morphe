from __future__ import annotations

from dataclasses import dataclass

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
    # Admin cookie: SameSite=Strict (never sent on cross-site requests). Visitor cookie: Lax. Both HttpOnly; Secure outside plain-HTTP development.
    response.set_cookie(name, value, max_age=int(max_age), httponly=True, secure=auth.cookie_secure,
                        samesite="strict" if name == ADMIN_COOKIE else "lax", path="/")


def clear_cookie(response: Response, auth: AuthService, name: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, secure=auth.cookie_secure,
                           samesite="strict" if name == ADMIN_COOKIE else "lax")


def require_admin(request: Request) -> Identity:
    """Authorization gate for every administrative route. The role comes only from a verified, unexpired, unrevoked server session.

    401: no valid session at all, or the administrator session has expired / been revoked / has a bad signature.
    403: a valid ordinary visitor session without administrator rights, or a state-changing request without a valid CSRF token.
    """
    auth = get_auth(request)
    token = request.cookies.get(ADMIN_COOKIE)
    ident = auth.verify_admin(token) if token else None
    if ident is None:
        if token:
            raise AuthError("your administrator session has expired or is invalid; sign in again", code="session_expired")
        if auth.verify_user(request.cookies.get(USER_COOKIE)):
            raise MorpheError("administrator privileges are required for this operation", code="admin_required", status_code=403)
        raise AuthError("authentication is required")
    if request.method not in SAFE_METHODS and not auth.csrf_ok(ident.sid, request.headers.get(CSRF_HEADER)):
        raise MorpheError("missing or invalid CSRF token", code="csrf_failed", status_code=403)
    return ident


def try_admin(request: Request) -> Identity | None:
    """Admin identity if the request carries a valid administrator session; never raises (used for role-aware responses)."""
    try:
        token = request.cookies.get(ADMIN_COOKIE)
        return get_auth(request).verify_admin(token) if token else None
    except MorpheError:
        return None


@dataclass
class Actor:
    role: str  # "admin" | "user"
    sid: str   # the visitor-session id (server-issued); present for administrators too
    admin: Identity | None = None

    @property
    def is_admin(self) -> bool:
        return self.admin is not None


def get_actor(request: Request, response: Response) -> Actor:
    """Who is calling a public endpoint: an administrator (valid session + CSRF on unsafe methods) or an ordinary visitor.

    Every caller gets a server-issued, signed visitor-session cookie; the client never gets to choose its own identifier.
    """
    auth = get_auth(request)
    admin = try_admin(request)
    if admin is not None and request.method not in SAFE_METHODS and not auth.csrf_ok(admin.sid, request.headers.get(CSRF_HEADER)):
        admin = None  # an unsigned state-changing request is treated as an ordinary visitor's
    sid = auth.verify_user(request.cookies.get(USER_COOKIE))
    if sid is None:
        token, sid = auth.new_user_session()
        set_session_cookie(response, auth, USER_COOKIE, token, auth.user_ttl)
    return Actor("admin" if admin else "user", sid, admin)
