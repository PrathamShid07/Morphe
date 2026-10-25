"""Cross-user isolation. These tests drive the real retrieval engine, the real Qdrant payload filters, the real MongoDB ownership queries and the
real routes. Only the external boundaries are replaced (hashing embedder, in-memory Qdrant, mongomock); no authorization logic is mocked."""
from __future__ import annotations

import pytest

from app.memory.personal import PersonalMemoryService
from app.retrieval.scoped import ScopedStore, build_scope
from app.storage.user_plane import UserVectorStore, owner_token
from tests.conftest import register_user
from tests.corpus import DOCS

A = "/api/v1"
MODES = ["global", "graph", "progressive", "soft"]
ALICE_TEXT = ("Alice private project Zephyr: the quartz-nebula deployment uses a rotating vault key stored in the zebra rack. "
              "Zephyr launch codes are discussed only in the quartz-nebula channel.")
BOB_TEXT = ("Bob private notes about the Orion bakery: sourdough starter feeding schedule, rye flour supplier contacts and the oven maintenance log. "
            "Orion bakery wholesale prices are negotiated every March.")
ALICE_Q = "quartz-nebula deployment vault key zebra rack Zephyr launch codes"
BOB_Q = "Orion bakery sourdough starter rye flour supplier oven maintenance"


