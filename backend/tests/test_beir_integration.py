"""Integration tests: the real evaluator, graph routing, adaptation, versioning and rollback on a tiny BEIR-format corpus.
Offline: hashing embedder (test-only fake), mongomock, in-memory Qdrant. Nothing is downloaded."""
import json
from pathlib import Path

import numpy as np
import pytest

from app.benchmark.beir_eval import evaluate_beir
from app.benchmark.corpus import (DocMap, GraphInitConfig, build_initial_architecture, ingest_corpus, make_context,
                                  verify_mapping)
from app.benchmark.datasets import doc_text, load_beir
from app.benchmark.report import batch_summary, text_report
from app.benchmark.runner import BenchmarkConfig, _scramble, run_dataset, validation_queries
from app.benchmark.service import BenchmarkService
from app.benchmark.splits import build_split_protocol, group_qrels
from app.container import build_context
from app.ingestion.embedder import HashingEmbedder
from tests.beir_fixture import write_beir
from tests.conftest import make_settings

RUN = "beir_itest_0001"


@pytest.fixture()
def env(tmp_path):
    write_beir(tmp_path / "beir")
    s = make_settings(tmp_path, beir_data_dir=str(tmp_path / "beir"), beir_artifacts_dir=str(tmp_path / "art"))
    cfg = BenchmarkConfig(datasets=["synth"], data_dir=str(tmp_path / "beir"), artifacts_dir=str(tmp_path / "art"),
                          graph=GraphInitConfig(target_module_size=20, min_module_size=3, min_modules=4),
                          adaptation_query_batch=20, max_windows=1, fallback_min_score=0.05, latency_repetitions=1,
                          bootstrap_resamples=200, max_proposals_per_window=4)
    return s, cfg, HashingEmbedder(384)


@pytest.fixture()
def finished(env):
    s, cfg, emb = env
    out = run_dataset(cfg, s, emb, "synth", RUN)
    return env, out, Path(cfg.artifacts_dir) / "synth" / RUN


def trec(path):
    res = {}
    for ln in path.read_text().splitlines():
        q, _, d, _, sc, _ = ln.split(" ")
        res.setdefault(q, {})[d] = float(sc)
    return res


def test_full_pipeline_saves_every_artifact_and_a_complete_manifest(finished):
    (s, cfg, emb), out, d = finished
    names = {p.name for p in d.iterdir()}
    assert {"manifest.json", "metrics.json", "per_query_metrics.json", "latency.json", "comparisons.json", "adaptation.json",
            "safety.json", "logs.jsonl", "run_bm25.trec", "run_global_dense.trec", "run_fixed_graph.trec", "run_adaptive_graph.trec"} <= names
    m = json.loads((d / "manifest.json").read_text())
    assert m["status"] == "completed" and m["run_id"] == RUN and m["dataset_id"] == "synth" and m["seed"] == 13
    assert m["official_test_split"] is True and m["smoke"] is False and m["truncated"] is False
    assert m["dataset"]["counts"]["corpus"] == 288 and m["dataset"]["test_group_queries"] == 48 and m["dataset"]["relevant_grades"] == [1, 2]
    assert m["embedding"]["model"] == "test/hashing-embedder" and m["graph_init"]["uses_queries_or_qrels"] is False
    assert m["acceptance_thresholds"]["max_ndcg_regression"] == 0.02 and m["acceptance_thresholds"]["require_benefit"] is True
    assert m["architecture_version_before"] and m["architecture_version_after"] and "perf_counter" in m["timing_methodology"]
    assert m["environment"]["packages"]["beir"] and m["id_mapping"]["verified"] is True
    assert (d.parent.parent / "synth" / RUN).exists() and json.loads((d / "logs.jsonl").read_text().splitlines()[0])["phase"] == "data"


