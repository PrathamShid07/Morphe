"""Routing-diagnostics accounting on the offline synthetic BEIR corpus (hashing embedder, mongomock, in-memory Qdrant)."""
import json
from pathlib import Path

import pytest

from app.benchmark.corpus import GraphInitConfig, memory_id_for
from app.benchmark.datasets import load_beir
from app.benchmark.runner import BenchmarkConfig
from app.benchmark.splits import build_split_protocol, group_qrels
from app.evaluation import routing_diagnostics as RD
from app.ingestion.embedder import HashingEmbedder
from tests.beir_fixture import write_beir
from tests.conftest import make_settings


def _cfg(tmp_path, **over):
    write_beir(tmp_path / "beir")
    s = make_settings(tmp_path, beir_data_dir=str(tmp_path / "beir"), beir_artifacts_dir=str(tmp_path / "art"))
    cfg = BenchmarkConfig(datasets=["synth"], data_dir=str(tmp_path / "beir"), artifacts_dir=str(tmp_path / "art"),
                          graph=GraphInitConfig(target_module_size=20, min_module_size=3, min_modules=4), fallback_min_score=0.05,
                          **over)
    return s, cfg


def _run(tmp_path, run_id, **over):
    s, cfg = _cfg(tmp_path, **over)
    out = RD.run_dataset_diagnostics(cfg, s, HashingEmbedder(384), "synth", tmp_path / "phase2", run_id, reps=1)
    return s, cfg, out


@pytest.fixture()
def narrow(tmp_path):
    """One seed module, no expansion room: routing must exclude many relevant documents."""
    return _run(tmp_path, "diag_test_narrow", seed_module_count=1, max_candidate_modules=1)


def test_uses_validation_group_only_and_maps_qrels_to_documents_correctly(narrow, tmp_path):
    s, cfg, out = narrow
    data = load_beir("synth", cfg.data_dir, ("train", "test"))
    proto = build_split_protocol(data, cfg.seed)
    qrels = group_qrels(data, proto, "validation")
    ids = {r["query_id"] for r in out["rows"]}
    assert out["group"] == "validation" and out["official_test_split_used"] is False
    assert ids <= set(proto.validation) and not ids & set(proto.test) and not ids & set(proto.workload)
    for r in out["rows"]:
        for x in r["relevant_documents"]:
            assert qrels[r["query_id"]][x["doc_id"]] == x["grade"] > 0  # only judged-relevant documents, with their graded label
            assert x["memory_id"] == memory_id_for("synth", x["doc_id"])  # explicit, verified id mapping
    assert (tmp_path / "phase2" / "diag_test_narrow" / "synth" / "routing_diagnostics.json").exists()


def test_routing_miss_accounting_is_consistent(narrow):
    _, _, out = narrow
    rows, ag = out["rows"], out["aggregate"]
    assert ag["flags"]["mapping_or_consistency_error_queries"] == 0
    assert ag["lost_vs_global_top10"]["lost_although_eligible (must be 0)"] == 0  # the structural theorem holds on real engine output
    worse = [r for r in rows if r["outcome"] == "graph_worse"]
    assert worse, "single-module routing must lose something on this corpus"
    assert sum(ag["failure_taxonomy"][k] for k in ("routing_miss", "expansion_miss", "mixed_miss", "unattributed")) == len(worse)
    for r in rows:
        located = r["relevant_in_seed_modules"] + r["relevant_in_expanded_modules"] + r["relevant_excluded"]
        assert located == r["relevant_in_store"]
        if located:
            assert r["coverage_before_ranking"] == pytest.approx((r["relevant_in_seed_modules"] + r["relevant_in_expanded_modules"]) / located)
        if r["outcome"] == "graph_worse":
            assert r["relevant_lost_excluded"] >= 1 and r["category"] in ("routing_miss", "expansion_miss", "mixed_miss")
            assert r["flags"]["fallback_miss"] == (not r["fallback_used"])
        else:
            assert r["category"] is None
        for x in r["relevant_documents"]:
            if x["module_status"] == "excluded":
                assert not x["in_graph_top10"] or x["in_guided_only_top10"] or r["fallback_used"]
            if x["in_global_top10"] and not x["in_graph_top10"]:
                assert x["module_status"] == "excluded"  # a lost document can only be outside the searched modules
    assert out["examples"]["routing_miss"] is not None


def test_graph_never_changes_scores_only_the_searched_subset_and_global_equals_itself(tmp_path):
    """With routing wide open (every module a candidate) graph retrieval must equal global retrieval: no spurious loss."""
    _, _, out = _run(tmp_path, "diag_test_wide", seed_module_count=50, max_candidate_modules=50)
    ag = out["aggregate"]
    assert ag["outcomes_vs_global_ndcg10"].get("graph_worse", 0) == 0 and ag["outcomes_vs_global_ndcg10"].get("graph_better", 0) == 0
    assert all(abs(v) < 1e-9 for v in ag["metrics_delta_graph_minus_global_mean"].values())
    assert ag["coverage_before_ranking"]["relevant_docs_excluded"] == 0


def test_stage_timings_and_probe_curve_are_recorded_and_json_roundtrips(narrow):
    _, _, out = narrow
    st = out["aggregate"]["stage_timing_ms"]
    for who in ("global", "graph"):
        assert {"seed_ms", "expand_ms", "search_ms", "rank_ms", "fallback_ms", "total_ms"} <= set(st[who])
    assert st["global"]["seed_ms"] == 0 and st["graph"]["seed_ms"] > 0
    curve = out["aggregate"]["coverage_vs_probe_depth"]["curve"]
    shares = [c["share_of_relevant_docs_reachable"] for c in curve]
    assert shares == sorted(shares) and curve[0]["probe_modules"] == 1 and "diagnostic estimate" in out["aggregate"]["coverage_vs_probe_depth"]["kind"]
    json.loads(json.dumps(out, default=str))
