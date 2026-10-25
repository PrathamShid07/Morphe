#!/usr/bin/env python
"""Create or rotate MORPHE's administrator credentials locally.

    python backend/scripts/admin_credentials.py init                # first-time setup; refuses if a password hash already exists
    python backend/scripts/admin_credentials.py init --username me  # choose the username (default: morphe-admin)
    python backend/scripts/admin_credentials.py rotate              # new password for the SAME account (existing sessions stay valid until they expire/logout)
    python backend/scripts/admin_credentials.py rotate --revoke-sessions   # also sign every administrator out (restart not needed)
    python backend/scripts/admin_credentials.py secret              # (re)generate only SESSION_SECRET (ends every session on next API start)
    python backend/scripts/admin_credentials.py check               # verify .env is configured (never prints secrets)

The new password is printed ONCE to this terminal and never written anywhere. Only an Argon2id hash and a random session-signing
secret are stored, in the git-ignored backend/.env. You may also supply the password yourself with --password-from-env VAR.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
ENV = BACKEND / ".env"

from app.auth.passwords import hash_password, verify_password  # noqa: E402


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                v = m.group(2)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[m.group(1)] = v
    return out


def write_env(updates: dict[str, str]) -> None:
    """Set keys in backend/.env in place (other lines untouched). Values are single-quoted so `$` in Argon2 hashes is literal."""
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    done: set[str] = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{m.group(1)}='{updates[m.group(1)]}'"
            done.add(m.group(1))
    missing = [k for k in updates if k not in done]
    if missing:
        lines += ["", "# --- authentication (written by backend/scripts/admin_credentials.py; never commit this file) ---------------"]
        lines += [f"{k}='{updates[k]}'" for k in missing]
    ENV.write_text("\n".join(lines) + "\n")
    os.chmod(ENV, stat.S_IRUSR | stat.S_IWUSR)  # 0600: owner only


def new_password() -> str:
    return secrets.token_urlsafe(18)  # 144 bits of entropy


def revoke_all_sessions(env: dict[str, str]) -> int:
    """Mark every stored administrator session revoked (talks to MongoDB directly; the API need not be restarted)."""
    from pymongo import MongoClient
    uri = env.get("MONGODB_URI", "mongodb://localhost:27017")
    db = env.get("MONGODB_DATABASE", "morphe")
    res = MongoClient(uri, serverSelectionTimeoutMS=3000)[db]["admin_sessions"].update_many({"revoked": False}, {"$set": {"revoked": True}})
    return res.modified_count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["init", "rotate", "secret", "check"])
    ap.add_argument("--username", default=None)
    ap.add_argument("--password-from-env", metavar="VAR", default=None, help="read the new password from this environment variable instead of generating one")
    ap.add_argument("--revoke-sessions", action="store_true")
    a = ap.parse_args()
    env = read_env()
    has_hash = bool(env.get("ADMIN_PASSWORD_HASH"))

    if a.action == "check":
        ok = has_hash and len(env.get("SESSION_SECRET", "")) >= 32
        print(f"ADMIN_USERNAME      : {env.get('ADMIN_USERNAME', '(default: morphe-admin)')}")
        print(f"ADMIN_PASSWORD_HASH : {'set' if has_hash else 'MISSING'}")
        print(f"SESSION_SECRET      : {'set' if len(env.get('SESSION_SECRET', '')) >= 32 else 'MISSING or shorter than 32 characters'}")
        return 0 if ok else 1

    if a.action == "secret":
        write_env({"SESSION_SECRET": secrets.token_urlsafe(48)})
        print("SESSION_SECRET regenerated in backend/.env. Restart the API; every existing session becomes invalid.")
        return 0

    if a.action == "init" and has_hash:
        print("An administrator password hash already exists in backend/.env, so `init` will not replace it.\n"
              "Use `rotate` to set a new password for the same account (this is the safe recovery path).", file=sys.stderr)
        return 2
    if a.action == "rotate" and not has_hash:
        print("No administrator account is configured yet; run `init` first.", file=sys.stderr)
        return 2

    username = a.username or env.get("ADMIN_USERNAME") or "morphe-admin"
    if a.password_from_env:
        pw = os.environ.get(a.password_from_env, "")
        if len(pw) < 12:
            print(f"${a.password_from_env} must contain a password of at least 12 characters.", file=sys.stderr)
            return 2
        shown = False
    else:
        pw, shown = new_password(), True
    updates = {"ADMIN_USERNAME": username, "ADMIN_PASSWORD_HASH": hash_password(pw)}
    if len(env.get("SESSION_SECRET", "")) < 32:
        updates["SESSION_SECRET"] = secrets.token_urlsafe(48)
    write_env(updates)
    assert verify_password(read_env()["ADMIN_PASSWORD_HASH"], pw), "stored hash does not verify"  # round-trip check of what was actually written
    print(f"Administrator account configured in backend/.env (file mode 600).\n  username: {username}")
    if shown:
        print(f"  password: {pw}\nThis password is shown only once and is not stored anywhere. Restart the API to load it.")
    if a.revoke_sessions:
        try:
            print(f"Revoked {revoke_all_sessions(env)} existing administrator session(s).")
        except Exception as exc:  # noqa: BLE001
            print(f"Could not revoke sessions in MongoDB ({type(exc).__name__}); regenerate SESSION_SECRET with `secret` and restart instead.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
