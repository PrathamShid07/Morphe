"""Session management for MORPHE.

* **Admin session**: created only by `AuthService.login` after a successful Argon2 verification. The cookie holds a signed token
  (`HMAC-SHA256`) with a session id and expiry; the session also exists server-side in MongoDB so `logout` really revokes it.
* **User session**: an anonymous, server-issued, signed identifier. It carries no privilege; it only scopes a visitor's own query history.
* **CSRF token**: `HMAC(secret, "csrf." + session id)`; delivered in a JSON body and echoed by the SPA in `X-CSRF-Token` on state-changing admin requests.

The role is never read from anything the client sends other than a cookie whose signature and server-side record verify.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from ..common import MorpheError, get_logger, iso, log_event, new_id
from .passwords import hash_password, verify_password

log = get_logger("auth")
ADMIN_COOKIE = "morphe_admin"
USER_COOKIE = "morphe_session"
SESSIONS = "admin_sessions"

Role = Literal["admin", "user"]
_DUMMY_HASH: str | None = None


class AuthError(MorpheError):
    status_code, code = 401, "authentication_required"


@dataclass(frozen=True)
class Identity:
    role: Role
    sid: str
    username: str | None
    expires_at: float


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AuthService:
    def __init__(self, settings, mongo_provider: Callable[[], Any], clock: Callable[[], float] = time.time):
        self.s = settings
        self._mongo_provider = mongo_provider
        self.clock = clock
        secret = settings.session_secret
        if not secret:
            secret = secrets.token_urlsafe(48)  # development only: sessions then end whenever the API restarts
            log_event(log, "ephemeral_session_secret", level=30, note="SESSION_SECRET is not configured; sessions will not survive a restart")
        self._secret = secret.encode()
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._lock = threading.Lock()
        self._indexed = False

    # ------------------------------------------------------------------------------------------------------------- properties
    @property
    def admin_configured(self) -> bool:
        return bool(self.s.admin_password_hash)

    @property
    def cookie_secure(self) -> bool:
        return self.s.is_production if self.s.session_cookie_secure is None else bool(self.s.session_cookie_secure)

    @property
    def admin_ttl(self) -> float:
        return float(self.s.admin_session_ttl_minutes) * 60

    @property
    def user_ttl(self) -> float:
        return float(self.s.user_session_ttl_days) * 86400

    def _col(self):
        mongo = self._mongo_provider()
        if mongo is None:
            raise MorpheError("service is not ready", code="not_ready", status_code=503)
        col = mongo.col(SESSIONS)
        if not self._indexed:
            col.create_index("sid", unique=True)
            self._indexed = True
        return col

    # ----------------------------------------------------------------------------------------------------------- token signing
    def _sign(self, kind: str, payload: dict[str, Any]) -> str:
        body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        mac = hmac.new(self._secret, f"{kind}.{body}".encode(), hashlib.sha256).digest()
        return f"{body}.{_b64(mac)}"

    def _unsign(self, kind: str, token: str | None) -> dict[str, Any] | None:
        """Payload if the signature verifies for this kind and the token has not expired; otherwise None."""
        if not token or token.count(".") != 1 or len(token) > 1024:
            return None
        body, sig = token.split(".")
        try:
            mac = hmac.new(self._secret, f"{kind}.{body}".encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(mac, _unb64(sig)):
                return None
            payload = json.loads(_unb64(body))
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("k") != kind or float(payload.get("exp", 0)) <= self.clock():
            return None
        return payload

    def csrf_token(self, sid: str) -> str:
        return _b64(hmac.new(self._secret, f"csrf.{sid}".encode(), hashlib.sha256).digest())

    def csrf_ok(self, sid: str, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(self.csrf_token(sid), presented or "")

    # ------------------------------------------------------------------------------------------------------------ rate limiting
    def _throttle_key(self, ip: str, username: str) -> tuple[str, str]:
        return (ip or "unknown", (username or "").strip().lower()[:64])

    def _check_lockout(self, key: tuple[str, str]) -> None:
        now = self.clock()
        window = float(self.s.login_lockout_seconds)
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < window]
            self._failures[key] = recent
            if len(recent) >= self.s.login_max_attempts:
                retry = int(window - (now - recent[0])) + 1
                raise MorpheError(f"too many failed sign-in attempts; try again in {retry} seconds", code="too_many_attempts",
                                  status_code=429, details={"retry_after_seconds": retry})

    def _record_failure(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(self.clock())

    # ------------------------------------------------------------------------------------------------------------- admin login
    def login(self, username: str, password: str, ip: str = "") -> tuple[str, Identity]:
        global _DUMMY_HASH
        if not self.admin_configured:
            raise MorpheError("administrator sign-in is not configured on this server (see SETUP.md, 'Administrator access')",
                              code="admin_not_configured", status_code=503)
        key = self._throttle_key(ip, username)
        self._check_lockout(key)
        user_ok = hmac.compare_digest((username or "").encode(), self.s.admin_username.encode())
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_hex(8))
        pw_ok = verify_password(self.s.admin_password_hash if user_ok else _DUMMY_HASH, password or "")  # same work either way
        if not (user_ok and pw_ok):
            self._record_failure(key)
            log_event(log, "admin_login_failed", level=30, ip=ip)
            raise AuthError("invalid username or password", code="invalid_credentials")
        with self._lock:
            self._failures.pop(key, None)
        now = self.clock()
        sid = new_id("adm")
        exp = now + self.admin_ttl
        col = self._col()
        col.delete_many({"exp": {"$lt": now - 86400}})  # housekeeping: forget sessions that ended more than a day ago
        col.insert_one({"sid": sid, "username": self.s.admin_username, "created_at": iso(), "exp": exp, "revoked": False})
        log_event(log, "admin_login", ip=ip, session=sid)
        token = self._sign("admin", {"k": "admin", "sid": sid, "u": self.s.admin_username, "iat": now, "exp": exp})
        return token, Identity("admin", sid, self.s.admin_username, exp)

    def verify_admin(self, token: str | None) -> Identity | None:
        """The admin identity behind a cookie, or None. Requires a valid signature, an unexpired token and a live (unrevoked) server record."""
        payload = self._unsign("admin", token)
        if payload is None:
            return None
        doc = self._col().find_one({"sid": payload.get("sid")})
        if not doc or doc.get("revoked") or float(doc.get("exp", 0)) <= self.clock():
            return None
        return Identity("admin", payload["sid"], doc.get("username"), float(doc["exp"]))

    def logout(self, token: str | None) -> bool:
        payload = self._unsign("admin", token)
        if payload is None:
            return False
        self._col().update_one({"sid": payload.get("sid")}, {"$set": {"revoked": True, "revoked_at": iso()}})
        log_event(log, "admin_logout", session=payload.get("sid"))
        return True

    # ------------------------------------------------------------------------------------------------------- anonymous user session
    def new_user_session(self) -> tuple[str, str]:
        sid = new_id("usr")
        exp = self.clock() + self.user_ttl
        return self._sign("user", {"k": "user", "sid": sid, "exp": exp}), sid

    def verify_user(self, token: str | None) -> str | None:
        payload = self._unsign("user", token)
        return payload.get("sid") if payload else None
