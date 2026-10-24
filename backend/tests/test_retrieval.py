"""Graph-guided retrieval: routing, filtering, fallback, tracking, candidate-count definition."""
import pytest

from app.retrieval.graph_selector import expand_candidates
from app.graph.manager import graph_from_state
from app.versioning.state import ArchitectureState, EdgeSpec, ModuleSpec, edge_id_for
from tests.conftest import ingest_docs


def test_query_is_routed_by_module_profiles_and_trace_matches_flow(seeded):
    ctx = seeded
    out = ctx.retrieval.query("how does django map url patterns to views", 3, True)
    t = out["trace"]
    assert out["results"][0]["module_id"] == "mod_django"
    assert t["seed_modules"][0]["module_id"] == "mod_django"
    cand_ids = [c["module_id"] for c in t["candidate_modules"]]
    assert len(cand_ids) <= ctx.settings.max_candidate_modules and cand_ids[0] == "mod_django"
    # candidate_count == active records inside the searched modules (explicit, consistent definition)
    expected = sum(ctx.graph.graph.nodes[m]["active_record_count"] for m in cand_ids)
    assert t["candidate_count"] == expected and t["fallback_used"] is False
    assert all(r["module_id"] in cand_ids for r in out["results"])  # filtered search really was restricted
    assert out["architecture_version"] == ctx.versions.active_id() and out["query_id"].startswith("qry_")


def test_guided_search_cannot_see_modules_outside_the_candidate_set(seeded):
    ctx = seeded
    ctx.settings.fallback_enabled = False
    out = ctx.retrieval.query("redis expire ttl eviction", 5, True)
    cands = {c["module_id"] for c in out["trace"]["candidate_modules"]}
    assert {r["module_id"] for r in out["results"]} <= cands
    # remove redis from routing: its records become unreachable by the guided path
    ctx.qdrant.delete_module_profiles(["mod_redis"])
    out2 = ctx.retrieval.query("redis expire ttl eviction", 5, True)
    assert "mod_redis" not in {c["module_id"] for c in out2["trace"]["candidate_modules"]}
    assert "mod_redis" not in {r["module_id"] for r in out2["results"]}


def test_global_fallback_recovers_when_routing_is_wrong(seeded):
    ctx = seeded
    ctx.qdrant.delete_module_profiles(["mod_redis"])  # routing can no longer reach redis
    ctx.settings.fallback_min_score = 0.3
    ctx.settings.fallback_enabled = True
    ctx.settings.max_candidate_modules = 2  # seeds only: no room for hierarchy expansion to reach redis
    out = ctx.retrieval.query("redis expire ttl eviction policies lru", 3, True)
    assert out["trace"]["fallback_used"] is True and out["trace"]["fallback_reason"]
    assert out["results"][0]["module_id"] == "mod_redis"
    assert out["trace"]["candidate_count"] > out["trace"]["guided_candidate_count"]


def test_global_mode_searches_everything(seeded):
    out = seeded.retrieval.query("git rebase merge", 3, True, mode="global")
    assert out["mode"] == "global" and out["trace"]["candidate_count"] == seeded.budget.usage()["active_memories"]


def test_top_k_validation_and_query_validation(seeded):
    from app.common import ValidationFailure
    with pytest.raises(ValidationFailure):
        seeded.retrieval.query("x", 0)
    with pytest.raises(ValidationFailure):
        seeded.retrieval.query("   ", 3)
    assert len(seeded.retrieval.query("git", 2)["results"]) == 2


def test_access_counts_events_and_window_are_recorded(seeded):
    ctx = seeded
    ctx.tracker.reset_window()
    out = ctx.retrieval.query("docker compose volumes", 3)
    mid = out["results"][0]["memory_id"]
    meta = ctx.mongo.get_memory_metadata(mid)
    assert meta["access_count"] == 1 and meta["last_accessed_at"]
    ev = ctx.mongo.col("query_events").find_one({"query_id": out["query_id"]})
    assert ev["result_ids"] and ev["candidate_count"] > 0 and ev["architecture_version"] == ctx.versions.active_id()
    assert "query_text" not in ev and len(ev["query_preview"]) <= 120  # privacy-conscious trace
    assert ctx.tracker.window()["query_count"] == 1


def test_superseded_and_deleted_records_are_not_retrievable(seeded):
    ctx = seeded
    out = ctx.retrieval.query("git cherry-pick single commit", 3)
    top = out["results"][0]["memory_id"]
    ctx.qdrant.set_status([top], "superseded")
    again = ctx.retrieval.query("git cherry-pick single commit", 10)
    assert top not in [r["memory_id"] for r in again["results"]]


# ---------------------------------------------------------------- selector
def small_graph():
    s = ArchitectureState()
    for m in ("a", "b", "c", "d", "e"):
        s.modules[m] = ModuleSpec(m, m, profile_vector=[1.0, 0.0])
        s.assignments[m] = [f"{m}1", f"{m}2"]
    s.modules["p"] = ModuleSpec("p", "p")
    s.modules["a"].parent_id = s.modules["e"].parent_id = "p"
    for ch in ("a", "e"):
        eid = edge_id_for("hierarchy", "p", ch)
        s.edges[eid] = EdgeSpec(eid, "p", ch, "hierarchy")
    eid = edge_id_for("co_access", "a", "c")
    s.edges[eid] = EdgeSpec(eid, "a", "c", "co_access", 0.5, 10)
    eid = edge_id_for("co_access", "a", "d")
    s.edges[eid] = EdgeSpec(eid, "a", "d", "co_access", 0.2, 4)
    return graph_from_state(s)


def test_expansion_orders_seeds_then_shortcuts_by_weight_then_siblings_and_respects_cap():
    g = small_graph()
    out = expand_candidates([("a", 0.9)], g, 8)
    assert [(c.module_id, c.origin) for c in out] == [("a", "seed"), ("c", "co_access"), ("d", "co_access"), ("e", "sibling")]
    assert [c.module_id for c in expand_candidates([("a", 0.9)], g, 2)] == ["a", "c"]
    assert [c.module_id for c in expand_candidates([("b", 0.9)], g, 8)] == ["b"]  # isolated: no expansion


def test_graph_edges_change_the_candidate_set():
    s = ArchitectureState()
    for m in ("x", "y"):
        s.modules[m] = ModuleSpec(m, m, profile_vector=[1.0])
        s.assignments[m] = [m + "1"]
    without = [c.module_id for c in expand_candidates([("x", 1.0)], graph_from_state(s), 5)]
    eid = edge_id_for("co_access", "x", "y")
    s.edges[eid] = EdgeSpec(eid, "x", "y", "co_access", 0.4, 8)
    with_edge = [c.module_id for c in expand_candidates([("x", 1.0)], graph_from_state(s), 5)]
    assert without == ["x"] and with_edge == ["x", "y"]
