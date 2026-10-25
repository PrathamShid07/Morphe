"""Password hashing with Argon2id (argon2-cffi). Plaintext passwords are never stored or logged."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_DEFAULT = PasswordHasher()  # argon2id, RFC 9106 low-memory parameters


def hash_password(password: str, *, hasher: PasswordHasher | None = None) -> str:
    return (hasher or _DEFAULT).hash(password)


def fast_hasher() -> PasswordHasher:
    """Cheap parameters for automated tests only (the real parameters are encoded inside every hash and honoured on verify)."""
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


def verify_password(password_hash: str, password: str) -> bool:
    """True only for a correct password. Malformed hashes and mismatches both return False (never raise)."""
    if not password_hash:
        return False
    try:
        return _DEFAULT.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _DEFAULT.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False
