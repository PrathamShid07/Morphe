"""Strategy benchmark runner on the offline synthetic BEIR corpus (hashing embedder, mongomock, in-memory Qdrant) + protocol guarantees."""
import json
from pathlib import Path

import pytest

from app.benchmark.corpus import GraphInitConfig
from app.benchmark.runner import BenchmarkConfig
from app.evaluation import run_strategies as RS
from app.ingestion.embedder import HashingEmbedder
from tests.beir_fixture import write_beir
from tests.conftest import admin_test_client, make_settings


@pytest.fixture()
def env(tmp_path):
    write_beir(tmp_path / "beir")
    s = make_settings(tmp_path, beir_data_dir=str(tmp_path / "beir"), beir_artifacts_dir=str(tmp_path / "art" / "beir"))
    cfg = BenchmarkConfig(datasets=["synth"], data_dir=str(tmp_path / "beir"), artifacts_dir=str(tmp_path / "art" / "beir"),
                          graph=GraphInitConfig(target_module_size=20, min_module_size=3, min_modules=4), fallback_min_score=0.05)
    return s, cfg, HashingEmbedder(384), tmp_path / "art"


@pytest.fixture()
def finished(env, monkeypatch):
    s, cfg, emb, out = env
    asked = []
    orig = RS.group_qrels
    monkeypatch.setattr(RS, "group_qrels", lambda data, proto, group: (asked.append(group), orig(data, proto, group))[1])
    man = RS.run(cfg, s, emb, ["synth"], out, "strat_test_01", "tune", progress=lambda m: None)
    tuned_groups = list(asked)
    assert not (out / RS.ROOT_NAME / "strat_test_01" / "synth" / "test.json").exists()  # nothing was evaluated on test during tuning
    RS.run(cfg, s, emb, ["synth"], out, "strat_test_01", "evaluate", reps_val=1, reps_test=1, progress=lambda m: None)
    return env, man, tuned_groups, out / RS.ROOT_NAME / "strat_test_01"


def test_tuning_reads_the_validation_group_only_and_freezes_before_any_test_label(finished):
    (s, cfg, emb, out), man, tuned_groups, d = finished
    assert set(tuned_groups) == {"validation"}  # tune phase never asked for test (or workload) qrels
    assert (d / "frozen_config.json").exists() and (d / "tuning.json").exists()
    fz = json.loads((d / "frozen_config.json").read_text())
    assert fz["selected_from"] == "validation group only" and set(fz["code_sha256"]) >= {"retrieval/strategies.py", "retrieval/controller.py"}
    assert fz["soft"]["weight"] > 0 and fz["progressive"]["policy"] in ("profile", "blend", "graph")


def test_artifacts_metrics_and_comparisons_are_complete_and_consistent(finished):
    (s, cfg, emb, out), man, _, d = finished
    for group in ("validation", "test"):
        r = json.loads((d / "synth" / f"{group}.json").read_text())
        assert set(r["metrics"]) == set(RS.STRATEGIES) and r["group"] == group and r["frozen_config_sha256"] == json.loads((d / "manifest.json").read_text())["frozen_config_sha256"]
        assert all(g < 1e-4 for g in r["metric_consistency_gap"].values())  # per-query values agree with BEIR's aggregate
        for m in r["metrics"].values():
            assert {"ndcg_at_10", "map_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10", "precision_at_10"} <= set(m)
        assert {c["comparison"] for c in r["comparisons"]} == {"fixed_graph_vs_global_dense", "progressive_vs_global_dense", "soft_vs_global_dense",
                                                              "progressive_vs_fixed_graph", "soft_vs_fixed_graph"}
        for c in r["comparisons"]:
            b = c["bootstrap"]["ndcg_at_10"]
            assert b["resamples"] == 1000 and b["seed"] == 13 and b["n_queries"] == r["queries"]  # documented, fixed bootstrap settings
        assert len(r["rows"]) == r["queries"]
        lat = r["latency"]
        assert lat["global_dense"]["mean_eligible_candidates"] >= lat["fixed_graph"]["mean_eligible_candidates"]  # a hard filter never examines more
        assert lat["soft"]["candidate_pool_reduction_vs_corpus"] == 0  # soft routing is a full search: no reduction is claimed
        assert {"stop_reasons", "stages_run_distribution"} <= set(r["diagnostics"]["progressive"]["progressive"])
    t = json.loads((d / "synth" / "test.json").read_text())
    assert t["official_test_split"] is True and t["status"].startswith("confirmatory") and t["smoke"] is False