def test_all_methods_use_the_same_queries_and_return_valid_original_document_ids(finished):
    (s, cfg, emb), out, d = finished
    ds = load_beir("synth", cfg.data_dir, ("train", "test"))
    qrels = group_qrels(ds, build_split_protocol(ds), "test")
    runs = {m: trec(d / f"run_{m}.trec") for m in ("bm25", "global_dense", "fixed_graph", "adaptive_graph")}
    for name, r in runs.items():
        assert set(r) == set(qrels), name  # identical query set for every method
        assert all(doc in ds.corpus for docs in r.values() for doc in docs), name  # original BEIR ids, never memory ids
        assert all(len(docs) <= 100 and all(np.isfinite(v) for v in docs.values()) for docs in r.values())
        assert not any(doc.startswith("mem_") for docs in r.values() for doc in docs)
    # the aggregate in metrics.json is exactly what BEIR computes on the saved run file
    saved = json.loads((d / "metrics.json").read_text())["methods"]
    for name, r in runs.items():
        again = evaluate_beir(qrels, r)
        assert again["ndcg_at_10"] == pytest.approx(saved[name]["ndcg_at_10"], abs=1e-5)
        assert saved[name]["queries_evaluated"] == len(qrels)
    assert json.loads((d / "metrics.json").read_text())["beir_vs_per_query_max_gap"]["global_dense"] < 1e-4


def test_global_searches_everything_and_graph_methods_narrow_the_pool(finished):
    (s, cfg, emb), out, d = finished
    lat = json.loads((d / "latency.json").read_text())["methods"]
    n = 288
    assert lat["global_dense"]["mean_eligible_candidates"] == n and lat["global_dense"]["candidate_pool_reduction_vs_corpus"] == 0
    assert lat["fixed_graph"]["mean_eligible_candidates"] < n and lat["fixed_graph"]["candidate_pool_reduction_vs_corpus"] > 0
    for m in ("global_dense", "fixed_graph", "adaptive_graph"):  # latency is decomposed and end-to-end includes embedding
        assert lat[m]["end_to_end"]["p50_ms"] >= lat[m]["retrieval"]["p50_ms"] > 0 and lat[m]["query_embedding"]["p50_ms"] > 0
    assert "stage_seed" in lat["fixed_graph"] and "stage_expand" in lat["fixed_graph"] and "stage_search" in lat["fixed_graph"]
    pq = json.loads((d / "per_query_metrics.json").read_text())["methods"]
    q = next(iter(pq["global_dense"]))
    assert pq["global_dense"][q]["embed_ms"] == pq["fixed_graph"][q]["embed_ms"]  # one shared set of dense query vectors
    sysinfo = json.loads((d / "latency.json").read_text())["system"]
    assert sysinfo["graph"]["initial"]["nodes"] > 0 and sysinfo["timings_s"]["corpus_embedding_s"] >= 0 and sysinfo["process_rss_mb"] > 0
    assert sysinfo["live_parity"]["checked"] > 0 and sysinfo["live_parity"]["identical_top10"] == sysinfo["live_parity"]["checked"]


def test_adaptive_equals_fixed_unless_the_adaptation_engine_committed_a_version(finished):
    (s, cfg, emb), out, d = finished
    a = json.loads((d / "adaptation.json").read_text())
    m = json.loads((d / "manifest.json").read_text())
    r_fixed, r_adapt = trec(d / "run_fixed_graph.trec"), trec(d / "run_adaptive_graph.trec")
    if a["accepted"] == 0:
        assert m["architecture_version_after"] == a["initial_version"] and r_fixed == r_adapt
    else:
        assert m["architecture_version_after"] != a["initial_version"]
    cmp = {c["comparison"]: c for c in json.loads((d / "comparisons.json").read_text())}
    b = cmp["adaptive_vs_fixed"]["bootstrap"]["ndcg_at_10"]
    assert b["resamples"] == 200 and b["n_queries"] == 48 and b["seed"] == 13
    assert {"fixed_vs_global", "adaptive_vs_global", "global_dense_vs_bm25"} <= set(cmp)
    assert a["proposals_evaluated"] == a["accepted"] + a["rejected"] + a["failed"] + a["rolled_back"] or a["proposals_evaluated"] >= a["accepted"]


