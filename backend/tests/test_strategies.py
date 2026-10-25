"""Experimental retrieval strategies: progressive graph expansion and soft graph routing (retrieval/strategies.py).
Real algorithms on the shared synthetic corpus; only the embedder / Qdrant / MongoDB boundaries are test doubles."""
import pytest

from app.retrieval import strategies as ST
from app.retrieval.ranker import rank_hits


def ids(out):
    return [r["memory_id"] for r in out["results"]]


def progressive(ctx, q="how does django map url patterns to views", k=3, **settings):
    for key, v in settings.items():
        setattr(ctx.settings, key, v)
    return ctx.retrieval.query(q, k, True, "progressive")


def spy_filters(ctx):
    """Record every module filter passed to the memory search (None = unfiltered/global)."""
    calls = []
    orig = ctx.retrieval.engine.store.search_memories

    def wrapped(vector, module_ids, limit, status="active"):
        calls.append(None if module_ids is None else list(module_ids))
        return orig(vector, module_ids, limit, status)
    ctx.retrieval.engine.store.search_memories = wrapped
    return calls


# ============================================================================================== progressive expansion
def test_stage_one_searches_the_top_profile_modules(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_seed_modules=2)
    t = out["trace"]
    ranking = [h.module_id for h in ctx.qdrant.search_modules(ctx.embedder.encode(["how does django map url patterns to views"])[0], 50)]
    s1 = t["stages"][0]
    assert s1["kind"] == "seed" and [m["module_id"] for m in s1["modules"]] == ranking[:2]
    assert [m["profile_rank"] for m in s1["modules"]] == [1, 2]
    assert [m["module_id"] for m in t["seed_modules"]] == ranking[:2] and t["strategy"] == "progressive" and out["mode"] == "progressive"


def test_multi_stage_expansion_never_repeats_a_module_and_respects_every_limit(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_seed_modules=1, progressive_step_modules=1, progressive_patience=99, progressive_max_steps=3,
                      progressive_max_modules=0, fallback_enabled=False)
    t = out["trace"]
    stages = t["stages"]
    assert len(stages) == 4 and [s["stage"] for s in stages] == [1, 2, 3, 4]  # seed + max_steps expansion steps
    searched = [m["module_id"] for s in stages for m in s["modules"]]
    assert len(searched) == len(set(searched)) == 1 + 1 * 3  # a module is never searched twice
    g = ctx.graph.graph
    cum = 0
    for s in stages:
        cum += sum(g.nodes[m["module_id"]]["active_record_count"] for m in s["modules"])
        assert s["cumulative_candidates"] == cum  # candidate accounting follows the graph's active record counts
    assert t["candidate_count"] == cum and t["progressive"]["stop_reason"] == "budget" and t["progressive"]["stages_run"] == 4
    # module cap: never exceeds progressive_max_modules even when steps would allow more
    out2 = progressive(ctx, progressive_seed_modules=1, progressive_step_modules=1, progressive_patience=99, progressive_max_modules=3, progressive_max_steps=10)
    assert out2["trace"]["progressive"]["modules_searched"] == 3 and out2["trace"]["progressive"]["stop_reason"] == "budget"


def test_stable_top_k_stops_early_and_stays_graph_only(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_seed_modules=1, progressive_step_modules=1, progressive_patience=1, progressive_max_steps=6,
                      fallback_min_score=0.0)
    t = out["trace"]
    st = t["stages"]
    assert t["progressive"]["stop_reason"] == "stable" and len(st) < 7
    assert st[-1]["top_k_changed"] is False and st[-1]["decision"].startswith("stop: the top-")
    assert t["fallback_used"] is False and out["results"][0]["module_id"] == "mod_django"
    assert all(s["top_k_changed"] is not None for s in st[1:]) and st[0]["top_k_changed"] is None  # nothing to compare with at stage 1