def test_global_and_fixed_strategies_match_the_baseline_implementations_exactly(finished):
    (s, cfg, emb, out), man, _, d = finished
    v = json.loads((d / "synth" / "validation.json").read_text())
    tune = json.loads((d / "tuning.json").read_text())["sweeps"]["synth"]
    # same queries, same engine, same metric code: the tuning pass and the evaluation pass must agree on the two controls
    assert v["metrics"]["global_dense"]["ndcg_at_10"] == pytest.approx(tune["global_dense"]["metrics"]["ndcg_at_10"], abs=1e-4)
    assert v["metrics"]["fixed_graph"]["ndcg_at_10"] == pytest.approx(tune["fixed_graph"]["metrics"]["ndcg_at_10"], abs=1e-4)


def test_run_ids_are_never_reused_and_test_needs_an_intact_frozen_config(finished, env):
    (s, cfg, emb, out), man, _, d = finished
    with pytest.raises(FileExistsError):
        RS.run(cfg, s, emb, ["synth"], out, "strat_test_01", "tune", progress=lambda m: None)  # a finished run is not overwritten
    with pytest.raises(FileNotFoundError):
        RS.run(cfg, s, emb, ["synth"], out, "strat_test_never_tuned", "evaluate", progress=lambda m: None)  # no test evaluation before freezing
    p = d / "frozen_config.json"
    p.write_text(p.read_text().replace('"weight"', '"weight" ', 1))
    with pytest.raises(ValueError, match="modified after it was frozen"):
        RS.run(cfg, s, emb, ["synth"], out, "strat_test_01", "evaluate", progress=lambda m: None)


def _sweep(nd_global, prog, soft):
    def row(nd, work=100.0, fb=0.0):
        return {"metrics": {"ndcg_at_10": nd}, "ndcg_per_query": [nd] * 20, "mean_candidates_incl_fallback": work, "mean_eligible_candidates": work, "fallback_rate": fb}
    return {"global_dense": row(nd_global, 1000.0), "fixed_graph": row(nd_global - 0.05, 200.0),
            "progressive": [row(*prog[i]) for i in range(len(RS.PROGRESSIVE_GRID))], "soft": [row(*soft[i]) for i in range(len(RS.SOFT_GRID))]}


def test_selection_rules_pick_the_cheapest_non_inferior_progressive_config_and_best_nonzero_soft_config():
    n = len(RS.PROGRESSIVE_GRID)
    prog = [(0.50, 900.0)] * n  # all non-inferior to global (0.50) ...
    prog[3] = (0.495, 400.0)  # ... config 3 is non-inferior (within 0.01) and cheapest
    prog[5] = (0.40, 100.0)   # cheapest of all but 0.10 worse: must not qualify
    soft = [(0.50,)] * len(RS.SOFT_GRID)
    soft[4] = (0.52,)
    soft[7] = (0.52,)  # tie on macro ndcg: the smaller weight wins
    sw = lambda i: RS.SOFT_GRID[i]["weight"]  # noqa: E731
    t = {"a": _sweep(0.50, prog, soft), "b": _sweep(0.50, prog, soft)}
    sel = RS.select_configs(t)
    assert sel["progressive"]["selected"] == RS.PROGRESSIVE_GRID[3] and "non-inferior" in sel["progressive"]["note"]
    assert sel["soft"]["selected"] == RS.SOFT_GRID[min((4, 7), key=sw)] and sel["soft"]["selected"]["weight"] > 0
    assert sel["soft"]["reliable_gain"] is True  # +0.02 on every query of every dataset: positive everywhere and the interval excludes 0
    assert sel["soft"]["bootstrap_vs_weight_zero_on_validation"]["a"]["mean_diff"] == pytest.approx(0.02)


def test_when_no_progressive_config_is_non_inferior_the_best_quality_one_is_chosen_and_the_fact_is_recorded():
    n = len(RS.PROGRESSIVE_GRID)
    prog = [(0.40, 100.0)] * n
    prog[2] = (0.45, 800.0)  # best quality, still 0.05 below global
    soft = [(0.5,)] * len(RS.SOFT_GRID)
    sel = RS.select_configs({"a": _sweep(0.50, prog, soft)})
    assert sel["progressive"]["selected"] == RS.PROGRESSIVE_GRID[2] and "NO configuration was non-inferior" in sel["progressive"]["note"]
    assert sel["soft"]["reliable_gain"] is False  # identical scores: no gain


