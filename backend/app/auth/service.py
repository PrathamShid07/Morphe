"""Session management for MORPHE.

* **Admin session**: created only by `AuthService.login` after a successful Argon2 verification. The cookie holds a signed token
  (`HMAC-SHA256`) with a session id and expiry; the session also exists server-side in MongoDB so `logout` really revokes it.
* **User session**: created by registration or `user_login` after an Argon2 check against the account's own hash. Same cookie/server-side-record design
  (`user_sessions`), so logout, account disabling and password changes revoke it immediately. The `user_id` that scopes every private memory comes
  only from this verified record, never from a request body, header or URL.
* **CSRF token**: `HMAC(secret, "csrf." + session id)`; delivered in a JSON body and echoed by the SPA in `X-CSRF-Token` on state-changing requests.

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

from ..accounts.users import ACTIVE, AccountDisabled, UserStore, normalize_email, public_user, validate_email, validate_name, validate_password
from ..common import MorpheError, get_logger, iso, log_event, new_id
from .passwords import hash_password, verify_password

log = get_logger("auth")
ADMIN_COOKIE = "morphe_admin"
USER_COOKIE = "morphe_user"
SESSIONS = "admin_sessions"
USER_SESSIONS = "user_sessions"

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
    user_id: str | None = None
    name: str | None = None
    email: str | None = None


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
        self._user_indexed = False
        self._users: UserStore | None = None

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

    # --------------------------------------------------------------------------------------------------------------- user accounts
    @property
    def users(self) -> UserStore:
        mongo = self._mongo_provider()
        if mongo is None:
            raise MorpheError("service is not ready", code="not_ready", status_code=503)
        if self._users is None or self._users.mongo is not mongo:
            self._users = UserStore(mongo)
        return self._users

    def _user_sessions(self):
        mongo = self._mongo_provider()
        if mongo is None:
            raise MorpheError("service is not ready", code="not_ready", status_code=503)
        col = mongo.col(USER_SESSIONS)
        if not self._user_indexed:
            col.create_index("sid", unique=True)
            col.create_index("user_id")
            self._user_indexed = True
        return col

    def _open_user_session(self, user: dict[str, Any]) -> tuple[str, Identity]:
        now = self.clock()
        sid, exp = new_id("ses"), now + self.user_ttl
        col = self._user_sessions()
        col.delete_many({"exp": {"$lt": now - 86400}})
        col.insert_one({"sid": sid, "user_id": user["user_id"], "created_at": iso(), "exp": exp, "revoked": False})
        token = self._sign("user", {"k": "user", "sid": sid, "u": user["user_id"], "iat": now, "exp": exp})
        return token, Identity("user", sid, None, exp, user["user_id"], user.get("full_name"), user.get("email"))

    def register(self, full_name: str, email: str, password: str, ip: str = "") -> tuple[str, Identity, dict[str, Any]]:
        """Create a regular `user` account and sign it in. The role is fixed here, on the server; the request cannot influence it."""
        if not self.s.registration_enabled:
            raise MorpheError("registration is disabled on this server", code="registration_disabled", status_code=403)
        key = ("register", ip or "unknown")
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < 3600]
            self._failures[key] = recent
            if len(recent) >= self.s.register_max_per_hour:
                raise MorpheError("too many accounts were created from this address recently; try again later", code="too_many_attempts", status_code=429)
        name, mail = validate_name(full_name), validate_email(email)
        validate_password(password, email=mail, name=name, min_length=self.s.password_min_length)
        doc = self.users.create(name, mail, hash_password(password))
        with self._lock:
            self._failures.setdefault(key, []).append(now)
        log_event(log, "user_registered", user_id=doc["user_id"])
        token, ident = self._open_user_session(doc)
        return token, ident, doc

    def user_login(self, email: str, password: str, ip: str = "") -> tuple[str, Identity, dict[str, Any]]:
        global _DUMMY_HASH
        key = self._throttle_key(ip, normalize_email(email))
        self._check_lockout(key)
        doc = self.users.by_email(email)
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_hex(8))
        ok = verify_password(doc["password_hash"] if doc else _DUMMY_HASH, password or "")  # the same work whether or not the account exists
        if not (doc and ok):
            self._record_failure(key)
            log_event(log, "user_login_failed", level=30)
            raise AuthError("invalid email or password", code="invalid_credentials")
        if doc.get("status") != ACTIVE:
            raise AccountDisabled("this account has been disabled; contact an administrator")
        with self._lock:
            self._failures.pop(key, None)
        self.users.touch(doc["user_id"], login=True)
        token, ident = self._open_user_session(doc)
        log_event(log, "user_login", user_id=doc["user_id"], session=ident.sid)
        return token, ident, doc

    def verify_user(self, token: str | None) -> Identity | None:
        """The user behind a cookie, or None. Needs a valid signature, an unexpired token and a live session record. A disabled account raises."""
        payload = self._unsign("user", token)
        if payload is None:
            return None
        doc = self._user_sessions().find_one({"sid": payload.get("sid")})
        if not doc or doc.get("revoked") or float(doc.get("exp", 0)) <= self.clock():
            return None
        user = self.users.by_id(doc["user_id"])
        if user is None:
            return None
        if user.get("status") != ACTIVE:
            raise AccountDisabled("this account has been disabled; contact an administrator")
        return Identity("user", doc["sid"], None, float(doc["exp"]), user["user_id"], user.get("full_name"), user.get("email"))

    def logout_user(self, token: str | None) -> Identity | None:
        payload = self._unsign("user", token)
        if payload is None:
            return None
        doc = self._user_sessions().find_one({"sid": payload.get("sid")})
        self._user_sessions().update_one({"sid": payload.get("sid")}, {"$set": {"revoked": True, "revoked_at": iso()}})
        if not doc:
            return None
        return Identity("user", doc["sid"], None, float(doc.get("exp", 0)), doc.get("user_id"))

    def revoke_user_sessions(self, user_id: str) -> int:
        """End every session of an account (used when it is disabled). Takes effect on the very next request."""
        res = self._user_sessions().update_many({"user_id": user_id, "revoked": False}, {"$set": {"revoked": True, "revoked_at": iso()}})
        return int(res.modified_count)
