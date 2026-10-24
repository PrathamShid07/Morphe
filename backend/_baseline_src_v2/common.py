"""Small shared helpers: IDs, time, errors, structured logging context."""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_last_ts = 0
_counter = 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id(prefix: str) -> str:
    """Time-sortable unique ID in the style `prefix_01J...` (ULID-like, Crockford base32)."""
    global _last_ts, _counter
    ts = int(time.time() * 1000)
    if ts == _last_ts:
        _counter += 1
    else:
        _last_ts, _counter = ts, 0
    n = ts
    head = ""
    for _ in range(10):
        head = _B32[n & 31] + head
        n >>= 5
    rnd = int.from_bytes(os.urandom(10), "big") + _counter
    tail = ""
    for _ in range(10):
        tail = _B32[rnd & 31] + tail
        rnd >>= 5
    return f"{prefix}_{head}{tail}"


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s or "x")[:max_len].strip("_") or "x"


def humanize_name(name: str) -> str:
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    words = re.sub(r"[_\-]+", " ", base).strip()
    return " ".join(w if w.isupper() else w.capitalize() for w in words.split()) or name


# ---------------------------------------------------------------- errors ----
class MorpheError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details


class NotFoundError(MorpheError):
    status_code, code = 404, "not_found"


class ConflictError(MorpheError):
    status_code, code = 409, "conflict"


class ValidationFailure(MorpheError):
    status_code, code = 422, "invalid_input"


class DependencyUnavailable(MorpheError):
    status_code, code = 503, "dependency_unavailable"


class IntegrityError(MorpheError):
    status_code, code = 500, "integrity_error"


# --------------------------------------------------------------- logging ----
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
operation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("operation_id", default=None)


class _ContextFormatter(logging.Formatter):
    def __init__(self, as_json: bool):
        super().__init__()
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        fields = dict(getattr(record, "fields", {}) or {})
        base = {
            "timestamp": iso(),
            "level": record.levelname,
            "component": record.name,
            "event": record.getMessage(),
        }
        if (rid := request_id_var.get()) is not None:
            base["request_id"] = rid
        if (oid := operation_id_var.get()) is not None:
            base["operation_id"] = oid
        base.update(fields)
        if record.exc_info:
            base["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        if self.as_json:
            return json.dumps(base, default=str)
        extras = " ".join(f"{k}={v}" for k, v in base.items() if k not in {"timestamp", "level", "component", "event"})
        return f'{base["timestamp"]} {base["level"]:<7} {base["component"]} {base["event"]} {extras}'.rstrip()


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    root = logging.getLogger("morphe")
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ContextFormatter(as_json))
    root.addHandler(handler)
    root.setLevel(level.upper())
    root.propagate = False


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"morphe.{component}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Structured log line. Callers pass IDs, counts and statuses — never credentials or source text."""
    logger.log(level, event, extra={"fields": fields})
