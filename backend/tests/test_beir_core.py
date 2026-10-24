"""Offline unit tests for the BEIR benchmark infrastructure (no network, no model download)."""
import math
import random

import numpy as np
import pytest

from app.benchmark.beir_eval import (K_VALUES, check_results, consistency_gap, evaluate_beir, paired_bootstrap,
                                     per_query_metrics)
from app.benchmark.bm25 import BM25Index
from app.benchmark.corpus import (DocMap, GraphInitConfig, build_initial_architecture, embed_corpus, ingest_corpus,
                                  make_context, memory_id_for, verify_mapping)
from app.benchmark.datasets import (DatasetInvalid, DatasetNotAvailable, doc_text, is_cached, list_datasets, load_beir,
                                    validate_beir)
from app.benchmark.methods import summarize_latency, MethodRun
from app.benchmark.splits import assert_disjoint, build_split_protocol, group_qrels
from app.evaluation.metrics import (average_precision_at_k, ndcg_at_k, percentile, precision_at_k, ranking_metrics)
from app.evaluation.version_comparator import AcceptancePolicy
from app.ingestion.embedder import HashingEmbedder
from tests.beir_fixture import write_beir
from tests.conftest import make_settings


@pytest.fixture()
def beir_dir(tmp_path):
    write_beir(tmp_path / "beir")
    return str(tmp_path / "beir")


# ------------------------------------------------------------------ loading and validation
def test_loader_uses_official_format_and_keeps_grades(beir_dir):
    d = load_beir("synth", beir_dir, ("train", "test"))
    assert len(d.corpus) == 8 * 36 and d.counts()["test_queries"] == 48 and d.counts()["train_queries"] == 80
    assert {g for q in d.qrels["test"].values() for g in q.values()} == {1, 2}  # graded judgments survive loading
    assert is_cached(beir_dir, "synth") and not is_cached(beir_dir, "nfcorpus")
    rows = {r["dataset_id"]: r for r in list_datasets(beir_dir)}
    assert rows["synth"]["cached"] and rows["synth"]["corpus_size"] == 288 and rows["nfcorpus"]["cached"] is False


def test_missing_dataset_is_an_explicit_error_never_a_substitute(tmp_path):
    with pytest.raises(DatasetNotAvailable) as e:
        load_beir("scifact", str(tmp_path), ("test",))
    assert "not cached" in e.value.message and "No substitute" in e.value.message


def test_unknown_split_and_path_traversal_rejected(beir_dir):
    with pytest.raises(DatasetNotAvailable):
        load_beir("synth", beir_dir, ("dev",))
    with pytest.raises(DatasetInvalid):
        load_beir("../etc", beir_dir, ("test",))


def test_validation_catches_malformed_qrels_and_duplicate_ids():
    corpus = {"d1": {"title": "t", "text": "x"}, "d2": {"title": "", "text": ""}}
    issues = validate_beir(corpus, {"q1": "a", "q2": "b"}, {"q1": {"d1": -1}, "q2": {"d1": 0}, "q9": {"d1": 1}}, corpus_lines=3)
    text = " ".join(issues)
    assert "duplicate document ids" in text and "neither title nor text" in text and "not in the query file" in text
    assert "negative" in text and "no relevant" in text
    # judgments for documents that are absent from the corpus are a recorded caveat unless strict
    assert validate_beir({"d1": {"title": "t", "text": "x"}}, {"q": "a"}, {"q": {"d1": 1, "ghost": 1}}) == []
    assert any("not in the corpus" in i for i in validate_beir({"d1": {"title": "t", "text": "x"}}, {"q": "a"},
                                                                {"q": {"d1": 1, "ghost": 1}}, strict_qrels=True))


def test_doc_text_handles_missing_title_and_text():
    assert doc_text({"title": "T", "text": "body"}) == "T body"
    assert doc_text({"title": None, "text": "body"}) == "body" and doc_text({"title": "T", "text": None}) == "T"
    assert doc_text({}) == ""


