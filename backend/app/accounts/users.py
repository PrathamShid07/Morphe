"""Account store. A user is identified everywhere by the stable `user_id`, never by email (an email address may change)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from pymongo import ASCENDING, DESCENDING

from ..common import ConflictError, MorpheError, NotFoundError, ValidationFailure, iso, new_id

COLLECTION = "users"
ACTIVE, DISABLED = "active", "disabled"
_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_COMMON = {"password", "password1", "password123", "qwerty123", "1234567890", "letmein123", "welcome123", "iloveyou123", "admin12345", "morphe12345"}
# Fields that must never leave the server.
PRIVATE_FIELDS = ("password_hash", "_id", "email_norm")


class AccountDisabled(MorpheError):
    status_code, code = 403, "account_disabled"


def normalize_email(email: str) -> str:
    """The canonical form used for uniqueness: NFKC, trimmed, lower-cased. No provider-specific tricks (dots, +tags) are applied."""
    return unicodedata.normalize("NFKC", email or "").strip().lower()


def validate_email(email: str) -> str:
    e = normalize_email(email)
    if not e or len(e) > 254 or not _EMAIL.match(e) or ".." in e:
        raise ValidationFailure("enter a valid email address", code="invalid_email")
    return e


def validate_name(name: str) -> str:
    n = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name or "")).strip()
    if len(n) < 2:
        raise ValidationFailure("enter your full name (at least 2 characters)", code="invalid_name")
    if len(n) > 100 or any(unicodedata.category(c).startswith("C") for c in n):
        raise ValidationFailure("the name is too long or contains invalid characters", code="invalid_name")
    return n


def validate_password(password: str, *, email: str = "", name: str = "", min_length: int = 10) -> None:
    problems: list[str] = []
    p = password or ""
    if len(p) < min_length:
        problems.append(f"at least {min_length} characters")
    if len(p) > 128:
        problems.append("at most 128 characters")
    if p and not (re.search(r"[A-Za-z]", p) and re.search(r"[^A-Za-z]", p)):
        problems.append("a mix of letters and at least one digit or symbol")
    local = email.split("@")[0].lower()
    if p and (p.lower() in _COMMON or (local and len(local) >= 4 and p.lower() == local) or (name and p.lower() == name.lower())):
        problems.append("something that is not an easily guessed password")
    if problems:
        raise ValidationFailure("password needs " + ", ".join(problems), code="weak_password", details={"requirements": problems})


def public_user(doc: dict[str, Any]) -> dict[str, Any]:
    """What may be shown to the account owner (and to administrators): never the hash."""
    return {"user_id": doc["user_id"], "full_name": doc.get("full_name"), "email": doc.get("email"), "role": "user",
            "status": doc.get("status", ACTIVE), "created_at": doc.get("created_at"), "last_login_at": doc.get("last_login_at"),
            "last_activity_at": doc.get("last_activity_at")}


class UserStore:
    def __init__(self, mongo):
        self.mongo = mongo
        self.col = mongo.col(COLLECTION)
        self.col.create_index("user_id", unique=True)
        self.col.create_index("email_norm", unique=True)  # the real uniqueness guarantee, even under concurrent sign-ups
        self.col.create_index([("created_at", DESCENDING)])
        self.col.create_index([("status", ASCENDING)])

    def create(self, full_name: str, email: str, password_hash: str) -> dict[str, Any]:
        from pymongo.errors import DuplicateKeyError
        now = iso()
        doc = {"user_id": new_id("usr"), "full_name": full_name, "email": email, "email_norm": email, "password_hash": password_hash,
               "role": "user", "status": ACTIVE, "created_at": now, "updated_at": now, "last_login_at": now, "last_activity_at": now}  # registering signs in
        try:
            self.col.insert_one(dict(doc))
        except DuplicateKeyError:
            raise ConflictError("an account with this email already exists", code="email_taken", status_code=409) from None
        return doc

    def by_email(self, email: str) -> dict[str, Any] | None:
        return self.col.find_one({"email_norm": normalize_email(email)})

    def by_id(self, user_id: str) -> dict[str, Any] | None:
        return self.col.find_one({"user_id": user_id})

    def get(self, user_id: str) -> dict[str, Any]:
        doc = self.by_id(user_id)
        if not doc:
            raise NotFoundError("user not found", code="user_not_found")
        return doc

    def touch(self, user_id: str, *, login: bool = False) -> None:
        now = iso()
        fields = {"last_activity_at": now, **({"last_login_at": now} if login else {})}
        self.col.update_one({"user_id": user_id}, {"$set": fields})

    def set_status(self, user_id: str, status: str, by: str, reason: str | None) -> dict[str, Any]:
        self.get(user_id)
        now = iso()
        fields: dict[str, Any] = {"status": status, "updated_at": now}
        if status == DISABLED:
            fields.update({"disabled_at": now, "disabled_by": by, "disabled_reason": (reason or "")[:200]})
        else:
            fields.update({"reactivated_at": now, "reactivated_by": by})
        self.col.update_one({"user_id": user_id}, {"$set": fields})
        return self.get(user_id)

    def page(self, flt: dict[str, Any], limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        total = self.col.count_documents(flt)
        rows = list(self.col.find(flt).sort([("created_at", DESCENDING), ("user_id", ASCENDING)]).skip(offset).limit(limit))
        return rows, total