# ------------------------------------------------------------------------------------------------------------- API
@pytest.fixture()
def api(finished):
    from fastapi.testclient import TestClient
    from app.main import create_app
    (s, cfg, emb, out), man, _, d = finished
    app = create_app(make_settings(out.parent, beir_strategies_dir=str(out / RS.ROOT_NAME)))
    with admin_test_client(app) as c:
        yield c, d


def test_strategy_api_serves_the_saved_artifacts_unchanged(api):
    c, d = api
    runs = c.get("/api/v1/benchmarks/strategies/runs").json()["items"]
    assert [r["run_id"] for r in runs] == ["strat_test_01"] and runs[0]["frozen"] is True and runs[0]["evaluated"] == {"synth": ["validation", "test"]}
    run = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01").json()
    assert run["frozen_config"] == json.loads((d / "frozen_config.json").read_text()) and "table" in run["selection"]["progressive"]
    g = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test").json()
    on_disk = json.loads((d / "synth" / "test.json").read_text())
    assert g["metrics"] == on_disk["metrics"] and "rows" not in g  # real numbers, not recomputed; per-query rows come from /rows
    rows = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test/rows", params={"sort": "progressive_vs_global_dense", "limit": 5}).json()
    assert rows["total"] == on_disk["queries"] and len(rows["items"]) == 5
    deltas = [x["delta_ndcg_at_10"] for x in rows["items"]]
    assert deltas == sorted(deltas)  # "worst" first
    best = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test/rows", params={"order": "best", "limit": 3}).json()["items"]
    assert [x["delta_ndcg_at_10"] for x in best] == sorted((x["delta_ndcg_at_10"] for x in best), reverse=True)
    deg = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test/rows", params={"only": "degraded", "limit": 200}).json()
    assert all(x["delta_ndcg_at_10"] < 0 for x in deg["items"])
    one = rows["items"][0]
    hit = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test/rows", params={"search": one["query_id"]}).json()
    assert hit["total"] >= 1 and one["query_id"] in {x["query_id"] for x in hit["items"]}


def test_strategy_api_exports_and_rejects_bad_input(api):
    c, d = api
    j = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/export").json()
    assert set(j["results"]["synth"]) == {"validation", "test"}
    csv_text = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/export", params={"format": "csv"}).text.splitlines()
    assert csv_text[0].startswith("run_id,dataset,group,status,strategy,ndcg_at_10") and len(csv_text) == 1 + 2 * 4
    assert c.get("/api/v1/benchmarks/strategies/runs/nope").status_code == 404
    assert c.get("/api/v1/benchmarks/strategies/runs/..%2F..%2Fetc").status_code in (404, 422)  # no path traversal
    assert c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/nope/test").status_code == 404
    assert c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/other").status_code == 422
    assert c.get("/api/v1/benchmarks/strategies/runs/strat_test_01/synth/test/rows", params={"sort": "bogus"}).status_code == 422


def test_graph_and_module_endpoints_report_capacity_utilisation(seeded, tmp_path):
    from app.api.routes import core  # call the route functions on the seeded context (a TestClient app would build its own empty one)
    ctx = seeded
    g = core.graph(ctx)
    cap = ctx.settings.split_capacity_threshold
    assert g["capacity_threshold"] == cap
    for n in g["nodes"]:
        d = n["data"]
        assert (d["utilization"] is None) if d["isContainer"] else d["utilization"] == pytest.approx(d["recordCount"] / cap, abs=1e-4)
    mods = core.list_modules(ctx)["items"]
    assert all(m["capacity_threshold"] == cap and m["utilization"] == pytest.approx(m["record_count"] / cap, abs=1e-4) for m in mods)
    det = core.module_detail(mods[0]["module_id"], ctx)
    assert det["capacity_threshold"] == cap and det["architecture_version"] == ctx.versions.active_id()


def test_run_detail_includes_server_latency_only_when_it_was_measured(api):
    c, d = api
    assert "server_latency" not in c.get("/api/v1/benchmarks/strategies/runs/strat_test_01").json()
    (d / "server_latency.json").write_text(json.dumps({"deployment": "test server", "datasets": {"synth": {"queries": 3, "strategies": {}}}}))
    got = c.get("/api/v1/benchmarks/strategies/runs/strat_test_01").json()
    assert got["server_latency"]["deployment"] == "test server"  # served unchanged from the artifact