# ------------------------------------------------------------------------ id mapping
def test_doc_map_is_stable_unique_and_roundtrips_through_qdrant(beir_dir, tmp_path):
    d = load_beir("synth", beir_dir, ("train", "test"))
    ids = sorted(d.corpus)
    m1, m2 = DocMap.build("synth", ids), DocMap.build("synth", ids)
    assert m1.doc_to_mem == m2.doc_to_mem and len(set(m1.doc_to_mem.values())) == len(ids)
    assert m1.doc_to_mem["ast-001"] == memory_id_for("synth", "ast-001") and m1.docs([m1.doc_to_mem["cooking"[:3] + "-002"]]) == ["coo-002"]
    emb = HashingEmbedder(384)
    ctx_settings = make_settings(tmp_path, beir_data_dir=beir_dir, beir_artifacts_dir=str(tmp_path / "a"))
    mat = emb.encode([doc_text(d.corpus[i]) for i in ids])
    state, _ = build_initial_architecture("synth", m1, ids, mat, d.corpus, GraphInitConfig(target_module_size=20, min_module_size=3, min_modules=4))
    ctx = make_context(ctx_settings, emb, "synth", "run_test0001")
    try:
        ingest_corpus(ctx, "synth", d.corpus, ids, mat, m1, state)
        assert verify_mapping(ctx, m1)["documents"] == len(ids)
        # original BEIR id, dataset id and title are in the payload; a missing title is stored safely
        payloads = {p["beir_doc_id"]: p for _, p, _ in ctx.qdrant.scroll_memories()}
        assert payloads["ast-000"]["dataset_id"] == "synth" and payloads["ast-000"]["title"] is None
        assert payloads["ast-001"]["title"] == "astronomy study 1" and payloads["ast-001"]["status"] == "active"
        # tamper with one payload: the mapping check must notice
        some = ctx.qdrant.get_memories([m1.doc_to_mem["ast-001"]])
        ctx.qdrant.set_payload([m1.doc_to_mem["ast-001"]], {"beir_doc_id": "wrong"})
        with pytest.raises(ValueError):
            verify_mapping(ctx, m1)
        assert some
    finally:
        ctx.close()


def test_embedding_cache_is_reused_and_invalidated_by_content(beir_dir, tmp_path):
    d = load_beir("synth", beir_dir, ("test",))
    emb = HashingEmbedder(384)
    ids1, m1, i1 = embed_corpus(emb, "synth", d.corpus, "sha-a", str(tmp_path / "emb"))
    ids2, m2, i2 = embed_corpus(emb, "synth", d.corpus, "sha-a", str(tmp_path / "emb"))
    assert i1["cache_hit"] is False and i2["cache_hit"] is True and np.array_equal(m1, m2) and ids1 == ids2
    assert m1.shape == (288, 384) and np.isfinite(m1).all()
    _, _, i3 = embed_corpus(emb, "synth", d.corpus, "sha-b", str(tmp_path / "emb"))  # corpus content changed
    assert i3["cache_hit"] is False


# --------------------------------------------------------------------------- metrics
def test_ranking_metrics_hand_computed():
    grades = {"a": 2, "b": 1, "c": 1}
    ranked = ["x", "a", "y", "b"] + [f"f{i}" for i in range(20)]
    dcg = 2 / math.log2(3) + 1 / math.log2(5)
    idcg = 2 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(grades, ranked, 10) == pytest.approx(dcg / idcg)
    assert precision_at_k(grades, ranked, 10) == pytest.approx(0.2)
    assert average_precision_at_k(grades, ranked, 10) == pytest.approx((1 / 2 + 2 / 4) / 3)  # divided by ALL relevant docs
    m = ranking_metrics(grades, ranked)
    assert m["mrr_at_10"] == 0.5 and m["recall_at_5"] == pytest.approx(2 / 3) and m["recall_at_10"] == pytest.approx(2 / 3)
    assert m["recall_at_100"] == pytest.approx(2 / 3) and ndcg_at_k({"a": 1}, ["z"], 10) == 0.0


def test_graded_relevance_is_not_binarised_for_ndcg():
    ranked = ["b", "a"]  # the grade-1 doc is ranked above the grade-2 doc
    assert ndcg_at_k({"a": 2, "b": 1}, ranked, 10) < 1.0
    assert ndcg_at_k({"a": 1, "b": 1}, ranked, 10) == 1.0  # binarised labels would call this perfect