def save(c, text, **kw):
    r = c.post(f"{A}/me/memories", json={"text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def ask(c, q, mode="graph", top_k=10):
    r = c.post(f"{A}/me/query", json={"query": q, "mode": mode, "top_k": top_k, "debug_trace": True})
    assert r.status_code == 200, r.text
    return r.json()


def publish_core(ctx, names=("django", "postgres", "docker")):
    out = {}
    for n in names:
        d = DOCS[n]
        out[n] = ctx.ingestion.ingest(d["source_name"], d["text"], d["metadata"], visibility="shared")
    return out


@pytest.fixture()
def two(world):
    """Two independent browsers, each with a private memory of their own."""
    a = world.user("Alice Aardvark", "alice@example.com")
    b = world.user("Bob Badger", "bob@example.com")
    sa, sb = save(a, ALICE_TEXT, title="Project Zephyr"), save(b, BOB_TEXT, title="Orion bakery")
    world.alice, world.bob, world.sa, world.sb = a, b, sa, sb
    world.alice_id = a.get(f"{A}/auth/me").json()["user"]["user_id"]
    world.bob_id = b.get(f"{A}/auth/me").json()["user"]["user_id"]
    return world


# ------------------------------------------------------------------------------------------------------ 1 + 2: own memory works
def test_each_user_saves_and_retrieves_their_own_memory(two):
    ra, rb = ask(two.alice, ALICE_Q), ask(two.bob, BOB_Q)
    assert ra["results"] and ra["results"][0]["memory_id"] in two.sa["memory_ids"] and ra["results"][0]["scope"] == "personal"
    assert rb["results"] and rb["results"][0]["memory_id"] in two.sb["memory_ids"] and rb["results"][0]["scope"] == "personal"
    assert ra["memory_scope"]["personal_records"] == len(two.sa["memory_ids"]) and not ra["memory_scope"]["empty"]


# -------------------------------------------------- 3, 4, 7, 8, 9: private data never leaks, under every strategy and fallback
@pytest.mark.parametrize("mode", MODES)
def test_a_user_cannot_retrieve_another_users_private_memory(two, mode):
    for asker, question, victim_ids in ((two.alice, BOB_Q, two.sb["memory_ids"]), (two.bob, ALICE_Q, two.sa["memory_ids"])):
        r = ask(asker, question, mode)  # the question matches the OTHER user's memory perfectly
        returned = {x["memory_id"] for x in r["results"]}
        assert not returned & set(victim_ids), (mode, returned)
        assert all(x["scope"] in ("personal", "shared") for x in r["results"])
        blob = str(r)
        assert "Orion bakery" not in blob if asker is two.alice else "quartz-nebula" not in blob  # not even in a trace or module name


@pytest.mark.parametrize("mode", MODES)
def test_global_fallback_stays_inside_the_callers_scope(two, mode):
    """Alice has several unrelated modules and the question matches none of them, so guided routing is weak and the fallback runs - it must still
    only search Alice's scope (never the whole collection, which holds Bob's perfectly matching memory)."""
    for topic, text in (("Gardening", "Tomato seedlings need six hours of sun and weekly watering."), ("Chess", "The Sicilian defence answers e4 with c5 and fights for the centre."),
                        ("Cooking", "Risotto needs warm stock added slowly while stirring constantly.")):
        save(two.alice, text, topic=topic)
    mine = {n["id"] for n in two.alice.get(f"{A}/me/graph").json()["nodes"]}
    assert len(mine) >= 4
    r = ask(two.alice, BOB_Q, mode, top_k=5)
    assert not {x["memory_id"] for x in r["results"]} & set(two.sb["memory_ids"])
    assert all(x["memory_id"].startswith(f"umem_{owner_token(two.alice_id)}_") for x in r["results"])
    if mode == "graph":
        assert r["trace"]["fallback_used"] is True  # the fallback path really executed
    if mode == "progressive":  # with so few modules expansion legitimately covers the whole personal scope; it never goes beyond it
        assert r["trace"]["progressive"]["modules_searched"] <= len(mine) and r["trace"]["progressive"]["modules_total"] == len(mine)
    for m in r["trace"].get("searched_modules", []) + r["trace"].get("seed_modules", []):
        assert m["module_id"] in mine, m


def test_progressive_budget_fallback_is_scoped_too(two, monkeypatch):
    """Force progressive expansion to exhaust its budget with an unsettled top-k, which triggers the global fallback."""
    for i, topic in enumerate(("Gardening", "Chess", "Cooking", "Sailing", "Pottery")):
        save(two.alice, f"{topic} note number {i}: tips about {topic.lower()} that have nothing to do with bakeries or vaults.", topic=topic)
    monkeypatch.setattr(two.ctx.settings, "progressive_max_modules", 1)
    monkeypatch.setattr(two.ctx.settings, "progressive_seed_modules", 1)
    r = ask(two.alice, BOB_Q, "progressive", top_k=5)
    assert r["trace"]["fallback_used"] is True, r["trace"]["progressive"]
    assert not {x["memory_id"] for x in r["results"]} & set(two.sb["memory_ids"])
    assert all(x["memory_id"].startswith(f"umem_{owner_token(two.alice_id)}_") for x in r["results"])
    assert "Orion" not in str(r)


def test_traces_and_graph_expose_only_the_callers_modules(two):
    ids_b = set(two.sb["module_ids"])
    for mode in MODES:
        t = ask(two.alice, BOB_Q, mode)["trace"]
        used = {m["module_id"] for m in t.get("seed_modules", []) + t.get("candidate_modules", []) + t.get("searched_modules", [])} | set(t["result_modules"])
        assert not used & ids_b, mode
    ga = two.alice.get(f"{A}/me/graph").json()
    gb = two.bob.get(f"{A}/me/graph").json()
    na, nb = {n["id"] for n in ga["nodes"]}, {n["id"] for n in gb["nodes"]}
    assert na and nb and not na & nb
    assert na == set(two.sa["module_ids"]) and nb == set(two.sb["module_ids"])
    assert "Orion" not in str(ga) and "Zephyr" not in str(gb) and "zephyr" not in str(gb).lower()


# --------------------------------------------------------------------------------------------- 5, 6, 10, 11, 12: no way around the owner
def test_ids_of_other_users_objects_grant_nothing(two):
    victim_mem = two.sb["memory_ids"][0]
    victim_mod = two.sb["module_ids"][0]
    victim_q = ask(two.bob, BOB_Q)["query_id"]
    # guessing / knowing another user's memory id, module id or query id
    r = two.alice.delete(f"{A}/me/memories/{victim_mem}")
    assert r.status_code == 404 and "bob" not in r.text.lower()
    assert two.alice.get(f"{A}/me/modules/{victim_mod}").status_code == 404
    assert two.alice.get(f"{A}/me/queries/{victim_q}").status_code == 404
    nonexistent = two.alice.get(f"{A}/me/queries/qry_does_not_exist")
    assert nonexistent.status_code == 404 and nonexistent.json()["error"]["message"] == two.alice.get(f"{A}/me/queries/{victim_q}").json()["error"]["message"]
    # browsing filters cannot be pointed at someone else's module
    listing = two.alice.get(f"{A}/me/memories", params={"module_id": victim_mod}).json()
    assert listing["total"] == 0 and not listing["items"]
    assert all(i["memory_id"] in two.sa["memory_ids"] for i in two.alice.get(f"{A}/me/memories").json()["items"])
    # Bob's memory is untouched
    assert two.bob.get(f"{A}/me/memories").json()["total"] == len(two.sb["memory_ids"])
    assert two.ctx.user_data.get_memory(two.bob_id, victim_mem)["status"] == "active"


def test_owner_hints_in_requests_headers_and_params_are_rejected_or_ignored(two):
    # a body cannot carry an owner, a visibility or a role
    for extra in ({"owner_user_id": two.bob_id}, {"user_id": two.bob_id}, {"visibility": "shared"}, {"role": "admin"}):
        r = two.alice.post(f"{A}/me/memories", json={"text": "planted in someone else's memory", **extra})
        assert r.status_code == 422, extra
    assert two.alice.post(f"{A}/me/query", json={"query": BOB_Q, "owner_user_id": two.bob_id}).status_code == 422
    # headers / query strings naming another user change nothing
    hdr = {"X-User-Id": two.bob_id, "X-Owner": two.bob_id, "X-Impersonate": two.bob_id}
    r = two.alice.get(f"{A}/me/memories", headers=hdr, params={"owner_user_id": two.bob_id, "user_id": two.bob_id})
    assert {i["memory_id"] for i in r.json()["items"]} <= set(two.sa["memory_ids"])
    r = two.alice.post(f"{A}/me/query", json={"query": BOB_Q}, headers=hdr)
    assert not {x["memory_id"] for x in r.json()["results"]} & set(two.sb["memory_ids"])
    # the memory a user saves is owned by the SESSION's user, whatever the request said
    doc = two.ctx.user_data.get_memory(two.alice_id, two.sa["memory_ids"][0])
    assert doc["owner_user_id"] == two.alice_id and doc["visibility"] == "private"


def test_users_cannot_ingest_modify_or_delete_shared_or_administrator_data(two):
    publish_core(two.ctx, ("django",))
    mem = two.ctx.mongo.col("memory_metadata").find_one({})["memory_id"]
    for call in (lambda c: c.post(f"{A}/ingestion", json={"source_name": "x.md", "text": "hello world text"}),
                 lambda c: c.patch(f"{A}/memories/{mem}", json={"pinned": True}), lambda c: c.delete(f"{A}/memories/{mem}?force=true"),
                 lambda c: c.post(f"{A}/versions/checkpoint"), lambda c: c.post(f"{A}/compression/run", json={}),
                 lambda c: c.get(f"{A}/admin/users"), lambda c: c.get(f"{A}/admin/activity"), lambda c: c.get(f"{A}/admin/users/{two.bob_id}/activity"),
                 lambda c: c.get(f"{A}/admin/users/{two.bob_id}/memories", params={"reason": "just curious about this"})):
        assert call(two.alice).status_code == 403
    assert two.ctx.mongo.col("memory_metadata").find_one({"memory_id": mem})["status"] == "active"
    assert two.ctx.mongo.col("user_memories").count_documents({"owner_user_id": two.bob_id, "status": "active"}) == len(two.sb["memory_ids"])


# ------------------------------------------------------------------------------------------------------------ 16: shared knowledge
def test_shared_records_are_visible_to_everyone_but_only_when_explicitly_published(world):
    a, b = world.user("Alice Aardvark", "alice@example.com"), world.user("Bob Badger", "bob@example.com")
    shared = world.ctx.ingestion.ingest(DOCS["django"]["source_name"], DOCS["django"]["text"], DOCS["django"]["metadata"], visibility="shared")
    hidden = world.ctx.ingestion.ingest(DOCS["docker"]["source_name"], DOCS["docker"]["text"], DOCS["docker"]["metadata"])  # default: restricted
    q_shared, q_hidden = "how does django map url patterns to views", "how do I build a docker image and run a container"
    for c in (a, b):
        r = ask(c, q_shared, "global")
        assert r["results"] and all(x["scope"] == "shared" for x in r["results"]) and set(x["memory_id"] for x in r["results"]) & set(shared["memory_ids"])
        assert not {x["memory_id"] for x in ask(c, q_hidden, "global")["results"]} & set(hidden["memory_ids"])  # restricted never reaches a user
    # an administrator publishes it, then withdraws it
    admin = world.admin()
    assert admin.post(f"{A}/sources/{hidden['source_id']}/visibility", json={"visibility": "shared"}).status_code == 200
    assert {x["memory_id"] for x in ask(a, q_hidden, "global")["results"]} & set(hidden["memory_ids"])
    assert admin.post(f"{A}/sources/{hidden['source_id']}/visibility", json={"visibility": "restricted"}).status_code == 200
    assert not {x["memory_id"] for x in ask(a, q_hidden, "global")["results"]} & set(hidden["memory_ids"])
    # a record with no label at all (legacy, never migrated) is not shared either
    world.ctx.qdrant.delete_payload_keys(shared["memory_ids"], ["visibility"])
    assert not {x["memory_id"] for x in ask(a, q_shared, "global")["results"]} & set(shared["memory_ids"])


@pytest.mark.parametrize("mode", MODES)
def test_shared_and_personal_memory_are_searched_together_in_every_strategy(world, mode):
    publish_core(world.ctx)
    a = world.user("Alice Aardvark", "alice@example.com")
    mine = save(a, ALICE_TEXT, title="Project Zephyr")
    r = ask(a, "how does django map url patterns to views", mode)
    assert any(x["scope"] == "shared" for x in r["results"])
    r = ask(a, ALICE_Q, mode)
    assert r["results"][0]["memory_id"] in mine["memory_ids"] and r["results"][0]["scope"] == "personal"


def test_shared_graph_nodes_only_include_published_modules(world):
    publish_core(world.ctx, ("django",))
    world.ctx.ingestion.ingest(DOCS["docker"]["source_name"], DOCS["docker"]["text"], DOCS["docker"]["metadata"])  # restricted
    a = world.user("Alice Aardvark", "alice@example.com")
    nodes = {n["id"]: n for n in a.get(f"{A}/me/graph").json()["nodes"]}
    assert "mod_django" in nodes and nodes["mod_django"]["data"]["scope"] == "shared"
    assert not any("docker" in n.lower() for n in nodes)


# ------------------------------------------------------------------------------------------------ new accounts and empty memory
def test_a_new_account_starts_with_an_empty_personal_space(world):
    publish_core(world.ctx, ("django",))
    a = world.user("Alice Aardvark", "alice@example.com")
    save(a, ALICE_TEXT)
    c = world.user("Carol Newcomer", "carol@example.com")  # registers after Alice has data
    assert c.get(f"{A}/me/memories").json()["total"] == 0
    g = c.get(f"{A}/me/graph").json()
    assert all(n["data"]["scope"] == "shared" for n in g["nodes"])  # only published shared knowledge, none of Alice's modules
    assert c.get(f"{A}/me/summary").json()["memories"] == 0


@pytest.mark.parametrize("mode", MODES)
def test_an_empty_account_can_query_without_error(world, mode):
    c = world.user("Carol Newcomer", "carol@example.com")
    r = ask(c, "anything at all", mode)
    assert r["results"] == [] and r["memory_scope"]["empty"] is True
    g = c.get(f"{A}/me/graph").json()
    assert g["nodes"] == [] and g["memory_scope"]["empty"] is True


# --------------------------------------------------------------------------------------------- storage adapters, directly
def test_vector_adapter_never_returns_a_foreign_point_even_for_identical_text(world):
    ctx = world.ctx
    ca, cb = ctx.personal, ctx.personal
    a, b = "usr_AAAAAAAAAAAAAAAA", "usr_BBBBBBBBBBBBBBBB"
    ra, rb = ca.save(a, ALICE_TEXT, title="x"), cb.save(b, ALICE_TEXT, title="x")  # byte-identical content, two owners
    assert ra["source_id"] != rb["source_id"] and not set(ra["memory_ids"]) & set(rb["memory_ids"])
    qv = ctx.embedder.encode([ALICE_Q])[0]
    ha = ctx.user_vectors.search_memories(a, qv, None, 20)
    hb = ctx.user_vectors.search_memories(b, qv, None, 20)
    assert ha and hb and {h.memory_id for h in ha} == set(ra["memory_ids"]) and {h.memory_id for h in hb} == set(rb["memory_ids"])
    assert all(h.payload["owner_user_id"] == a for h in ha)
    assert ctx.user_vectors.search_modules(a, qv, 10) and {h.module_id for h in ctx.user_vectors.search_modules(a, qv, 10)} == set(ra["module_ids"])
    assert ctx.user_vectors.count_memories(a) == len(ra["memory_ids"]) and ctx.user_vectors.count_modules(b) == len(rb["module_ids"])
    # module filters naming the other owner's module ids find nothing
    assert ctx.user_vectors.search_memories(a, qv, rb["module_ids"], 20) == []
    assert ctx.user_vectors.get_memories(a, rb["memory_ids"]) == {}


def test_vector_adapter_refuses_unowned_or_mismatched_operations(world):
    uv: UserVectorStore = world.ctx.user_vectors
    qv = world.ctx.embedder.encode(["x"])[0]
    with pytest.raises(ValueError):
        uv.search_memories("", qv, None, 5)  # there is no unscoped search
    with pytest.raises(ValueError):
        uv.upsert_memories("usr_AAAA", [("umem_x", qv, {"memory_id": "umem_x", "owner_user_id": "usr_BBBB", "status": "active"})])  # cannot write for someone else
    with pytest.raises(ValueError):
        world.ctx.user_data.get_memory("", "umem_x")


def test_scoped_store_only_reaches_the_scopes_own_and_published_modules(world):
    ctx = world.ctx
    publish_core(ctx, ("django",))
    a, b = "usr_AAAAAAAAAAAAAAAA", "usr_BBBBBBBBBBBBBBBB"
    ra, rb = ctx.personal.save(a, ALICE_TEXT, title="A"), ctx.personal.save(b, BOB_TEXT, title="B")
    scope = build_scope(a, ctx.personal.graph(a), ctx.graph.graph, ctx.mongo, 1)
    store = ScopedStore(ctx.qdrant, ctx.user_vectors, scope)
    qv = ctx.embedder.encode([BOB_Q])[0]
    assert store.search_memories(qv, rb["module_ids"], 10) == []  # naming Bob's modules reaches nothing
    everything = store.search_memories(qv, None, 50)
    assert everything and {h.memory_id for h in everything} <= set(ra["memory_ids"]) | {m for m in ctx.mongo.col("memory_metadata").distinct("memory_id")}
    assert not {h.memory_id for h in everything} & set(rb["memory_ids"])
    assert store.count_memories(None) == len(ra["memory_ids"]) + ctx.qdrant.count_memories(None, visibility="shared")
    assert store.count_memories(rb["module_ids"]) == 0
    assert not {h.module_id for h in store.search_modules(qv, 20)} & set(rb["module_ids"])


def test_stored_payloads_carry_ownership_and_visibility(two):
    pts = list(two.ctx.user_vectors.scroll_memories(two.alice_id, status=None))
    assert pts
    for _, payload, _ in pts:
        assert payload["owner_user_id"] == two.alice_id and payload["visibility"] == "private" and payload["status"] == "active"
        assert payload["memory_id"].startswith(f"umem_{owner_token(two.alice_id)}_")
    for d in two.ctx.user_data.list_modules(two.alice_id):
        assert d["owner_user_id"] == two.alice_id and d["module_id"].startswith(f"umod_{owner_token(two.alice_id)}_")
    src = two.ctx.user_data.get_source(two.alice_id, two.sa["source_id"])
    assert src["owner_user_id"] == two.alice_id and src["visibility"] == "private"
    assert two.ctx.user_data.get_source(two.bob_id, two.sa["source_id"]) is None  # Bob's scope cannot see Alice's source row


def test_user_scoped_indexes_exist(world):
    world.user("Alice Aardvark", "alice@example.com")
    idx = world.ctx.mongo.col("user_memories").index_information()
    keys = [tuple(k for k, _ in v["key"]) for v in idx.values()]
    assert ("owner_user_id", "memory_id") in keys and ("owner_user_id", "status", "module_id") in keys
    assert any(v.get("unique") and [k for k, _ in v["key"]] == ["owner_user_id", "memory_id"] for v in idx.values())
    assert any(v.get("unique") for v in world.ctx.mongo.col("users").index_information().values())
    assert any(v.get("unique") and [k for k, _ in v["key"]] == ["owner_user_id", "module_id"] for v in world.ctx.mongo.col("user_modules").index_information().values())


# --------------------------------------------------------------------------------------------- consistency and failure handling
def test_a_failed_save_leaves_nothing_active_and_is_reported_honestly(world, monkeypatch):
    c = world.user("Alice Aardvark", "alice@example.com")
    uid = c.get(f"{A}/auth/me").json()["user"]["user_id"]

    def boom(*a, **k):
        raise RuntimeError("simulated storage failure after staging")

    monkeypatch.setattr(world.ctx.personal, "refresh_profiles", boom)
    r = c.post(f"{A}/me/memories", json={"text": ALICE_TEXT, "title": "will fail"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "save_failed" and "nothing was stored" in r.json()["error"]["message"]
    monkeypatch.undo()
    assert world.ctx.user_vectors.count_memories(uid) == 0
    assert world.ctx.user_data.stats(uid) == {"memories": 0, "modules": 0, "sources": 0, "tokens": 0}
    assert world.ctx.mongo.col("user_memories").count_documents({"owner_user_id": uid, "status": "active"}) == 0
    assert world.ctx.mongo.col("user_sources").count_documents({"owner_user_id": uid}) == 0
    ev = world.ctx.mongo.col("audit_events").find_one({"action": "memory.created", "actor_user_id": uid})
    assert ev["status"] == "failure" and ev["metadata"]["error_code"] == "storage_error"
    # and the account still works afterwards
    assert c.post(f"{A}/me/memories", json={"text": ALICE_TEXT}).status_code == 201


def test_rejected_and_unauthorized_actions_change_no_memory_or_architecture_state(two):
    snap = lambda: (two.ctx.mongo.col("user_memories").count_documents({}), two.ctx.user_vectors.client.count(two.ctx.user_vectors.memory_collection, exact=True).count,
                    two.ctx.mongo.col("memory_metadata").count_documents({}), two.ctx.versions.active_id(), two.ctx.mongo.col("modules").count_documents({}))
    before = snap()
    anon = two.browser()
    for c in (anon, two.alice):
        for path, body in (("/ingestion", {"source_name": "a", "text": "b"}), ("/versions/checkpoint", {}), ("/compression/run", {}), ("/adaptation/run-window", {})):
            assert c.post(f"{A}{path}", json=body).status_code in (401, 403)
    assert anon.post(f"{A}/me/memories", json={"text": "unauthenticated write"}).status_code == 401
    assert two.alice.post(f"{A}/me/memories", json={"text": ""}).status_code == 422
    assert two.alice.post(f"{A}/me/memories", json={"text": "x" * 30_000}).status_code in (413, 422)
    assert two.alice.delete(f"{A}/me/memories/{two.sb['memory_ids'][0]}").status_code == 404
    assert snap() == before


def test_deleting_a_memory_retires_its_module_and_never_touches_other_users(two):
    r = two.alice.delete(f"{A}/me/memories/{two.sa['memory_ids'][0]}")
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    if len(two.sa["memory_ids"]) == 1:
        assert two.alice.get(f"{A}/me/graph").json()["nodes"] == []
    assert not ask(two.alice, ALICE_Q)["results"] or all(x["memory_id"] != two.sa["memory_ids"][0] for x in ask(two.alice, ALICE_Q)["results"])
    assert ask(two.bob, BOB_Q)["results"][0]["memory_id"] in two.sb["memory_ids"]


def test_duplicate_save_is_reported_and_per_user(two):
    again = two.alice.post(f"{A}/me/memories", json={"text": ALICE_TEXT}).json()
    assert again["status"] == "duplicate" and again["chunks_created"] == 0
    other = two.bob.post(f"{A}/me/memories", json={"text": ALICE_TEXT}).json()  # Bob saving the same words is his own, separate memory
    assert other["status"] == "completed" and not set(other["memory_ids"]) & set(two.sa["memory_ids"])


def test_personal_graph_has_hierarchy_and_co_access_shortcuts_only_for_its_owner(world):
    c = world.user("Alice Aardvark", "alice@example.com")
    save(c, ALICE_TEXT, topic="Zephyr", group="Work")
    save(c, "Zephyr team stand-up happens every weekday at nine in the quartz-nebula room.", topic="Meetings", group="Work")
    g = c.get(f"{A}/me/graph").json()
    kinds = {e["data"]["relationType"] for e in g["edges"]}
    assert "hierarchy" in kinds and len(g["nodes"]) == 3  # Work (container) + Zephyr + Meetings
    for _ in range(world.settings.user_coaccess_min_count):  # queries that retrieve both modules build a shortcut
        ask(c, "Zephyr quartz-nebula", "global", top_k=10)
    g = c.get(f"{A}/me/graph").json()
    assert "co_access" in {e["data"]["relationType"] for e in g["edges"]}
    other = world.user("Bob Badger", "bob@example.com")
    assert other.get(f"{A}/me/graph").json()["edges"] == []
