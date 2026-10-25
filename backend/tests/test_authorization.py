"""Role-based access control exercised on the REAL routes (never just on frontend visibility)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.auth.service import ADMIN_COOKIE, USER_COOKIE
from app.main import create_app
from tests.conftest import SESSION_SECRET, login_admin
from tests.corpus import DOCS

A = "/api/v1"

# Everything a visitor (or the sign-in flow) may call. Any route NOT listed here must be administrator-only; the parametrised
# test below fails if someone adds a new route and forgets to protect it.
PUBLIC = {
    ("POST", f"{A}/query"), ("GET", f"{A}/me/queries"), ("GET", f"{A}/me/queries/{{query_id}}"),
    ("GET", f"{A}/explore/graph"), ("GET", f"{A}/explore/modules"), ("GET", f"{A}/explore/modules/{{module_id}}"),
    ("POST", f"{A}/auth/login"), ("GET", f"{A}/auth/session"), ("POST", f"{A}/auth/logout"), ("GET", "/health"),
}


def api_routes(app):
    """Every (METHOD, path) the app serves, read from its OpenAPI document so included routers are covered too."""
    for path, ops in app.openapi()["paths"].items():
        if path.startswith(("/api/", "/health")):
            for m in ops:
                yield m.upper(), path


@pytest.fixture()
def seeded_app(seeded):
    """One app over ONE seeded context, so several independent TestClients (separate cookie jars = separate browsers) share it."""
    return create_app(seeded.settings, seeded)


def setck(c: TestClient, name: str, value: str) -> None:
    """Replace a cookie (httpx would otherwise send the old one next to the forged one)."""
    c.cookies.delete(name)
    c.cookies.set(name, value)


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


def test_the_public_surface_is_exactly_what_is_allowed(seeded_app):
    protected = [(m, p) for m, p in api_routes(seeded_app) if (m, p) not in PUBLIC]
    assert len(protected) >= 40  # every administrative endpoint is covered by the parametrised tests below
    assert PUBLIC <= set(api_routes(seeded_app)) | {("GET", "/health")}


def test_every_non_public_route_rejects_a_caller_with_no_session(seeded_app):
    with TestClient(seeded_app) as anon:
        for m, p in api_routes(seeded_app):
            if (m, p) in PUBLIC:
                continue
            r = send(anon, m, p)
            assert r.status_code == 401 and r.json()["error"]["code"] == "authentication_required", (m, p, r.status_code)


def test_every_non_public_route_forbids_an_ordinary_visitor(seeded_app):
    with TestClient(seeded_app) as visitor:
        assert visitor.post(f"{A}/query", json={"query": "git stash"}).status_code == 200  # becomes a known visitor
        assert visitor.cookies.get(USER_COOKIE)
        for m, p in api_routes(seeded_app):
            if (m, p) in PUBLIC:
                continue
            r = send(visitor, m, p)
            assert r.status_code == 403 and r.json()["error"]["code"] == "admin_required", (m, p, r.status_code)


def test_administrator_reaches_every_read_only_admin_route(seeded_app):
    with TestClient(seeded_app) as admin:
        login_admin(admin)
        checked = 0
        for m, p in api_routes(seeded_app):
            if (m, p) in PUBLIC or m != "GET" or "{" in p:  # parameterised and mutating routes are covered by the dedicated tests
                continue
            assert admin.get(p).status_code not in (401, 403), p
            checked += 1
        assert checked >= 15


def snapshot(c):
    return {"memories": c.mongo.col("memory_metadata").count_documents({}), "version": c.versions.active_id(),
            "events": c.mongo.col("adaptation_events").count_documents({}), "modules": c.mongo.col("modules").count_documents({}),
            "q": c.qdrant.count_memories(None)}


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
        ("POST", f"{A}/evaluation/dataset/load", {}),
    ]
    with TestClient(seeded_app) as visitor:
        visitor.get(f"{A}/auth/session")
        for method, path, body in attempts:
            for c in (TestClient(seeded_app), visitor):  # one with no session at all, one with a visitor session
                r = c.request(method, path, json=body)
                assert r.status_code in (401, 403), (method, path, r.status_code)
    assert snapshot(seeded) == before


def test_visitors_can_still_query_and_inspect_the_graph(seeded_app):
    with TestClient(seeded_app) as v:
        q = v.post(f"{A}/query", json={"query": "how does django map url patterns to views", "top_k": 3, "debug_trace": True})
        assert q.status_code == 200 and q.json()["results"][0]["module_id"] == "mod_django"
        assert v.get(f"{A}/explore/graph").status_code == 200 and v.get(f"{A}/explore/modules").status_code == 200
        assert v.get(f"{A}/explore/modules/mod_django").status_code == 200
        assert v.get(f"{A}/explore/modules/nope").status_code == 404
        assert v.get("/health").status_code == 200


def test_visitor_responses_are_sanitised_and_admin_responses_stay_complete(seeded_app):
    body = {"query": "how does django map url patterns to views", "top_k": 3, "debug_trace": True}
    with TestClient(seeded_app) as v, TestClient(seeded_app) as a:
        login_admin(a)
        u, adm = v.post(f"{A}/query", json=body).json(), a.post(f"{A}/query", json=body).json()
    assert u["architecture_version"] is None and adm["architecture_version"]
    for r in u["results"]:
        assert set(r) == {"memory_id", "text", "score", "module_id", "module_name", "source_name", "section"}
    assert "access_count" in adm["results"][0] and "source_id" in adm["results"][0]
    assert "guided_result_ids" not in u["trace"] and "guided_result_ids" in adm["trace"]
    assert u["trace"]["seed_modules"] and u["trace"]["candidate_count"] > 0  # the route explanation itself is kept


def test_visitor_top_k_is_capped_but_administrators_may_ask_for_more(seeded_app):
    with TestClient(seeded_app) as v, TestClient(seeded_app) as a:
        r = v.post(f"{A}/query", json={"query": "git stash", "top_k": 30})
        assert r.status_code == 422 and r.json()["error"]["code"] == "top_k_too_large"
        assert v.post(f"{A}/query", json={"query": "git stash", "top_k": 20}).status_code == 200
        login_admin(a)
        assert a.post(f"{A}/query", json={"query": "git stash", "top_k": 30}).status_code == 200


def test_query_history_is_private_to_the_session_that_asked(seeded_app):
    with TestClient(seeded_app) as alice, TestClient(seeded_app) as bob:
        qa = alice.post(f"{A}/query", json={"query": "alice private question about redis streams"}).json()
        bob.post(f"{A}/query", json={"query": "bob question about git stash"})
        mine, theirs = alice.get(f"{A}/me/queries").json()["items"], bob.get(f"{A}/me/queries").json()["items"]
        assert [i["query"] for i in mine] == ["alice private question about redis streams"]
        assert [i["query"] for i in theirs] == ["bob question about git stash"]
        assert alice.get(f"{A}/me/queries/{qa['query_id']}").json()["results"] == qa["results"]
        r = bob.get(f"{A}/me/queries/{qa['query_id']}")  # Bob asking for Alice's result by id
        assert r.status_code == 404 and "alice" not in r.text
        # a stored result carries no operational detail
        assert "access_count" not in json.dumps(alice.get(f"{A}/me/queries/{qa['query_id']}").json())


def test_a_visitor_cannot_choose_or_forge_a_session_identifier(seeded_app, settings):
    with TestClient(seeded_app) as alice, TestClient(seeded_app) as mallory:
        qa = alice.post(f"{A}/query", json={"query": "alice secret topic redis"}).json()
        alice_cookie = alice.cookies.get(USER_COOKIE)
        # client-supplied identifiers in headers / query strings / bodies are ignored
        r = mallory.get(f"{A}/me/queries", headers={"X-Session-Id": "alice", "X-Visitor": alice_cookie}, params={"session_id": "alice", "sid": alice_cookie})
        assert r.json()["items"] == []
        # a tampered or foreign-signed visitor cookie is not accepted as anyone's session
        body, sig = alice_cookie.split(".")
        flip = lambda t: ("B" if t[0] == "A" else "A") + t[1:]
        for forged in (f"{body}.{flip(sig)}", "usr_x.y", f"{flip(body)}.{sig}"):
            setck(mallory, USER_COOKIE, forged)
            assert mallory.get(f"{A}/me/queries/{qa['query_id']}").status_code == 404
            assert mallory.get(f"{A}/me/queries").json()["items"] == []
        # the real owner still sees it
        assert alice.get(f"{A}/me/queries/{qa['query_id']}").status_code == 200


def test_query_events_record_the_session_and_actor(seeded_app, seeded):
    with TestClient(seeded_app) as v, TestClient(seeded_app) as a:
        v.post(f"{A}/query", json={"query": "visitor asks about django urls"})
        login_admin(a)
        a.post(f"{A}/query", json={"query": "admin asks about django urls"})
    evs = list(seeded.mongo.col("query_events").find({"query_preview": {"$regex": "asks about django"}}))
    assert {e["actor"] for e in evs} == {"user", "admin"} and len({e["session_id"] for e in evs}) == 2


def test_graph_view_for_visitors_hides_operational_detail_and_memory_text(seeded_app):
    with TestClient(seeded_app) as v, TestClient(seeded_app) as a:
        login_admin(a)
        texts = [m["text"] for m in (a.get(f"{A}/memories/{i['memory_id']}").json() for i in a.get(f"{A}/memories", params={"limit": 50}).json()["items"])]
        g = v.get(f"{A}/explore/graph").json()
        mods = v.get(f"{A}/explore/modules").json()
        det = v.get(f"{A}/explore/modules/mod_django").json()
        admin_graph = a.get(f"{A}/graph").json()
    blob = json.dumps([g, mods, det])
    assert texts and not any(t[:60] in blob for t in texts)
    assert g["architecture_version"] is None and "capacity_threshold" not in g
    assert all(n["data"]["lastChangeOp"] is None and n["data"]["utilization"] is None for n in g["nodes"])
    assert all(e["data"]["evidence"] == {} and e["data"]["operationId"] is None for e in g["edges"])
    assert {n["id"] for n in g["nodes"]} == {n["id"] for n in admin_graph["nodes"]}  # same real structure
    for forbidden in ("memories", "adaptation_history", "total_access_count", "last_accessed_at", "token_count", "queries_touching_module", "created_at", "last_change_op"):
        assert forbidden not in det and all(forbidden not in m for m in mods["items"]), forbidden
    assert det["parent"]["module_id"] == "mod_web" and det["neighbors"]


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
        r = c.post(f"{A}/query", json={"query": "git stash", "role": "admin", "debug_trace": True}, headers={"X-Role": "admin"})
        assert r.status_code == 200 and "access_count" not in json.dumps(r.json()) and r.json()["architecture_version"] is None
        assert c.post(f"{A}/ingestion", json={"source_name": "a", "text": "b", "role": "admin"}).status_code in (401, 403)


def test_visitor_session_never_becomes_an_admin_session(seeded_app):
    with TestClient(seeded_app) as c:
        c.post(f"{A}/query", json={"query": "git stash"})
        assert c.get(f"{A}/auth/session").json()["role"] == "user"
        assert c.cookies.get(ADMIN_COOKIE) is None
        assert c.get(f"{A}/versions").status_code == 403


def test_admin_query_without_csrf_is_treated_as_a_visitor_query(seeded_app):
    with TestClient(seeded_app) as a:
        login_admin(a)
        del a.headers["X-CSRF-Token"]  # a cross-site POST rides the cookie but cannot know the token
        r = a.post(f"{A}/query", json={"query": "git stash", "debug_trace": True})
        assert r.status_code == 200 and r.json()["architecture_version"] is None and "access_count" not in json.dumps(r.json())