def test_weak_result_triggers_global_fallback_with_a_reason(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_seed_modules=1, progressive_step_modules=1, progressive_max_steps=1, fallback_min_score=0.99,
                      fallback_enabled=True)  # narrow budget: not every module is searched, so a fallback has something to recover
    t = out["trace"]
    assert t["fallback_used"] is True and "below the fallback threshold" in t["fallback_reason"]
    glob = ctx.retrieval.query("how does django map url patterns to views", 3, False, "global")
    assert ids(out) == ids(glob)  # fallback returns exactly the global ranking
    assert t["candidate_count"] > t["guided_candidate_count"]
    off = progressive(ctx, progressive_seed_modules=1, progressive_step_modules=1, progressive_max_steps=1, fallback_min_score=0.99, fallback_enabled=False)
    assert off["trace"]["fallback_used"] is False and off["results"]  # disabled fallback never runs


def test_budget_reached_while_unsettled_falls_back_unless_disabled(seeded):
    ctx = seeded
    kw = dict(progressive_seed_modules=1, progressive_step_modules=1, progressive_patience=99, progressive_max_steps=2,
              progressive_max_modules=0, fallback_min_score=0.0, fallback_enabled=True)
    on = progressive(ctx, progressive_fallback_on_budget=True, **kw)
    assert on["trace"]["fallback_used"] is True and "still changing" in on["trace"]["fallback_reason"]
    off = progressive(ctx, progressive_fallback_on_budget=False, **kw)
    assert off["trace"]["fallback_used"] is False and off["trace"]["progressive"]["unsettled_at_stop"] is True


def test_searching_everything_equals_global_search_with_stable_unique_ids(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_seed_modules=3, progressive_step_modules=3, progressive_patience=99, progressive_max_steps=50,
                      progressive_max_modules=0, fallback_enabled=False, k=8)
    glob = ctx.retrieval.query("how does django map url patterns to views", 8, False, "global")
    assert out["trace"]["progressive"]["stop_reason"] == "all modules searched"
    assert ids(out) == ids(glob) and len(set(ids(out))) == len(ids(out))  # no duplicates, same stable ids and order as exact search
    assert [round(r["score"], 4) for r in out["results"]] == [round(r["score"], 4) for r in glob["results"]]


def test_empty_corpus_modules_fall_back_cleanly(ctx):
    out = ctx.retrieval.query("anything at all", 3, True, "progressive")
    assert out["results"] == [] and out["trace"]["fallback_used"] is True and "no module profiles" in out["trace"]["fallback_reason"]


@pytest.mark.parametrize("policy", ["profile", "graph", "blend"])
def test_policies_work_on_a_graph_with_no_edges_between_content_modules(seeded, policy):
    ctx = seeded
    g = ctx.graph.graph
    g.remove_edges_from(list(g.edges(keys=True)))  # disconnected architecture: nothing is linked to anything
    out = progressive(ctx, progressive_policy=policy, progressive_patience=99, progressive_max_steps=2, progressive_max_modules=0,
                      fallback_enabled=False)
    searched = [m["module_id"] for s in out["trace"]["stages"] for m in s["modules"]]
    assert out["results"] and len(searched) == len(set(searched)) > 3  # still expands, by profile rank


def test_graph_policy_prefers_linked_modules_while_profile_policy_does_not(seeded):
    ctx = seeded
    g = ctx.graph.graph
    q = ctx.embedder.encode(["how does django map url patterns to views"])[0]
    order = [h.module_id for h in ctx.qdrant.search_modules(q, 50)]
    sim = {h.module_id: h.score for h in ctx.qdrant.search_modules(q, 50)}
    searched = {order[0]}
    remaining = order[1:]
    linked = ST._linked(g, searched)
    assert ST._next_modules("profile", remaining, sim, g, searched, 2, 0.05) == remaining[:2]
    gp = ST._next_modules("graph", remaining, sim, g, searched, 2, 0.05)
    assert gp == ([m for m in remaining if m in linked] + [m for m in remaining if m not in linked])[:2]
    bp = ST._next_modules("blend", remaining, sim, g, searched, 2, 10.0)  # huge bonus: linked modules always outrank unlinked ones
    assert all(m in linked for m in bp[: min(2, len([m for m in remaining if m in linked]))])


