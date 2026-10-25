"""User accounts: registration, sign-in, sessions, disabling. Real routes, real hashing, real stores (mongomock / in-memory Qdrant)."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.auth.service import ADMIN_COOKIE, USER_COOKIE
from app.main import create_app
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD, login_admin, login_user, make_settings, register_user

A = "/api/v1"


def reg(c, **over):
    body = {"full_name": "Alice Tester", "email": "alice@example.com", "password": USER_PASSWORD, "confirm_password": USER_PASSWORD} | over
    return c.post(f"{A}/auth/register", json=body)


def test_registration_creates_a_regular_user_and_signs_in(anon_client):
    r = reg(anon_client)
    body = r.json()
    assert r.status_code == 201 and body["authenticated"] and body["role"] == "user" and body["csrf_token"]
    assert body["user"]["email"] == "alice@example.com" and body["user"]["full_name"] == "Alice Tester" and body["user"]["status"] == "active"
    blob = r.text
    assert USER_PASSWORD not in blob and "password_hash" not in blob and "email_norm" not in blob and "argon2" not in blob
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith(USER_COOKIE))
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()
    assert anon_client.get(f"{A}/auth/me").json()["user"]["user_id"] == body["user"]["user_id"]


def test_the_server_alone_assigns_the_role(anon_client):
    ctx = anon_client.app.state.ctx
    for extra in ({"role": "admin"}, {"is_admin": True}, {"user_id": "usr_x"}, {"status": "active"}):
        r = reg(anon_client, **extra)
        assert r.status_code == 422, extra
    assert ctx.mongo.col("users").count_documents({}) == 0  # nothing was created by the rejected attempts
    assert reg(anon_client).json()["role"] == "user"
    assert {u["role"] for u in ctx.mongo.col("users").find({})} == {"user"}


def test_duplicate_emails_are_refused_whatever_the_case_or_spacing(world):
    a, b = world.browser(), world.browser()
    assert reg(a).status_code == 201
    for variant in ("alice@example.com", "ALICE@Example.COM", "  Alice@example.com  "):
        r = reg(b, email=variant, full_name="Mallory Other")
        assert r.status_code == 409 and r.json()["error"]["code"] == "email_taken", variant
    assert world.ctx.mongo.col("users").count_documents({}) == 1


@pytest.mark.parametrize("over,code", [
    ({"email": "not-an-email"}, "invalid_email"), ({"email": "a@b"}, "invalid_email"), ({"email": ""}, None),
    ({"full_name": "A"}, "invalid_name"), ({"full_name": "x" * 130}, None),
    ({"password": "short1!", "confirm_password": "short1!"}, "weak_password"),
    ({"password": "alllettersonlyhere", "confirm_password": "alllettersonlyhere"}, "weak_password"),
    ({"password": "1234567890", "confirm_password": "1234567890"}, "weak_password"),
    ({"password": "Another-Good-Pass-1", "confirm_password": "Different-Pass-2"}, "password_mismatch"),
])
def test_invalid_registrations_are_rejected_with_helpful_errors(anon_client, over, code):
    ctx = anon_client.app.state.ctx
    r = reg(anon_client, **over)
    assert r.status_code == 422
    if code:
        assert r.json()["error"]["code"] == code and r.json()["error"]["message"]
    assert ctx.mongo.col("users").count_documents({}) == 0


def test_emails_are_normalised_and_login_is_case_insensitive(world):
    c = world.browser()
    assert reg(c, email="  Mixed.Case@Example.COM ").json()["user"]["email"] == "mixed.case@example.com"
    other = world.browser()
    assert other.post(f"{A}/auth/login", json={"email": "MIXED.case@example.com", "password": USER_PASSWORD}).status_code == 200


def test_passwords_are_stored_only_as_argon2id_hashes(anon_client):
    ctx = anon_client.app.state.ctx
    reg(anon_client)
    doc = ctx.mongo.col("users").find_one({"email": "alice@example.com"})
    assert doc["password_hash"].startswith("$argon2id$") and USER_PASSWORD not in json.dumps(doc, default=str)
    assert doc["role"] == "user" and doc["status"] == "active" and doc["user_id"].startswith("usr_")
    sessions = list(ctx.mongo.col("user_sessions").find({}))
    assert sessions and USER_PASSWORD not in json.dumps(sessions, default=str)


def test_login_success_and_uniform_failure(world):
    reg(world.browser())
    c = world.browser()
    r = c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": USER_PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "user" and r.json()["user"]["email"] == "alice@example.com"
    bad_pw = world.browser().post(f"{A}/auth/login", json={"email": "alice@example.com", "password": "wrong-password-1"})
    unknown = world.browser().post(f"{A}/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json()["error"]["code"] == unknown.json()["error"]["code"] == "invalid_credentials"
    assert bad_pw.json()["error"]["message"] == unknown.json()["error"]["message"]  # does not reveal which accounts exist
    assert not any(h.startswith(USER_COOKIE) for h in bad_pw.headers.get_list("set-cookie"))


def test_a_valid_session_survives_a_page_refresh(world):
    c = world.browser()
    reg(c)
    refreshed = world.browser()  # a new tab / reload: no memory, same cookies
    for k, v in c.cookies.items():
        refreshed.cookies.set(k, v)
    me = refreshed.get(f"{A}/auth/me").json()
    assert me["authenticated"] and me["role"] == "user" and me["csrf_token"] and me["user"]["email"] == "alice@example.com"


def test_logout_revokes_the_session_even_if_the_cookie_is_replayed(world):
    c = world.browser()
    reg(c)
    c.headers["X-CSRF-Token"] = c.get(f"{A}/auth/me").json()["csrf_token"]
    stolen = c.cookies.get(USER_COOKIE)
    assert c.get(f"{A}/me/graph").status_code == 200
    assert c.post(f"{A}/auth/logout").json()["authenticated"] is False
    assert c.get(f"{A}/me/graph").status_code == 401
    replay = world.browser()
    replay.cookies.set(USER_COOKIE, stolen)
    r = replay.get(f"{A}/me/graph")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    assert replay.get(f"{A}/auth/me").json()["authenticated"] is False


def test_expired_sessions_are_rejected(world):
    c = world.browser()
    reg(c)
    assert c.get(f"{A}/me/graph").status_code == 200
    real = time.time
    world.app.state.auth.clock = lambda: real() + 31 * 86400  # past the 30-day lifetime
    r = c.get(f"{A}/me/graph")
    assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    assert c.get(f"{A}/auth/me").json()["authenticated"] is False


def test_tampered_or_foreign_user_cookies_are_rejected(world, settings):
    c = world.browser()
    reg(c)
    good = c.cookies.get(USER_COOKIE)
    body, sig = good.split(".")
    flip = lambda t: ("B" if t[0] == "A" else "A") + t[1:]
    for forged in (f"{body}.{flip(sig)}", f"{flip(body)}.{sig}", "x.y", "garbage"):
        c.cookies.delete(USER_COOKIE)
        c.cookies.set(USER_COOKIE, forged)
        assert c.get(f"{A}/me/graph").status_code == 401, forged
    other = create_app(settings.model_copy(update={"session_secret": "z" * 48}), world.ctx)
    with TestClient(other) as o:
        register_user(o, "Foreign Person", "foreign@example.com")
        foreign = o.cookies.get(USER_COOKIE)
    c.cookies.delete(USER_COOKIE)
    c.cookies.set(USER_COOKIE, foreign)
    assert c.get(f"{A}/me/graph").status_code == 401  # signed with a different secret


def test_state_changing_user_requests_need_the_csrf_token(world):
    c = world.browser()
    token = reg(c).json()["csrf_token"]
    assert c.get(f"{A}/me/graph").status_code == 200  # safe methods need only the cookie
    r = c.post(f"{A}/me/memories", json={"text": "remember this"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert c.post(f"{A}/me/memories", json={"text": "remember this"}, headers={"X-CSRF-Token": token + "x"}).status_code == 403
    assert c.post(f"{A}/auth/logout").status_code == 403
    assert c.post(f"{A}/me/memories", json={"text": "remember this"}, headers={"X-CSRF-Token": token}).status_code == 201
    other = world.browser()
    register_user(other, "Bob Other", "bob@example.com")
    assert other.post(f"{A}/me/memories", json={"text": "x"}, headers={"X-CSRF-Token": token}).status_code == 403  # another session's token is worthless


def test_repeated_failures_lock_the_account_login(world):
    reg(world.browser())
    c = world.browser()
    for _ in range(5):
        assert c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": "nope-nope-1"}).status_code == 401
    r = c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": USER_PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    real = time.time
    world.app.state.auth.clock = lambda: real() + 301
    assert c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": USER_PASSWORD}).status_code == 200


def test_registration_can_be_switched_off(tmp_path):
    app = create_app(make_settings(tmp_path, registration_enabled=False))
    with TestClient(app) as c:
        r = reg(c)
        assert r.status_code == 403 and r.json()["error"]["code"] == "registration_disabled"
        assert c.get(f"{A}/auth/me").json()["registration_enabled"] is False


def test_sign_ups_from_one_address_are_rate_limited(tmp_path):
    app = create_app(make_settings(tmp_path, register_max_per_hour=3))
    with TestClient(app) as c:
        for i in range(3):
            assert reg(c, email=f"user{i}@example.com").status_code == 201
        r = reg(c, email="user9@example.com")
        assert r.status_code == 429


def test_user_and_administrator_credentials_are_not_interchangeable(world):
    reg(world.browser())
    c = world.browser()
    assert c.post(f"{A}/auth/admin/login", json={"username": "alice@example.com", "password": USER_PASSWORD}).status_code == 401
    assert c.post(f"{A}/auth/login", json={"email": "test-admin@example.com", "password": ADMIN_PASSWORD}).status_code == 401
    assert c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": USER_PASSWORD, "role": "admin"}).status_code == 422
    admin = login_admin(world.browser())
    assert admin.cookies.get(ADMIN_COOKIE) and not admin.cookies.get(USER_COOKIE)


def test_signing_in_as_one_role_replaces_the_other_in_the_same_browser(world):
    c = world.browser()
    reg(c)
    assert c.cookies.get(USER_COOKIE)
    r = c.post(f"{A}/auth/admin/login", json={"username": "test-admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    me = c.get(f"{A}/auth/me").json()
    assert me["role"] == "admin" and me["user"] is None


def test_a_disabled_account_loses_access_immediately(world):
    u = world.user("Alice Tester", "alice@example.com")
    uid = u.get(f"{A}/auth/me").json()["user"]["user_id"]
    assert u.post(f"{A}/me/memories", json={"text": "before the account was disabled"}).status_code == 201
    admin = world.admin()
    r = admin.post(f"{A}/admin/users/{uid}/status", json={"status": "disabled", "reason": "abuse report"})
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    # sessions were revoked on the spot: the very next request on the session the user already holds is refused ...
    for attempt in (lambda: u.get(f"{A}/me/graph"), lambda: u.post(f"{A}/me/memories", json={"text": "after disabling"}),
                    lambda: u.post(f"{A}/me/query", json={"query": "anything"})):
        r = attempt()
        assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
    assert world.ctx.mongo.col("user_memories").count_documents({"text_preview": {"$regex": "after disabling"}}) == 0
    assert world.ctx.mongo.col("user_sessions").count_documents({"user_id": uid, "revoked": False}) == 0
    # ... a fresh login is told why, and nothing can be saved or asked
    c = world.browser()
    r = c.post(f"{A}/auth/login", json={"email": "alice@example.com", "password": USER_PASSWORD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"
    assert u.get(f"{A}/auth/me").json()["authenticated"] is False
    # reactivation restores access
    assert admin.post(f"{A}/admin/users/{uid}/status", json={"status": "active"}).status_code == 200
    assert login_user(world.browser(), "alice@example.com")["role"] == "user"
    assert admin.post(f"{A}/admin/users/{uid}/status", json={"status": "active"}).status_code == 409  # no-op change is reported


def test_a_disabled_flag_alone_blocks_even_a_live_session(world):
    """Defence in depth: if a session somehow survived (not revoked), the account status is still checked on every request."""
    u = world.user("Alice Aardvark", "alice@example.com")
    uid = u.get(f"{A}/auth/me").json()["user"]["user_id"]
    world.ctx.mongo.col("users").update_one({"user_id": uid}, {"$set": {"status": "disabled"}})
    for attempt in (lambda: u.get(f"{A}/me/graph"), lambda: u.post(f"{A}/me/memories", json={"text": "sneaky"}), lambda: u.post(f"{A}/me/query", json={"query": "x"})):
        r = attempt()
        assert r.status_code == 403 and r.json()["error"]["code"] == "account_disabled"
    assert u.get(f"{A}/auth/me").json()["authenticated"] is False  # reported as signed out, not as an error page
    assert world.ctx.mongo.col("user_memories").count_documents({"owner_user_id": uid}) == 0