def _random_run(qrels, docs, seed):
    rng = random.Random(seed)
    results = {}
    for q, rels in qrels.items():
        pool = rng.sample(docs, 60) + [d for d in rels if rng.random() < 0.7]
        results[q] = {d: rng.random() for d in dict.fromkeys(pool)}
    return results


def test_beir_aggregates_match_per_query_metrics_and_own_implementation(beir_dir):
    d = load_beir("synth", beir_dir, ("test",))
    qrels = d.qrels["test"]
    results = _random_run(qrels, sorted(d.corpus), 1)
    agg = evaluate_beir(qrels, results)
    pq = per_query_metrics(qrels, results)
    assert consistency_gap(agg, pq) < 1e-4  # BEIR rounds to 5 decimals
    # our own ranking_metrics (used during candidate validation) agree with pytrec_eval per query
    for q in list(qrels)[:15]:
        ranked = [x for x, _ in sorted(results[q].items(), key=lambda kv: kv[1], reverse=True)]
        mine = ranking_metrics(qrels[q], ranked)
        for k in ("ndcg_at_10", "map_at_10", "precision_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10"):
            assert mine[k] == pytest.approx(pq[q][k], abs=1e-9), (q, k)
    assert set(agg["raw"]) >= {"NDCG@10", "MAP@10", "Recall@5", "Recall@10", "Recall@100", "P@10", "MRR@10"}
    assert agg["queries_evaluated"] == len(qrels) and K_VALUES == [1, 3, 5, 10, 100]


def test_unanswered_queries_and_identical_ids_are_handled_explicitly():
    qrels = {"q1": {"d1": 1}, "q2": {"d2": 1}}
    with pytest.raises(ValueError):  # pytrec_eval would silently skip q2 and inflate every score
        check_results(qrels, {"q1": {"d1": 1.0}})
    with pytest.raises(ValueError):
        check_results(qrels, {"q1": {"d1": float("nan")}, "q2": {}})
    # BEIR default: a document whose id equals the query id is not a valid answer (ArguAna/Quora convention)
    res = {"q1": {"q1": 9.0, "d1": 1.0}, "q2": {"d2": 1.0}}
    agg = evaluate_beir({"q1": {"q1": 1, "d1": 1}, "q2": {"d2": 1}}, res)
    assert agg["mrr_at_10"] == 1.0 and res["q1"]["q1"] == 9.0  # input not mutated


def test_candidate_count_definition_and_percentile(seeded):
    ctx = seeded
    out = ctx.retrieval.query("django url routing views", 3, True)
    t = out["trace"]
    in_modules = sum(ctx.graph.graph.nodes[c["module_id"]]["active_record_count"] for c in t["candidate_modules"])
    scanned_via_store = ctx.qdrant.count_memories([c["module_id"] for c in t["candidate_modules"]])
    assert t["guided_candidate_count"] == in_modules == scanned_via_store  # eligible documents, not "vectors scanned"
    assert percentile(list(range(1, 101)), 95) == pytest.approx(95.05)


def test_latency_summary_separates_stages_and_reports_pool_reduction():
    lat = {f"q{i}": {"retrieval_ms": 10.0 + i, "embed_ms": 5.0, "e2e_ms": 15.0 + i, "seed_ms": 1.0, "expand_ms": 0.5, "search_ms": 8.0,
                     "fallback_ms": 0.0, "eligible_candidates": 100.0, "candidates_incl_fallback": 100.0, "fallback": 0.0,
                     "results_returned": 100.0} for i in range(20)}
    s = summarize_latency(MethodRun("fixed_graph", {}, lat), 1000)
    assert s["candidate_pool_reduction_vs_corpus"] == pytest.approx(0.9) and s["fallback_rate"] == 0.0
    assert s["retrieval"]["p95_ms"] == pytest.approx(percentile([10.0 + i for i in range(20)], 95))
    assert s["query_embedding"]["p50_ms"] == 5.0 and s["stage_seed"]["mean_ms"] == 1.0 and s["total_results"] == 2000