def test_timing_attribution_and_stage_records(seeded):
    ctx = seeded
    out = progressive(ctx, progressive_patience=99, progressive_max_steps=2, progressive_max_modules=0, fallback_enabled=False)
    t = out["trace"]
    tm = t["timings_ms"]
    assert set(tm) >= {"seed", "expand", "search", "rank", "fallback"} and all(v >= 0 for v in tm.values())
    assert tm["search"] > 0 and tm["fallback"] == 0
    assert sum(tm.values()) <= out["latency_ms"] + 1.0  # stage times are a decomposition of the engine's work, never more than the whole
    assert t["embedding_ms"] is not None and t["embedding_ms"] >= 0
    assert abs(sum(s["search_ms"] for s in t["stages"]) - tm["search"]) < max(5.0, 0.5 * tm["search"] + 2.0)


def test_api_trace_agrees_with_the_filters_actually_sent_to_qdrant(seeded):
    ctx = seeded
    calls = spy_filters(ctx)
    ctx.settings.fallback_min_score = 0.0
    ctx.settings.progressive_patience = 99
    ctx.settings.progressive_max_steps = 2
    out = ctx.retrieval.query("how do I narrow a union type", 3, True, "progressive")
    stages = out["trace"]["stages"]
    sent = [c for c in calls if c is not None]
    assert sent == [[m["module_id"] for m in s["modules"]] for s in stages]  # one filtered search per stage, exactly the traced modules
    assert [c for c in calls if c is None] == [] or out["trace"]["fallback_used"]  # an unfiltered search only happens for a reported fallback


def test_progressive_is_not_the_fixed_graph_strategy(seeded):
    ctx = seeded
    ctx.settings.fallback_enabled = False
    fixed = ctx.retrieval.query("redis expire ttl eviction", 5, True, "graph")
    out = progressive(ctx, q="redis expire ttl eviction", k=5, progressive_patience=99, progressive_max_steps=3, progressive_max_modules=0)
    fixed_mods = {c["module_id"] for c in fixed["trace"]["candidate_modules"]}
    prog_mods = {m["module_id"] for s in out["trace"]["stages"] for m in s["modules"]}
    assert len(fixed_mods) <= ctx.settings.max_candidate_modules < len(prog_mods)  # not bound by the hard candidate cap or the hierarchy
    assert out["trace"]["strategy"] == "progressive" and fixed["trace"]["strategy"] == "graph"


def test_progressive_over_the_api(client):
    r = client.post("/api/v1/query", json={"query": "anything", "mode": "progressive", "debug_trace": True})
    assert r.status_code == 200 and r.json()["mode"] == "progressive"  # empty store: falls back cleanly instead of failing
    assert client.post("/api/v1/query", json={"query": "x", "mode": "nonsense"}).status_code == 422


# ====================================================================================================== soft routing
def soft(ctx, q="how does django map url patterns to views", k=5, **settings):
    for key, v in settings.items():
        setattr(ctx.settings, key, v)
    return ctx.retrieval.query(q, k, True, "soft")


def test_zero_weight_reproduces_the_dense_ranking_exactly(seeded):
    ctx = seeded
    for q in ("how does django map url patterns to views", "redis expire ttl eviction", "how do I narrow a union type"):
        out = soft(ctx, q, 8, soft_weight=0.0)
        glob = ctx.retrieval.query(q, 8, False, "global")
        assert ids(out) == ids(glob)
        assert [round(r["score"], 9) for r in out["results"]] == [round(r["score"], 9) for r in glob["results"]]
        assert out["trace"]["soft"]["promoted"] == out["trace"]["soft"]["demoted"] == 0


