"""Administrator authentication: login, session verification, expiry, tampering, logout, CSRF, throttling, credential script."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import fast_hasher, hash_password, verify_password
from app.auth.service import ADMIN_COOKIE, USER_COOKIE
from app.config import Settings
from app.main import create_app
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, SESSION_SECRET, admin_test_client, login_admin, make_settings, register_user
from tests.test_api import seed

A = "/api/v1"
LOGIN = f"{A}/auth/admin/login"


def setck(c, name, value):
    c.cookies.delete(name)
    c.cookies.set(name, value)


def login(c, **over):
    body = {"username": ADMIN_USER, "password": ADMIN_PASSWORD} | over
    return c.post(LOGIN, json=body)


def test_valid_credentials_sign_in_and_cookie_is_httponly_strict(anon_client):
    r = login(anon_client)
    body = r.json()
    assert r.status_code == 200 and body["authenticated"] and body["role"] == "admin" and body["username"] == ADMIN_USER and body["csrf_token"]
    assert ADMIN_PASSWORD not in r.text
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith(ADMIN_COOKIE))
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    s = anon_client.get(f"{A}/auth/me").json()
    assert s["authenticated"] and s["role"] == "admin" and s["csrf_token"] == body["csrf_token"]


def test_invalid_credentials_are_rejected_without_revealing_which_part_was_wrong(anon_client):
    bad_pw, bad_user = login(anon_client, password="wrong"), login(anon_client, username="someone-else")
    assert bad_pw.status_code == bad_user.status_code == 401
    assert bad_pw.json()["error"]["code"] == bad_user.json()["error"]["code"] == "invalid_credentials"
    assert bad_pw.json()["error"]["message"] == bad_user.json()["error"]["message"]
    assert not any(h.startswith(ADMIN_COOKIE) for h in bad_pw.headers.get_list("set-cookie"))
    assert anon_client.get(f"{A}/auth/me").json()["authenticated"] is False


def test_a_client_cannot_assert_its_own_role(anon_client):
    r = anon_client.post(LOGIN, json={"username": ADMIN_USER, "password": "wrong", "role": "admin"})
    assert r.status_code == 422  # unknown fields (such as "role") are refused outright
    assert anon_client.get(f"{A}/auth/me", params={"role": "admin"}, headers={"X-Role": "admin", "X-User-Role": "admin"}).json()["role"] is None
    anon_client.cookies.set("role", "admin")
    assert anon_client.get(f"{A}/memories").status_code in (401, 403)


def test_expired_session_is_rejected(settings):
    app = create_app(settings.model_copy(update={"admin_session_ttl_minutes": 10}))
    with admin_test_client(app) as c:
        assert c.get(f"{A}/metrics/summary").status_code == 200
        real = time.time
        c.app.state.auth.clock = lambda: real() + 11 * 60  # eleven minutes later
        r = c.get(f"{A}/metrics/summary")
        assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
        s = c.get(f"{A}/auth/me").json()
        assert s["authenticated"] is False and s["role"] is None


def test_tampered_forged_and_swapped_cookies_are_rejected(anon_client, settings):
    login(anon_client)
    good = anon_client.cookies.get(ADMIN_COOKIE)
    body, sig = good.split(".")
    flip = lambda t: ("B" if t[0] == "A" else "A") + t[1:]  # always a different first character (the last base64 char can be non-significant)
    for forged in (f"{body}.{flip(sig)}", f"{flip(body)}.{sig}", "x.y", "garbage", ""):
        setck(anon_client, ADMIN_COOKIE, forged)
        r = anon_client.get(f"{A}/metrics/summary")
        assert r.status_code in (401, 403), forged
    # a perfectly valid token signed with a DIFFERENT secret is just as worthless
    other = create_app(settings.model_copy(update={"session_secret": "z" * 48}))
    with TestClient(other) as o:
        login_admin(o)
        foreign = o.cookies.get(ADMIN_COOKIE)
    setck(anon_client, ADMIN_COOKIE, foreign)
    assert anon_client.get(f"{A}/metrics/summary").status_code == 401
    # a user token cannot be passed off as an administrator token (the signature covers the token kind)
    with TestClient(anon_client.app) as u:
        register_user(u)
        user_token = u.cookies.get(USER_COOKIE)
    setck(anon_client, ADMIN_COOKIE, user_token)
    assert anon_client.get(f"{A}/metrics/summary").status_code == 401


def test_logout_revokes_the_session_even_if_the_cookie_is_replayed(client):
    stolen = client.cookies.get(ADMIN_COOKIE)
    assert client.get(f"{A}/metrics/summary").status_code == 200
    r = client.post(f"{A}/auth/logout")
    assert r.status_code == 200 and r.json()["authenticated"] is False
    assert client.get(f"{A}/metrics/summary").status_code in (401, 403)
    with TestClient(client.app) as replay:  # an attacker re-presenting the exact cookie that was valid before logout
        replay.cookies.set(ADMIN_COOKIE, stolen)
        r = replay.get(f"{A}/metrics/summary")
        assert r.status_code == 401 and r.json()["error"]["code"] == "session_expired"
        assert replay.get(f"{A}/auth/me").json()["authenticated"] is False


def test_logout_is_idempotent_without_a_session(anon_client):
    assert anon_client.post(f"{A}/auth/logout").status_code == 200


def test_state_changing_admin_requests_need_the_csrf_token(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        token = login(c).json()["csrf_token"]
        assert c.get(f"{A}/metrics/summary").status_code == 200  # safe methods need only the cookie
        r = c.post(f"{A}/versions/checkpoint")
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
        assert c.post(f"{A}/versions/checkpoint", headers={"X-CSRF-Token": "not-the-token"}).status_code == 403
        assert c.post(f"{A}/versions/checkpoint", headers={"X-CSRF-Token": token + "x"}).status_code == 403
        assert c.post(f"{A}/auth/logout").status_code == 403  # even sign-out is protected
        assert c.post(f"{A}/versions/checkpoint", headers={"X-CSRF-Token": token}).status_code == 200


def test_csrf_token_of_one_session_does_not_work_for_another(settings):
    app = create_app(settings)
    with TestClient(app) as a, TestClient(app) as b:
        ta = login(a).json()["csrf_token"]
        login(b)
        assert b.post(f"{A}/versions/checkpoint", headers={"X-CSRF-Token": ta}).status_code == 403


def test_cross_site_origin_is_refused_for_state_changing_requests(client):
    r = client.post(f"{A}/versions/checkpoint", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "origin_not_allowed"
    assert client.post(f"{A}/versions/checkpoint", headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_repeated_failures_lock_the_sign_in_even_for_the_right_password(anon_client):
    for _ in range(5):
        assert login(anon_client, password="nope").status_code == 401
    r = login(anon_client)  # correct password, but locked
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_attempts"
    real = time.time
    anon_client.app.state.auth.clock = lambda: real() + 301  # the lock lapses
    assert login(anon_client).status_code == 200


def test_sign_in_is_disabled_when_no_admin_is_configured(tmp_path):
    app = create_app(make_settings(tmp_path, admin_password_hash=""))
    with TestClient(app) as c:
        r = login(c)
        assert r.status_code == 503 and r.json()["error"]["code"] == "admin_not_configured"
        assert c.get(f"{A}/metrics/summary").status_code == 401
        assert c.get(f"{A}/auth/me").json()["admin_configured"] is False


def test_passwords_are_stored_as_argon2id_and_never_in_plaintext(settings):
    h = settings.admin_password_hash
    assert h.startswith("$argon2id$") and ADMIN_PASSWORD not in h
    assert verify_password(h, ADMIN_PASSWORD) and not verify_password(h, ADMIN_PASSWORD + "x")
    assert not verify_password("", "anything") and not verify_password("not-a-hash", "anything")
    assert hash_password("same", hasher=fast_hasher()) != hash_password("same", hasher=fast_hasher())  # salted


def test_settings_reject_a_weak_session_secret_and_require_one_in_production(tmp_path):
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        make_settings(tmp_path, session_secret="short")
    with pytest.raises(ValueError, match="SESSION_SECRET is required"):
        Settings(app_env="production", admin_password_hash="$argon2id$x", session_secret="", cors_origins="https://m.example",
                 embedding_backend="sentence_transformers", mongo_backend="pymongo")


def test_secure_cookie_flag_follows_the_environment(settings, tmp_path):
    insecure = create_app(settings).state.auth
    secure = create_app(settings.model_copy(update={"session_cookie_secure": True})).state.auth
    assert insecure.cookie_secure is False and secure.cookie_secure is True


# ----------------------------------------------------------------------------------------------- credential bootstrap script
@pytest.fixture()
def cred(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "admin_credentials.py"
    spec = importlib.util.spec_from_file_location("admin_credentials", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ENV", tmp_path / ".env")
    (tmp_path / ".env").write_text("APP_ENV=development\nMONGODB_DATABASE=morphe\n")
    return mod


def run_script(mod, monkeypatch, capsys, *argv):
    monkeypatch.setattr("sys.argv", ["admin_credentials.py", *argv])
    code = mod.main()
    return code, capsys.readouterr()


def test_credential_script_creates_a_working_account_and_prints_the_password_once(cred, monkeypatch, capsys):
    code, out = run_script(cred, monkeypatch, capsys, "init")
    assert code == 0 and "username: morphe-admin" in out.out
    pw = next(line.split("password: ")[1] for line in out.out.splitlines() if "password: " in line).strip()
    text = cred.ENV.read_text()
    env = cred.read_env()
    assert pw not in text  # only the hash is stored
    assert env["ADMIN_PASSWORD_HASH"].startswith("$argon2id$") and verify_password(env["ADMIN_PASSWORD_HASH"], pw)
    assert len(env["SESSION_SECRET"]) >= 32 and env["APP_ENV"] == "development"  # unrelated lines untouched
    assert stat.S_IMODE(os.stat(cred.ENV).st_mode) == 0o600
    # and the settings loader reads exactly that file back (including the `$` characters of the hash)
    s = Settings(_env_file=str(cred.ENV))
    assert s.admin_password_hash == env["ADMIN_PASSWORD_HASH"] and verify_password(s.admin_password_hash, pw)


def test_credential_script_never_silently_replaces_an_existing_account(cred, monkeypatch, capsys):
    run_script(cred, monkeypatch, capsys, "init")
    before = cred.ENV.read_text()
    code, out = run_script(cred, monkeypatch, capsys, "init")
    assert code == 2 and cred.ENV.read_text() == before and "rotate" in out.err
    code, out = run_script(cred, monkeypatch, capsys, "rotate")
    assert code == 0 and cred.ENV.read_text() != before


def test_credential_script_check_and_rotate_without_account(cred, monkeypatch, capsys):
    assert run_script(cred, monkeypatch, capsys, "check")[0] == 1
    assert run_script(cred, monkeypatch, capsys, "rotate")[0] == 2
    assert run_script(cred, monkeypatch, capsys, "init")[0] == 0
    assert run_script(cred, monkeypatch, capsys, "check")[0] == 0