# ------------------------------------------------------------------------ BM25
def test_bm25_matches_hand_computation_and_ranks_matching_documents_first():
    docs = {"d1": "apple banana apple", "d2": "banana cherry", "d3": "cherry cherry cherry"}
    idx = BM25Index(list(docs), list(docs.values()), k1=1.2, b=0.75)
    n, avgdl = 3, (3 + 2 + 3) / 3
    idf_apple = math.log(1 + (3 - 1 + 0.5) / (1 + 0.5))
    expected = idf_apple * 2 * 2.2 / (2 + 1.2 * (1 - 0.75 + 0.75 * 3 / avgdl))
    s = idx.scores("apple")
    assert s[0] == pytest.approx(expected) and s[1] == 0 and s[2] == 0 and n == 3
    res, times = idx.search({"q": "cherry"}, 2)
    assert list(res["q"])[0] == "d3" and set(times) == {"q"} and idx.scores("zzz").sum() == 0
    assert idx.search({"q": "banana apple"}, 3)[0]["q"] == idx.search({"q": "banana apple"}, 3)[0]["q"]  # deterministic


# ---------------------------------------------------------------- graph initialisation
def test_initial_graph_is_deterministic_valid_and_label_free(beir_dir, tmp_path):
    d = load_beir("synth", beir_dir, ("train", "test"))
    ids = sorted(d.corpus)
    mat = HashingEmbedder(384).encode([doc_text(d.corpus[i]) for i in ids])
    dm = DocMap.build("synth", ids)
    cfg = GraphInitConfig(target_module_size=20, min_module_size=5, min_modules=4, seed=13)
    s1, i1 = build_initial_architecture("synth", dm, ids, mat, d.corpus, cfg)
    s2, _ = build_initial_architecture("synth", dm, ids, mat, d.corpus, cfg)
    assert s1.checksum() == s2.checksum() and s1.validate(384) == []
    s3, _ = build_initial_architecture("synth", dm, ids, mat, d.corpus, GraphInitConfig(target_module_size=20, min_module_size=5, min_modules=4, seed=99))
    assert s3.checksum() != s1.checksum()
    assert i1["uses_queries_or_qrels"] is False and i1["initial_edges"] == len(s1.edges)
    assert all(e.relation_type == "hierarchy" for e in s1.edges.values())  # edge policy: hierarchy only
    sizes = [len(v) for v in s1.assignments.values() if v]
    assert min(sizes) >= 5 and sum(sizes) == len(ids)  # every document in exactly one module, min size enforced
    assert set(s1.module_of()) == set(dm.mem_to_doc)


# --------------------------------------------------------------------- splits / leakage
def test_split_protocol_official_train_test_is_disjoint_and_deterministic(beir_dir):
    d = load_beir("synth", beir_dir, ("train", "test"))
    p = build_split_protocol(d, seed=13)
    assert p.official_test and len(p.test) == 48 and len(p.workload) + len(p.validation) == 80 - p.dropped_for_text_overlap
    assert not set(p.workload) & set(p.validation) and not set(p.test) & (set(p.workload) | set(p.validation))
    assert_disjoint(p, d)
    assert p.to_dict() == build_split_protocol(d, seed=13).to_dict()
    assert build_split_protocol(d, seed=14).workload != p.workload
    with pytest.raises(PermissionError):  # workload is used without judgments by construction
        group_qrels(d, p, "workload")


def test_split_protocol_drops_train_queries_that_duplicate_a_test_query(tmp_path):
    write_beir(tmp_path / "b2", "dup", dup_query_text=True)
    d = load_beir("dup", str(tmp_path / "b2"), ("train", "test"))
    p = build_split_protocol(d)
    assert p.dropped_for_text_overlap >= 1
    texts = lambda ids, sp: {d.queries[sp][i].strip().lower() for i in ids}  # noqa: E731
    assert not texts(p.workload, "train") & texts(p.test, "test") and not texts(p.validation, "train") & texts(p.test, "test")


def test_single_split_dataset_gets_internal_partition_flagged_non_official(tmp_path):
    write_beir(tmp_path / "b3", "only", splits=("test",))
    d = load_beir("only", str(tmp_path / "b3"), ("test",))
    p = build_split_protocol(d)
    assert not p.official_test and "INTERNAL" in p.notes
    assert len(p.workload) + len(p.validation) + len(p.test) == 48 and len(p.test) > len(p.validation)
    assert_disjoint(p, d)