def test_graph_information_changes_the_ranking_when_configured_and_is_bounded(seeded):
    ctx = seeded
    q = "python web framework tutorial for beginners"
    dense = ctx.retrieval.query(q, 10, False, "global")
    lam = 0.5
    out = soft(ctx, q, 10, soft_weight=lam, soft_graph_share=0.5)
    sf = out["trace"]["soft"]
    assert ids(out) != ids(dense) or sf["top_k_membership_changed"] or sf["promoted"] + sf["demoted"] > 0  # the prior really reorders
    for r in sf["results"]:
        assert abs(r["score_shift"]) <= lam + 1e-9  # bounded influence
        assert r["final_score"] == pytest.approx(r["dense_score"] + r["score_shift"], abs=1e-5)
    finals = [r["final_score"] for r in sf["results"]]
    assert finals == sorted(finals, reverse=True) and [r["final_rank"] for r in sf["results"]] == list(range(1, len(finals) + 1))
    assert {r["change"] for r in sf["results"]} <= {"promoted", "demoted", "unchanged"}


def test_soft_routing_does_not_exclude_modules_and_reports_it_is_a_full_search(seeded):
    ctx = seeded
    ctx.qdrant.delete_module_profiles(["mod_redis"])  # a hard filter could never reach redis now (see test_retrieval)
    ctx.settings.fallback_enabled = False
    ctx.settings.max_candidate_modules = 2  # seeds only: no hierarchy sibling can lead the hard filter to redis
    hard = ctx.retrieval.query("redis expire ttl eviction policies lru", 3, True, "graph")
    assert "mod_redis" not in {r["module_id"] for r in hard["results"]}
    out = soft(ctx, "redis expire ttl eviction policies lru", 3, soft_weight=0.02)
    assert out["results"][0]["module_id"] == "mod_redis"  # still found: nothing is excluded by module membership
    total = sum(d["active_record_count"] for _, d in ctx.graph.graph.nodes(data=True))
    t = out["trace"]
    assert t["candidate_count"] == total and t["soft"]["full_global_search"] is True and t["fallback_used"] is False


def test_missing_module_information_is_neutral(seeded):
    ctx = seeded
    ctx.qdrant.delete_module_profiles(["mod_redis"])
    out = soft(ctx, "redis expire ttl eviction policies lru", 8, soft_weight=0.4)
    redis_rows = [r for r in out["trace"]["soft"]["results"] if r["module_id"] == "mod_redis"]
    assert redis_rows and all(r["profile_score"] is None and r["score_shift"] == 0 for r in redis_rows)


def test_graph_share_one_uses_graph_distance_only(seeded):
    ctx = seeded
    out = soft(ctx, "how does django map url patterns to views", 10, soft_weight=0.1, soft_graph_share=1.0)
    rows = out["trace"]["soft"]["results"]
    seed_rows = [r for r in rows if r["graph_distance"] == 0]
    assert seed_rows and all(r["prior"] == 1.0 and r["graph_component"] == 1.0 for r in seed_rows)
    for r in rows:
        d = r["graph_distance"]
        assert r["graph_component"] == (round(1.0 / (1.0 + d), 4) if d is not None else 0.0)


def test_soft_routing_is_deterministic_and_ties_break_on_memory_id(seeded):
    ctx = seeded
    a = soft(ctx, "redis expire ttl eviction", 8, soft_weight=0.05)
    b = soft(ctx, "redis expire ttl eviction", 8, soft_weight=0.05)
    assert ids(a) == ids(b) and [r["score"] for r in a["results"]] == [r["score"] for r in b["results"]]
    from app.storage.qdrant_store import Hit
    h = [Hit(m, "x", 0.5, {}) for m in ("m9", "m1", "m5")]
    assert [x.memory_id for x in rank_hits(h, 3)] == ["m1", "m5", "m9"]


def test_graph_distance_uses_hierarchy_and_only_well_observed_coaccess_edges(seeded):
    ctx = seeded
    g = ctx.graph.graph
    first, second = [n for n, d in g.nodes(data=True) if d["active_record_count"] > 0][:2]
    base = ST.graph_distance(g, [first], 3, 5)
    assert base[first] == 0
    g.add_edge(first, second, key="co_access", relation_type="co_access", weight=0.1, co_access_count=2, source=first, target=second, edge_id="t_weak")
    weak = ST.graph_distance(g, [first], 3, 5)
    assert weak.get(second) == base.get(second)  # 2 observations < threshold 5: ignored
    g.remove_edge(first, second, key="co_access")
    g.add_edge(first, second, key="co_access", relation_type="co_access", weight=0.9, co_access_count=9, source=first, target=second, edge_id="t_strong")
    assert ST.graph_distance(g, [first], 3, 5)[second] == 1  # enough observations: a real shortcut
    assert ST.graph_distance(g, [first], 0, 5) == {first: 0}  # depth limit


