"""Role-based access control exercised on the REAL routes (never just on frontend visibility)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.service import ADMIN_COOKIE, USER_COOKIE
from app.main import create_app
from tests.conftest import login_admin, register_user

A = "/api/v1"

# Routes that need no administrator: the sign-in flow. Everything under /me is the signed-in user's own space. Any route NOT in one of these
# two groups must be administrator-only; the parametrised tests below fail if someone adds a route and forgets to protect it.
PUBLIC = {("POST", f"{A}/auth/register"), ("POST", f"{A}/auth/login"), ("POST", f"{A}/auth/admin/login"), ("GET", f"{A}/auth/me"),
          ("POST", f"{A}/auth/logout"), ("GET", "/health")}


def api_routes(app):
    """Every (METHOD, path) the app serves, read from its OpenAPI document so included routers are covered too."""
    for path, ops in app.openapi()["paths"].items():
        if path.startswith(("/api/", "/health")):
            for m in ops:
                yield m.upper(), path


def is_me(path: str) -> bool:
    return path.startswith(f"{A}/me/") or path == f"{A}/me"


def fill(path: str) -> str:
    out = path
    while "{" in out:
        i, j = out.index("{"), out.index("}")
        out = out[:i] + "x" + out[j + 1:]
    return out


def send(c: TestClient, method: str, path: str):
    if path.endswith("/ingestion/file"):
        return c.request(method, fill(path), files={"file": ("a.txt", b"hello")})
    return c.request(method, fill(path), json={} if method in ("POST", "PUT", "PATCH") else None)


@pytest.fixture()
def seeded_app(seeded):
    """One app over ONE seeded context, so several independent TestClients (separate cookie jars = separate browsers) share it."""
    return create_app(seeded.settings, seeded)


def admin_only(app):
    return [(m, p) for m, p in api_routes(app) if (m, p) not in PUBLIC and not is_me(p)]


def test_the_route_surface_partitions_into_public_user_and_admin(seeded_app):
    routes = set(api_routes(seeded_app))
    assert PUBLIC <= routes
    me = {(m, p) for m, p in routes if is_me(p)}
    assert {("POST", f"{A}/me/memories"), ("GET", f"{A}/me/graph"), ("POST", f"{A}/me/query"), ("GET", f"{A}/me/queries")} <= me
    admin = admin_only(seeded_app)
    assert len(admin) >= 45
    assert ("GET", f"{A}/admin/users") in admin and ("GET", f"{A}/admin/activity") in admin and ("POST", f"{A}/query") in admin


def test_every_admin_route_rejects_a_caller_with_no_session(seeded_app):
    with TestClient(seeded_app) as anon:
        for m, p in admin_only(seeded_app):
            r = send(anon, m, p)
            assert r.status_code == 401 and r.json()["error"]["code"] == "authentication_required", (m, p, r.status_code)


def test_every_admin_route_forbids_an_ordinary_user(seeded_app):
    with TestClient(seeded_app) as user:
        register_user(user)
        assert user.cookies.get(USER_COOKIE)
        for m, p in admin_only(seeded_app):
            r = send(user, m, p)
            assert r.status_code == 403 and r.json()["error"]["code"] == "admin_required", (m, p, r.status_code)


def test_every_personal_route_requires_a_user_session(seeded_app):
    with TestClient(seeded_app) as anon, TestClient(seeded_app) as admin:
        login_admin(admin)
        for m, p in api_routes(seeded_app):
            if not is_me(p):
                continue
            r = send(anon, m, p)
            assert r.status_code == 401, (m, p, r.status_code)
            a = send(admin, m, p)  # an administrator is not a user: there is no personal memory space to act in
            assert a.status_code == 403 and a.json()["error"]["code"] == "user_account_required", (m, p, a.status_code)


def test_administrator_reaches_every_read_only_admin_route(seeded_app):
    with TestClient(seeded_app) as admin:
        login_admin(admin)
        checked = 0
        for m, p in admin_only(seeded_app):
            if m != "GET" or "{" in p:  # parameterised and mutating routes are covered by dedicated tests
                continue
            assert admin.get(p).status_code not in (401, 403), p
            checked += 1
        assert checked >= 15


def snapshot(c):
    return {"memories": c.mongo.col("memory_metadata").count_documents({}), "version": c.versions.active_id(),
            "events": c.mongo.col("adaptation_events").count_documents({}), "modules": c.mongo.col("modules").count_documents({}),
            "q": c.qdrant.count_memories(None), "user_memories": c.mongo.col("user_memories").count_documents({}),
            "user_points": c.user_vectors.client.count(c.user_vectors.memory_collection, exact=True).count}


def test_rejected_requests_never_mutate_state(seeded_app, seeded):
    before = snapshot(seeded)
    mem = seeded.mongo.col("memory_metadata").find_one({})["memory_id"]
    attempts = [
        ("POST", f"{A}/ingestion", {"source_name": "x.md", "text": "attacker supplied memory"}),
        ("DELETE", f"{A}/memories/{mem}?force=true", None), ("PATCH", f"{A}/memories/{mem}", {"pinned": True}),
        ("POST", f"{A}/compression/run", {"mode": "emergency"}), ("POST", f"{A}/adaptation/apply", {"operation": "merge_modules", "module_ids": ["a", "b"]}),
        ("POST", f"{A}/adaptation/run-window", {}), ("POST", f"{A}/versions/checkpoint", {}), ("POST", f"{A}/versions/arch_0001/restore", {}),
        ("POST", f"{A}/admin/reset", {"confirm": "RESET"}), ("POST", f"{A}/admin/demo/seed", {}),
        ("POST", f"{A}/benchmarks/run", {"datasets": ["scifact"]}), ("PUT", f"{A}/compression/budget", {"budget_tokens": 5}),
        ("POST", f"{A}/evaluation/dataset/load", {}), ("POST", f"{A}/sources/src_x/visibility", {"visibility": "shared"}),
        ("POST", f"{A}/admin/users/usr_x/status", {"status": "disabled"}),
    ]
    with TestClient(seeded_app) as user:
        register_user(user)
        for method, path, body in attempts:
            for c in (TestClient(seeded_app), user):  # one with no session at all, one with an ordinary user's session
                r = c.request(method, path, json=body)
                assert r.status_code in (401, 403), (method, path, r.status_code)
    assert snapshot(seeded) == before


def test_health_detail_is_for_administrators_only(seeded_app):
    with TestClient(seeded_app) as v, TestClient(seeded_app) as a:
        login_admin(a)
        pub, adm = v.get("/health").json(), a.get("/health").json()
    assert set(pub) == {"status", "ready"} and pub["ready"] is True
    assert "checks" in adm and "architecture_version" in adm and "environment" in adm


def test_role_headers_cookies_params_and_bodies_do_not_grant_privileges(seeded_app):
    with TestClient(seeded_app) as c:
        c.cookies.set("role", "admin")
        c.cookies.set("is_admin", "true")
        for kw in ({"headers": {"X-Role": "admin", "X-Admin": "1", "Authorization": "Bearer admin"}}, {"params": {"role": "admin", "admin": "true"}}):
            assert c.get(f"{A}/memories", **kw).status_code == 401
        assert c.post(f"{A}/ingestion", json={"source_name": "a", "text": "b", "role": "admin"}).status_code in (401, 403)
        assert c.get(f"{A}/auth/me").json()["role"] is None
    with TestClient(seeded_app) as u:
        register_user(u)
        assert u.get(f"{A}/memories", headers={"X-Role": "admin"}, params={"role": "admin"}).status_code == 403
        assert u.get(f"{A}/auth/me").json()["role"] == "user"


def test_a_user_session_never_becomes_an_admin_session(seeded_app):
    with TestClient(seeded_app) as c:
        register_user(c)
        assert c.cookies.get(ADMIN_COOKIE) is None
        assert c.get(f"{A}/versions").status_code == 403
        assert c.get(f"{A}/admin/users").status_code == 403
        c.cookies.set(ADMIN_COOKIE, c.cookies.get(USER_COOKIE))  # presenting the user token as an admin cookie
        assert c.get(f"{A}/versions").status_code == 401


def test_admin_query_playground_is_admin_only_and_complete(seeded_app):
    body = {"query": "how does django map url patterns to views", "top_k": 3, "debug_trace": True}
    with TestClient(seeded_app) as anon, TestClient(seeded_app) as admin:
        assert anon.post(f"{A}/query", json=body).status_code == 401
        login_admin(admin)
        r = admin.post(f"{A}/query", json=body)
        assert r.status_code == 200 and r.json()["architecture_version"] and "access_count" in r.json()["results"][0]