def test_dev_split_is_used_for_validation_when_present(tmp_path):
    write_beir(tmp_path / "b4", "full", splits=("train", "dev", "test"))
    d = load_beir("full", str(tmp_path / "b4"), ("train", "dev", "test"))
    p = build_split_protocol(d)
    assert p.official_test and set(p.validation) <= set(d.queries["dev"]) and set(p.workload) <= set(d.queries["train"])
    assert p.pools == {"workload": "train", "validation": "dev", "test": "test"}


def test_query_limit_marks_the_run_non_official(beir_dir):
    d = load_beir("synth", beir_dir, ("train", "test"))
    p = build_split_protocol(d, query_limit=10)
    assert not p.official_test and p.truncated and len(p.test) == 10 and "SMOKE" in p.notes


# ------------------------------------------------------------ acceptance thresholds
def rep(ndcg=0.6, r5=0.7, mrr=0.5, p95=10.0, cand=100.0, n=50, valid=True):
    return {"valid": valid, "invalid_reason": None if valid else "insufficient",
            "metrics": {"ndcg_at_10": ndcg, "recall_at_5": r5, "mrr": mrr, "mrr_at_10": mrr, "p95_latency_ms": p95,
                        "mean_candidate_count": cand, "sample_size": n, "fallback_rate": 0.0},
            "conditions": {"dataset_hash": "h", "queries_evaluated": n}}


@pytest.fixture()
def beir_policy(tmp_path):
    return AcceptancePolicy(make_settings(tmp_path, acceptance_profile="beir", require_benefit=True, latency_noise_floor_ms=0.5))


def test_beir_guardrails_each_reject_a_regression(beir_policy):
    for kw in (dict(ndcg=0.57), dict(r5=0.67), dict(mrr=0.47), dict(p95=12.0, cand=50.0)):
        d = beir_policy.decide(rep(), rep(**kw))
        assert not d["accepted"] and d["reason"].startswith("Rejected"), kw
    names = {c["name"] for c in beir_policy.decide(rep(), rep(ndcg=0.6)).get("checks", [])}
    assert {"ndcg_at_10", "recall_at_5", "mrr_at_10", "p95_latency_ms"} <= names


def test_a_change_must_show_a_meaningful_benefit(beir_policy):
    neutral = beir_policy.decide(rep(), rep())
    assert not neutral["accepted"] and "no meaningful benefit" in neutral["reason"]
    assert beir_policy.decide(rep(), rep(ndcg=0.61))["accepted"]  # +0.01 nDCG@10 >= 0.005
    assert beir_policy.decide(rep(), rep(cand=90.0))["accepted"]  # -10 % candidate pool >= 5 %
    assert not beir_policy.decide(rep(), rep(cand=98.0))["accepted"]  # -2 % is not meaningful
    assert beir_policy.decide(rep(p95=10.0), rep(p95=5.0))["accepted"]  # -50 % p95, above the noise floor
    assert not beir_policy.decide(rep(p95=0.30), rep(p95=0.10))["accepted"]  # below the noise floor
    # a large benefit never excuses a guardrail violation
    assert not beir_policy.decide(rep(), rep(ndcg=0.5, cand=10.0))["accepted"]


def test_missing_or_insufficient_evaluation_data_is_never_a_pass(beir_policy):
    d = beir_policy.decide(rep(valid=False, n=3), rep(valid=False, n=3, ndcg=0.9))
    assert not d["accepted"] and "insufficient" in d["reason"].lower()
    assert beir_policy.thresholds()["require_benefit"] is True and beir_policy.thresholds()["profile"] == "beir"


# ---------------------------------------------------------------------- statistics
def test_paired_bootstrap_is_deterministic_paired_and_honest_about_no_effect():
    rng = np.random.default_rng(0)
    a = rng.random(200).tolist()
    b = [x + 0.1 for x in a]
    r = paired_bootstrap(a, b, 1000, 13)
    assert r["mean_diff"] == pytest.approx(0.1) and r["excludes_zero"] and r["ci95_low"] == pytest.approx(0.1) and r["resamples"] == 1000
    assert r == paired_bootstrap(a, b, 1000, 13) and r["queries_improved"] == 200
    noise = paired_bootstrap(a, (np.array(a) + rng.normal(0, 0.2, 200)).tolist(), 1000, 13)
    assert noise["ci95_low"] < 0 < noise["ci95_high"] and not noise["excludes_zero"]
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [1.0, 2.0])