def test_soft_over_the_api_and_trace_fields(client):
    r = client.post("/api/v1/query", json={"query": "anything", "mode": "soft", "debug_trace": True})
    assert r.status_code == 200 and r.json()["mode"] == "soft" and r.json()["results"] == []
    assert r.json()["trace"]["soft"]["formula"].startswith("final = dense")


# ============================================================================================ strategy comparison service
def test_compare_strategies_runs_each_real_implementation_and_tracks_nothing(seeded):
    from app.evaluation.strategy_compare import compare_strategies
    ctx = seeded
    q = ctx.datasets.current().queries[0]
    before = ctx.mongo.col("query_events").count_documents({})
    res = compare_strategies(ctx, None, q.query_id, 5)
    assert ctx.mongo.col("query_events").count_documents({}) == before  # comparing never feeds the adaptation windows
    assert set(res["strategies"]) == {"global", "graph", "progressive", "soft"} and res["labeled"] is True and res["query"] == q.query
    total = sum(d["active_record_count"] for _, d in ctx.graph.graph.nodes(data=True))
    for mode, entry in res["strategies"].items():
        direct = ctx.retrieval.query(q.query, 5, False, mode, track=False)  # the same strategy through the normal query path
        assert [r["memory_id"] for r in entry["results"]] == [r["memory_id"] for r in direct["results"]], mode
        assert entry["strategy"] == mode and entry["latency_ms"] > 0 and "search" in entry["timings_ms"]
        assert set(entry["metrics"]) >= {"recall_at_k", "mrr", "ndcg_at_10", "map_at_10", "precision_at_k"}
    assert res["strategies"]["global"]["candidate_count"] == total and res["strategies"]["global"]["candidate_pool_reduction"] == 0
    assert res["strategies"]["soft"]["candidate_pool_reduction"] == 0 and res["strategies"]["soft"]["strategy_details"]["soft"]["full_global_search"]
    assert res["strategies"]["progressive"]["stages"] and res["strategies"]["graph"]["searched_modules"]


def test_compare_strategies_routing_exclusion_diagnostic_is_consistent(seeded):
    from app.evaluation.strategy_compare import compare_strategies
    ctx = seeded
    ctx.qdrant.delete_module_profiles(["mod_redis"])  # redis can no longer be routed to
    ctx.settings.max_candidate_modules = 2  # seeds only: no hierarchy sibling reaches it either
    ctx.settings.fallback_enabled = False
    saw_exclusion = False
    for q in ctx.datasets.current().queries:
        res = compare_strategies(ctx, None, q.query_id, 3, ["graph", "global"])
        g = res["strategies"]["graph"]
        searched = {m["module_id"] for m in g["searched_modules"]}
        for ex in g["relevant_excluded_by_routing"]:
            saw_exclusion = True
            assert ex["module_id"] not in searched and ex["memory_id"] in g["relevant_missed"]  # excluded => cannot have been retrieved
        assert res["strategies"]["global"]["relevant_excluded_by_routing"] == []
    assert saw_exclusion


def test_compare_strategies_validation(seeded):
    from app.common import NotFoundError, ValidationFailure
    from app.evaluation.strategy_compare import compare_strategies
    ctx = seeded
    with pytest.raises(ValidationFailure):
        compare_strategies(ctx, "   ", None)
    with pytest.raises(ValidationFailure):
        compare_strategies(ctx, "hello", None, 5, ["nonsense"])
    with pytest.raises(NotFoundError):
        compare_strategies(ctx, None, "no-such-query")
    free = compare_strategies(ctx, "how do I narrow a union type", None, 3)
    assert free["labeled"] is False and all("metrics" not in e for e in free["strategies"].values())  # free text: no fake metrics
