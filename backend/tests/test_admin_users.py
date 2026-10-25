"""Administrator Users + Activity: what is recorded, what is visible, and what stays private. Plus audit-log hygiene."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.audit.service import AuditService, scrub
from app.common import iso
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD
from tests.test_isolation import ALICE_Q, ALICE_TEXT, BOB_Q, BOB_TEXT, ask, save

A = "/api/v1"


@pytest.fixture()
def busy(world):
    """Alice and Bob each saved a memory and asked questions; Bob also had a failed login. An administrator is signed in."""
    a, b = world.user("Alice Aardvark", "alice@example.com"), world.user("Bob Badger", "bob@example.com")
    world.a, world.b = a, b
    world.aid, world.bid = (c.get(f"{A}/auth/me").json()["user"]["user_id"] for c in (a, b))
    world.sa, world.sb = save(a, ALICE_TEXT, title="Zephyr"), save(b, BOB_TEXT, title="Bakery")
    ask(a, ALICE_Q, "graph")
    ask(a, ALICE_Q, "soft")
    ask(b, BOB_Q, "progressive")
    assert world.browser().post(f"{A}/auth/login", json={"email": "bob@example.com", "password": "not-his-password-1"}).status_code == 401
    assert b.post(f"{A}/me/query", json={"query": "too many", "top_k": 40}).status_code == 422  # a failed query (over the user limit)
    world.admin_client = world.admin()
    return world


def events(world, **q):
    return world.admin_client.get(f"{A}/admin/activity", params={"limit": 200, **q}).json()


def test_users_page_lists_accounts_with_real_counts(busy):
    r = busy.admin_client.get(f"{A}/admin/users").json()
    assert r["total"] == 2
    by = {u["email"]: u for u in r["items"]}
    alice, bob = by["alice@example.com"], by["bob@example.com"]
    assert alice["user_id"] == busy.aid and alice["status"] == "active" and alice["created_at"] and alice["last_login_at"] and alice["last_activity_at"]
    assert alice["stats"]["private_memories"] == len(busy.sa["memory_ids"]) and alice["stats"]["personal_modules"] == len(busy.sa["module_ids"])
    assert alice["stats"]["queries_submitted"] == 2 and alice["stats"]["queries_succeeded"] == 2 and alice["stats"]["queries_failed"] == 0
    assert alice["stats"]["memory_saves"] == 1 and alice["stats"]["failed_operations"] == 0
    assert bob["stats"]["queries_submitted"] == 2 and bob["stats"]["queries_succeeded"] == 1 and bob["stats"]["queries_failed"] == 1
    assert bob["stats"]["failed_logins"] == 1 and bob["stats"]["failed_operations"] == 1
    assert "password_hash" not in json.dumps(r) and "email_norm" not in json.dumps(r) and USER_PASSWORD not in json.dumps(r)


def test_users_page_search_status_and_activity_filters(busy):
    g = lambda **p: busy.admin_client.get(f"{A}/admin/users", params=p).json()
    assert [u["email"] for u in g(search="badger")["items"]] == ["bob@example.com"]
    assert [u["email"] for u in g(search="ALICE@")["items"]] == ["alice@example.com"]
    assert g(search=busy.aid[-8:])["total"] == 1
    assert g(status="disabled")["total"] == 0 and g(status="active")["total"] == 2
    assert g(active_after=iso(datetime.now(timezone.utc) + timedelta(days=1)))["total"] == 0
    assert g(active_after=iso(datetime.now(timezone.utc) - timedelta(days=1)))["total"] == 2
    page = g(limit=1, offset=0)
    assert page["total"] == 2 and len(page["items"]) == 1 and g(limit=1, offset=1)["items"][0]["user_id"] != page["items"][0]["user_id"]


def test_user_detail_and_activity(busy):
    d = busy.admin_client.get(f"{A}/admin/users/{busy.aid}").json()
    assert d["email"] == "alice@example.com" and d["recent_activity"] and all(e["subject_user_id"] == busy.aid for e in d["recent_activity"])
    act = busy.admin_client.get(f"{A}/admin/users/{busy.aid}/activity").json()
    assert {"auth.registered", "memory.created", "query.submitted", "query.completed"} <= {e["action"] for e in act["items"]}
    assert busy.admin_client.get(f"{A}/admin/users/usr_nope").status_code == 404


def test_activity_records_each_meaningful_event_with_the_right_actor(busy):
    ev = events(busy)["items"]
    actions = {(e["action"], e["status"]) for e in ev}
    for want in (("auth.registered", "success"), ("auth.login", "failure"), ("memory.created", "success"), ("query.submitted", "success"),
                 ("query.completed", "success"), ("query.completed", "failure"), ("admin.login", "success")):
        assert want in actions, want
    for e in ev:
        assert e["event_id"].startswith("evt_") and e["timestamp"] and e["request_id"]
        if e["action"].startswith("query."):
            assert e["actor_role"] == "user" and e["actor_user_id"] in (busy.aid, busy.bid) and e["subject_user_id"] == e["actor_user_id"]
    completed = [e for e in ev if e["action"] == "query.completed" and e["status"] == "success"]
    mine = [e for e in completed if e["actor_user_id"] == busy.aid]
    assert len(mine) == 2 and {e["metadata"]["strategy"] for e in mine} == {"graph", "soft"}
    assert all(e["metadata"]["result_count"] >= 1 and e["metadata"]["latency_ms"] >= 0 and e["metadata"]["query"] == ALICE_Q[:300] for e in mine)
    fail = next(e for e in ev if e["action"] == "query.completed" and e["status"] == "failure")
    assert fail["actor_user_id"] == busy.bid and fail["metadata"]["error_code"] == "top_k_out_of_range"
    login_fail = next(e for e in ev if e["action"] == "auth.login" and e["status"] == "failure")
    assert login_fail["subject_user_id"] == busy.bid and login_fail["metadata"]["error_code"] == "invalid_credentials"


def test_activity_filters_user_action_status_date_and_target(busy):
    only_alice = events(busy, user_id=busy.aid)["items"]
    assert only_alice and all(e["subject_user_id"] == busy.aid for e in only_alice)
    q = events(busy, action="query")["items"]
    assert q and all(e["action"].startswith("query.") for e in q)
    assert {e["action"] for e in events(busy, action="query.submitted")["items"]} == {"query.submitted"}
    fails = events(busy, status="failure")["items"]
    assert fails and all(e["status"] == "failure" for e in fails)
    assert events(busy, role="admin")["items"] and all(e["actor_role"] == "admin" for e in events(busy, role="admin")["items"])
    assert events(busy, target_type="source")["items"] and all(e["target_type"] == "source" for e in events(busy, target_type="source")["items"])
    now = datetime.now(timezone.utc)
    assert events(busy, since=iso(now + timedelta(hours=1)))["total"] == 0
    assert events(busy, since=iso(now - timedelta(hours=1)), until=iso(now + timedelta(hours=1)))["total"] > 0
    assert events(busy, search="progressive")["total"] >= 1
    first = events(busy, limit=3)
    nxt = events(busy, limit=3, offset=3)
    assert len(first["items"]) == 3 and first["total"] == nxt["total"] and not {e["event_id"] for e in first["items"]} & {e["event_id"] for e in nxt["items"]}
    # newest first
    stamps = [e["timestamp"] for e in events(busy)["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert "query.completed" in busy.admin_client.get(f"{A}/admin/activity/actions").json()["items"]
    one = first["items"][0]
    assert busy.admin_client.get(f"{A}/admin/activity/{one['event_id']}").json()["event_id"] == one["event_id"]
    assert busy.admin_client.get(f"{A}/admin/activity/evt_missing").status_code == 404


def test_the_audit_log_never_contains_secrets_tokens_or_memory_text(busy):
    blob = json.dumps(list(busy.ctx.mongo.col("audit_events").find({}, {"_id": 0})), default=str)
    for secret in (USER_PASSWORD, ADMIN_PASSWORD, "not-his-password-1", busy.settings.session_secret, "argon2", "password_hash"):
        assert secret not in blob, secret
    for csrf in (busy.a.headers["X-CSRF-Token"], busy.b.headers["X-CSRF-Token"], busy.admin_client.headers["X-CSRF-Token"]):
        assert csrf not in blob
    assert "vault key stored in the zebra rack" not in blob and "sourdough starter feeding schedule" not in blob  # saved memory text is not logged
    assert "embedding" not in blob.lower() or "embedding_ms" in blob.lower()


def test_scrub_removes_secret_keys_and_bounds_size():
    out = scrub({"password": "x", "Authorization": "Bearer y", "csrf_token": "z", "api_key": "k", "ok": "fine", "nested": {"token": "t", "n": 1},
                 "long": "a" * 1000, "list": list(range(500)), "vector": [0.1] * 384})
    assert set(out) == {"ok", "nested", "long", "list"} and out["nested"] == {"n": 1} and len(out["long"]) == 300 and len(out["list"]) == 50


def test_admin_actions_are_themselves_audited(busy):
    c = busy.admin_client
    assert c.post(f"{A}/versions/checkpoint").status_code == 200
    src = busy.ctx.ingestion.ingest("x.md", "Some administrator content about kubernetes pods and nodes.", {"topic": "k8s"})
    assert c.post(f"{A}/sources/{src['source_id']}/visibility", json={"visibility": "shared"}).status_code == 200
    assert c.post(f"{A}/admin/users/{busy.bid}/status", json={"status": "disabled", "reason": "testing"}).status_code == 200
    assert c.get(f"{A}/admin/users/{busy.aid}/memories", params={"reason": "support ticket 4711"}).status_code == 200
    assert busy.browser().post(f"{A}/versions/checkpoint").status_code == 401  # not signed in: nothing recorded as an admin action
    ev = events(busy, role="admin")["items"]
    names = {(e["action"], e["status"]) for e in ev}
    assert ("version.checkpoint", "success") in names and ("source.visibility_changed", "success") in names
    assert ("user.disabled", "success") in names and ("admin.user_memories_accessed", "success") in names
    cp = next(e for e in ev if e["action"] == "version.checkpoint")
    assert cp["actor_label"] == "test-admin" and cp["target_id"] == "/versions/checkpoint" and cp["metadata"]["status_code"] == 200
    access = next(e for e in ev if e["action"] == "admin.user_memories_accessed")
    assert access["subject_user_id"] == busy.aid and access["metadata"]["reason"] == "support ticket 4711"
    disabled = next(e for e in ev if e["action"] == "user.disabled")
    assert disabled["subject_user_id"] == busy.bid and disabled["metadata"]["reason"] == "testing" and disabled["metadata"]["sessions_revoked"] >= 1
    # a failed administrator write is recorded as such
    assert c.post(f"{A}/versions/arch_nope/restore").status_code in (404, 422, 409)
    failed = [e for e in events(busy, role="admin", status="failure")["items"] if e["action"] == "version.restored"]
    assert failed and failed[0]["metadata"]["status_code"] >= 400


def test_admins_see_questions_but_not_memory_content_unless_they_ask_and_say_why(busy):
    c = busy.admin_client
    q = c.get(f"{A}/admin/users/{busy.aid}/queries").json()
    assert q["total"] == 2 and {i["query"] for i in q["items"]} == {ALICE_Q}
    qid = q["items"][0]["query_id"]
    detail = c.get(f"{A}/admin/users/{busy.aid}/queries/{qid}").json()
    assert detail["query"] == ALICE_Q and detail["results"] and detail["user_id"] == busy.aid
    blob = json.dumps(detail)
    assert "rotating" not in blob and "text" not in {k for r in detail["results"] for k in r}  # result metadata only, no retrieved text
    # an admin cannot read user A's query through user B's page
    assert c.get(f"{A}/admin/users/{busy.bid}/queries/{qid}").status_code == 404
    # opening content needs an explicit reason, and is logged
    assert c.get(f"{A}/admin/users/{busy.aid}/memories").status_code == 422
    assert c.get(f"{A}/admin/users/{busy.aid}/memories", params={"reason": "short"}).status_code == 422
    ok = c.get(f"{A}/admin/users/{busy.aid}/memories", params={"reason": "moderation review of a report"})
    assert ok.status_code == 200 and "rotating vault key" in json.dumps(ok.json())
    assert any(e["action"] == "admin.user_memories_accessed" for e in events(busy)["items"])
    # the plain user detail / list never include content
    assert "rotating" not in json.dumps(c.get(f"{A}/admin/users/{busy.aid}").json()) and "rotating" not in json.dumps(c.get(f"{A}/admin/users").json())


def test_users_cannot_read_each_others_history_or_the_admin_log(busy):
    qa = busy.a.get(f"{A}/me/queries").json()["items"]
    qb = busy.b.get(f"{A}/me/queries").json()["items"]
    assert {i["query"] for i in qa} == {ALICE_Q} and BOB_Q in {i["query"] for i in qb}
    assert busy.b.get(f"{A}/me/queries/{qa[0]['query_id']}").status_code == 404
    assert busy.a.get(f"{A}/admin/activity").status_code == 403


def test_group_actions_are_audited_logout_and_deletion(busy):
    busy.a.post(f"{A}/me/graph".replace("/graph", "/memories") + "/x", json={})  # noise: not found
    mem = busy.sa["memory_ids"][0]
    assert busy.a.delete(f"{A}/me/memories/{mem}").status_code == 200
    assert busy.a.delete(f"{A}/me/memories/{mem}").status_code == 404  # second time fails and is recorded as a failure
    busy.a.get(f"{A}/me/graph")
    assert busy.a.post(f"{A}/auth/logout").status_code == 200
    ev = busy.admin_client.get(f"{A}/admin/users/{busy.aid}/activity", params={"limit": 200}).json()["items"]
    st = {(e["action"], e["status"]) for e in ev}
    assert ("memory.deleted", "success") in st and ("memory.deleted", "failure") in st and ("graph.viewed", "success") in st and ("auth.logout", "success") in st
    summary = busy.admin_client.get(f"{A}/admin/users/{busy.aid}").json()["stats"]
    assert summary["memory_deletions"] == 1 and summary["failed_operations"] == 1 and summary["private_memories"] == len(busy.sa["memory_ids"]) - 1


def test_audit_retention_ttl_index_and_purge(world):
    audit: AuditService = world.ctx.audit
    keys = [v for v in world.ctx.mongo.col("audit_events").index_information().values()]
    assert any(v.get("expireAfterSeconds") == 0 for v in keys)  # MongoDB expires events itself (retention is configurable)
    old = audit.record("auth.login", actor_user_id="usr_old", actor_role="user")
    world.ctx.mongo.col("audit_events").update_one({"event_id": old["event_id"]}, {"$set": {"timestamp": iso(datetime.now(timezone.utc) - timedelta(days=400))}})
    fresh = audit.record("auth.login", actor_user_id="usr_new", actor_role="user")
    assert audit.purge(365) == 1 and audit.get(old["event_id"]) is None and audit.get(fresh["event_id"])
    assert audit.purge(0) == 0


def test_a_write_failure_in_the_audit_log_never_breaks_the_request(world, monkeypatch):
    u = world.user("Alice Aardvark", "alice@example.com")
    monkeypatch.setattr(world.ctx.audit.col, "insert_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit store down")))
    assert u.get(f"{A}/me/graph").status_code == 200
    assert u.post(f"{A}/me/memories", json={"text": ALICE_TEXT}).status_code == 201


def test_deleting_an_account_removes_its_personal_data_but_keeps_the_audit_trail(busy):
    c = busy.admin_client
    assert c.delete(f"{A}/admin/users/{busy.aid}").status_code == 422  # explicit confirmation required
    assert c.delete(f"{A}/admin/users/{busy.aid}", params={"confirm": "nope"}).status_code == 422
    assert busy.ctx.user_data.stats(busy.aid)["memories"] > 0
    r = c.delete(f"{A}/admin/users/{busy.aid}", params={"confirm": "DELETE"})
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert c.get(f"{A}/admin/users/{busy.aid}").status_code == 404
    assert busy.ctx.user_data.stats(busy.aid) == {"memories": 0, "modules": 0, "sources": 0, "tokens": 0}
    assert busy.ctx.user_vectors.count_memories(busy.aid) == 0 and busy.ctx.user_vectors.count_modules(busy.aid) == 0
    assert busy.ctx.mongo.col("user_query_results").count_documents({"owner_user_id": busy.aid}) == 0
    assert busy.a.get(f"{A}/me/graph").status_code == 401  # its sessions are gone
    assert busy.b.get(f"{A}/me/memories").json()["total"] == len(busy.sb["memory_ids"])  # other users untouched
    ev = events(busy, user_id=busy.aid)["items"]
    assert any(e["action"] == "user.deleted" for e in ev) and any(e["action"] == "memory.created" for e in ev)