def test_safety_probes_prove_rejection_rollback_and_restoration_on_the_benchmark_corpus(finished):
    (s, cfg, emb), out, d = finished
    sf = json.loads((d / "safety.json").read_text())
    h = sf["harmful_candidate"]
    assert h["status"] == "rejected" and h["rejection_stage"] == "pre_commit" and h["state_unchanged"] and h["version_unchanged"]
    assert h["candidate_ndcg_at_10"] < h["baseline_ndcg_at_10"]
    f = sf["post_commit_fault"]
    assert f["status"] == "rolled_back" and f["recovered"] and f["state_restored_exactly"] and f["integrity_passed"]
    r = sf["restoration"]
    assert r["restore_status"] == "completed" and r["state_matches_pre_change_snapshot"] and r["restored_again"] == "completed"
    assert r["state_matches_changed_architecture"] and r["integrity_passed"]


def test_results_are_retrievable_after_the_run_and_survive_a_service_restart(finished):
    (s, cfg, emb), out, d = finished
    sm = batch_summary(cfg.artifacts_dir, RUN)
    assert sm["datasets"]["synth"]["status"] == "completed" and set(sm["macro_average"]) == {"bm25", "global_dense", "fixed_graph", "adaptive_graph"}
    assert "OFFICIAL test" in text_report(cfg.artifacts_dir, RUN) and "macro-average" in text_report(cfg.artifacts_dir, RUN)
    ctx = build_context(s)
    try:
        ctx.mongo.col("benchmark_runs").insert_one({"run_id": "beir_zombie", "status": "running", "created_at": "2026-01-01T00:00:00Z",
                                                    "datasets": {"synth": {"status": "running"}}, "config": {}})
        svc = BenchmarkService(ctx)  # "restart"
        zombie = svc._job("beir_zombie")
        assert zombie["status"] == "failed" and "interrupted" in zombie["error"]
        ids = {r["run_id"] for r in svc.list_runs()}
        assert RUN in ids and "beir_zombie" in ids  # saved artifacts are listed again after a restart
        assert svc.results(RUN)["datasets"]["synth"]["metrics"]["global_dense"]["ndcg_at_10"] is not None
        svc.close()
    finally:
        ctx.close()


# --------------------------------------------------- real graph path, adaptation, version, rollback on BEIR documents
@pytest.fixture()
def bench_ctx(env):
    s, cfg, emb = env
    data = load_beir("synth", cfg.data_dir, ("train", "test"))
    ids = sorted(data.corpus)
    mat = emb.encode([doc_text(data.corpus[i]) for i in ids])
    dm = DocMap.build("synth", ids)
    state, _ = build_initial_architecture("synth", dm, ids, mat, data.corpus, cfg.graph)
    ctx = make_context(s, emb, "synth", "run_ctx00001", seed_module_count=2, max_candidate_modules=4, fallback_min_score=0.05,
                       adaptation_query_batch=20, eval_repetitions=1)
    ingest_corpus(ctx, "synth", data.corpus, ids, mat, dm, state)
    proto = build_split_protocol(data)
    vq, _ = validation_queries(data, proto, dm)
    ctx.datasets.replace_queries(vq, {"name": "validation"})
    yield ctx, data, dm, proto
    ctx.close()


def test_fixed_graph_retrieval_uses_graph_selected_modules_and_returns_beir_ids(bench_ctx):
    ctx, data, dm, proto = bench_ctx
    q = next(iter(data.queries["test"].values()))
    out = ctx.retrieval.query(q, 10, True)
    cand = {c["module_id"] for c in out["trace"]["candidate_modules"]}
    assert 0 < len(cand) <= 4 and {r["module_id"] for r in out["results"]} <= cand
    assert out["trace"]["candidate_count"] < len(data.corpus) or out["trace"]["fallback_used"]
    payload = ctx.qdrant.get_memories([out["results"][0]["memory_id"]])[out["results"][0]["memory_id"]][0]
    assert payload["beir_doc_id"] in data.corpus and dm.mem_to_doc[out["results"][0]["memory_id"]] == payload["beir_doc_id"]
    assert verify_mapping(ctx, dm)["verified"]


def _probe_pair(ctx, data, proto):
    ctx.settings.coaccess_min_count = ctx.settings.coaccess_min_window_queries = 1
    ctx.tracker.reset_window()
    for t in list(data.queries["train"].values())[:60]:
        ctx.retrieval.query(t, 10, False)
    w = ctx.tracker.window()
    return [k.split("|") for k, _ in sorted(w["pair_counts"].items(), key=lambda kv: -kv[1])], w


def test_invalid_candidate_is_rejected_and_never_becomes_active(bench_ctx):
    ctx, data, dm, proto = bench_ctx
    pairs, w = _probe_pair(ctx, data, proto)
    before, v = ctx.persistence.export_live_state().checksum(), ctx.versions.active_id()
    n_versions = ctx.mongo.col("architecture_versions").count_documents({})
    ev = ctx.adaptation.process("add_shortcut_edge", pairs[0], apply=True, window=w, plan_transform=lambda st: _scramble(st, 3))
    assert ev["status"] == "rejected" and ev["rejection_stage"] == "pre_commit" and ev["decision"] == "reject"
    assert ctx.persistence.export_live_state().checksum() == before and ctx.versions.active_id() == v
    assert ctx.mongo.col("architecture_versions").count_documents({}) == n_versions and ctx.validator.check()["passed"]


def test_accepted_adaptation_creates_a_version_and_post_commit_failure_rolls_back(bench_ctx):
    ctx, data, dm, proto = bench_ctx
    pairs, w = _probe_pair(ctx, data, proto)
    ctx.settings.require_benefit = False  # make a valid-but-neutral candidate acceptable; guardrails stay active
    before, v0 = ctx.persistence.export_live_state().checksum(), ctx.versions.active_id()
    ok = next(ev for ev in (ctx.adaptation.process("add_shortcut_edge", p, apply=True, window=w) for p in pairs[:6]) if ev["status"] == "completed")
    assert ok["new_version"] and ok["new_version"] != v0 and ctx.versions.active_id() == ok["new_version"]
    assert ctx.versions.verify(ok["new_version"])["valid"] and any(e.relation_type == "co_access" for e in ctx.persistence.export_live_state().edges.values())
    assert ok["evaluation"]["baseline"]["ndcg_at_10"] is not None and ok["evaluation"]["candidate"]["ndcg_at_10"] is not None
    # now an injected post-commit failure on another valid candidate: persistent state must be restored exactly
    mid, v1 = ctx.persistence.export_live_state().checksum(), ctx.versions.active_id()
    pairs2, w2 = pairs, w  # remaining pairs of the first replayed window
    linked = {frozenset((e.source, e.target)) for e in ctx.persistence.export_live_state().edges.values()}
    rb = None
    for p in [p for p in pairs2 if frozenset(p) not in linked][:8]:
        e = ctx.adaptation.process("add_shortcut_edge", p, apply=True, window=w2, fault="post_commit")
        if e["status"] != "rejected":
            rb = e
            break
    assert rb is not None and rb["status"] == "rolled_back" and rb["recovered"] and "post-commit" in rb["error"].lower()
    assert ctx.persistence.export_live_state().checksum() == mid and ctx.versions.active_id() in (v1, rb["rolled_back_to"])
    assert ctx.validator.check()["passed"] and before != mid
    # rollback of the accepted change itself restores the pre-adaptation architecture across all stores
    res = ctx.lifecycle.restore(v0, evaluate=False)
    exp, _ = ctx.lifecycle.reconcile_state(ctx.versions.snapshot_state(v0))
    assert res["status"] == "completed" and ctx.persistence.export_live_state().checksum() == exp.checksum()
    assert not ctx.persistence.export_live_state().co_access_edges() and ctx.validator.check()["passed"]
