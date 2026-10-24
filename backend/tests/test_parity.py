"""Regression tests for the live-versus-sandbox parity investigation (docs/BEIR_PARITY_INVESTIGATION.md).

Demonstrated discrepancy: documents with bit-identical vectors tie exactly; (1) the old parity check ordered the live ranking by memory id
but the sandbox ranking by BEIR document id, and (2) a vector store returns an insertion-order-dependent subset of a tie group that
straddles the search limit. Offline: hashing embedder (test-only fake), mongomock, in-memory Qdrant."""
import random

import numpy as np

from app.benchmark.corpus import (DocMap, GraphInitConfig, build_initial_architecture, ingest_corpus, make_context,
                                  verify_mapping)
from app.benchmark.datasets import doc_text
from app.benchmark.methods import FALLBACK_K, sandbox_engine
from app.evaluation import parity as P
from app.ingestion.embedder import HashingEmbedder
from app.retrieval.ranker import rank_hits
from app.storage.qdrant_store import Hit, QdrantStore
from tests.conftest import make_settings


# ---------------------------------------------------------------------------------------------- vector-store ties
def _dup_items(dim=16, n_unique=40, n_dup=12):
    rng = np.random.default_rng(0)
    base = rng.normal(size=(n_unique, dim)).astype(np.float32)
    dup = rng.normal(size=dim).astype(np.float32)
    return [(f"u{i}", base[i]) for i in range(n_unique)] + [(f"d{i:02d}", dup) for i in range(n_dup)], dup


def _search(items, order_seed, q, limit, tie_safe):
    order = list(items)
    random.Random(order_seed).shuffle(order)
    s = QdrantStore.in_memory(16, tie_safe=tie_safe)
    s.upsert_memories([(m, v, {"memory_id": m, "module_id": "x" if i % 2 else "y", "status": "active"}) for i, (m, v) in enumerate(order)])
    return s, [h.memory_id for h in s.search_memories(q, None, limit)]


def test_tie_group_straddling_the_limit_is_independent_of_insertion_order():
    items, q = _dup_items()
    outs = {tuple(_search(items, seed, q, 5, True)[1]) for seed in range(6)}
    assert outs == {("d00", "d01", "d02", "d03", "d04")}  # exact ties resolve by id, whatever the insertion order


def test_tie_safe_search_matches_plain_search_when_there_is_no_tie():
    items, _ = _dup_items()
    q = items[3][1]  # unique vector -> no tie at the boundary
    _, safe = _search(items, 1, q, 7, True)
    _, plain = _search(items, 1, q, 7, False)
    assert safe == plain and safe[0] == "u3"


def test_tie_safe_search_respects_module_filters_and_group_larger_than_the_margin():
    items, q = _dup_items(n_dup=30)
    s, _ = _search(items, 2, q, 3, True)
    got = [h.memory_id for h in s.search_memories(q, ["x"], 4)]
    assert got == sorted(got) and all(g.startswith("d") for g in got) and len(got) == 4  # filter honoured, deterministic ids
    every = {m for m, _ in items if m.startswith("d")}
    assert len(s.search_memories(q, None, 100)) == len(items) and every  # limit above corpus size returns everything


def test_rank_hits_breaks_exact_ties_on_memory_id():
    hits = [Hit(m, "x", 0.5, {}) for m in ("m3", "m1", "m2")] + [Hit("m0", "x", 0.9, {})]
    assert [h.memory_id for h in rank_hits(hits, 4)] == ["m0", "m1", "m2", "m3"]


# ------------------------------------------------------------------------------------------ classification helpers
def test_first_divergence_reports_the_earliest_stage():
    base = {"seed_modules": [("a", 0.9)], "candidate_modules": ["a", "b"], "guided_space": 10,
            "guided_ranking": [("x", 0.8), ("y", 0.7)], "fallback_decision": {"used": False, "reason": None},
            "final_ranking": [("x", 0.8), ("y", 0.7)]}
    assert P.first_divergence(base, dict(base)) is None
    assert P.first_divergence(base, {**base, "final_ranking": [("y", 0.7), ("x", 0.8)]}) == "final_ranking"
    assert P.first_divergence(base, {**base, "fallback_decision": {"used": True, "reason": "r"}}) == "fallback_decision"
    assert P.first_divergence(base, {**base, "guided_ranking": [("y", 0.7), ("x", 0.8)], "final_ranking": []}) == "guided_ranking"
    assert P.first_divergence(base, {**base, "guided_space": 11, "final_ranking": []}) == "guided_space"
    assert P.first_divergence(base, {**base, "candidate_modules": ["a"], "guided_space": 1}) == "candidate_modules"
    assert P.first_divergence(base, {**base, "seed_modules": [("b", 0.9)]}) == "seed_modules"


def test_classification_separates_ties_from_real_score_disagreement():
    a = [("p", 0.9), ("q", 0.5), ("r", 0.5), ("s", 0.1)]
    assert P.classify_ranking_difference(a, [("p", 0.9), ("r", 0.5), ("q", 0.5), ("s", 0.1)])["cause"] == "exact_score_tie"
    assert P.classify_ranking_difference(a, [("p", 0.9), ("r", 0.5 + 3e-3), ("q", 0.5), ("s", 0.1)])["cause"] == "score_disagreement"
    noisy = P.classify_ranking_difference([("p", 0.9), ("q", 0.5), ("r", 0.5 - 4e-7)], [("p", 0.9), ("r", 0.5 - 4e-7), ("q", 0.5)])
    assert noisy["cause"] == "float_noise_tie"
    assert P.classify_ranking_difference(a, [("p", 0.9), ("q", 0.4), ("r", 0.5), ("s", 0.1)])["cause"] == "score_disagreement"
    assert P.classify_ranking_difference(a, a)["cause"] == "identical"


def test_numpy_oracle_orders_by_score_then_id_and_cutoff_ties_are_detected():
    mat = np.array([[1, 0], [1, 0], [0, 1], [0.6, 0.8]], dtype=np.float32)
    o = P.numpy_oracle(mat, np.array([1.0, 0.0]), ["b", "a", "c", "d"], 4)
    assert [i for i, _ in o] == ["a", "b", "d", "c"]
    assert P.cutoff_ties(o, (1, 2)) == {"tie_at_1": True, "tie_at_2": False}


# ------------------------------------------------------------------------- live engine vs sandbox, duplicated documents
def _dup_corpus():
    topics = {"alpha": "river water flow", "beta": "market price stock", "gamma": "protein cell gene"}
    corpus = {}
    for t, words in topics.items():
        for i in range(24):
            corpus[f"{t}-{i:02d}"] = {"title": f"{t} study {i}", "text": f"{words} detailed report number {i} about {t}"}
        for i in range(8):  # exact duplicates under different ids (identical title+text -> bit-identical vectors)
            corpus[f"{t}-dup-{i}"] = {"title": f"{t} shared notice", "text": f"{words} repeated notice about {t}"}
    return corpus


def test_live_and_sandbox_engines_agree_on_duplicate_documents_but_legacy_check_does_not(tmp_path):
    emb = HashingEmbedder(384)
    corpus = _dup_corpus()
    ids = sorted(corpus)
    mat = emb.encode([doc_text(corpus[i]) for i in ids]).astype(np.float32)
    dm = DocMap.build("dupes", ids)
    vectors = {dm.doc_to_mem[d]: mat[i] for i, d in enumerate(ids)}
    state, _ = build_initial_architecture("dupes", dm, ids, mat, corpus, GraphInitConfig(target_module_size=20, min_module_size=3, min_modules=4))
    s = make_settings(tmp_path, seed_module_count=3, max_candidate_modules=8, fallback_min_score=0.05)
    ctx = make_context(s, emb, "dupes", "parity_test_0001", seed_module_count=3, max_candidate_modules=8, fallback_min_score=0.05)
    try:
        ingest_corpus(ctx, "dupes", corpus, ids, mat, dm, state)
        verify_mapping(ctx, dm)
        live_state = ctx.persistence.export_live_state()
        sandbox = sandbox_engine(ctx, live_state, vectors)
        assert P.graph_diff(ctx.retrieval.engine.graph_provider(), sandbox.graph_provider())["identical"]
        tg = P.tie_groups(mat, ids)
        assert tg["groups_of_identical_vectors"] == 3 and tg["largest_group"] == 8
        texts = ["river water flow notice", "market price stock notice", "protein cell gene notice", "shared notice", "repeated notice about alpha"]
        qv = emb.encode(texts)
        legacy_mismatch = 0
        for q in qv:
            for depth in (5, 100):
                lo = ctx.retrieval.engine.retrieve(q, depth, "graph", fallback_k=FALLBACK_K)
                so = sandbox.retrieve(q, depth, "graph", fallback_k=FALLBACK_K)
                a, b = P.stages_of(lo), P.stages_of(so)
                assert P.first_divergence(a, b, depth) is None, (depth, a["final_ranking"][:6], b["final_ranking"][:6])
                assert [m for m, _ in a["final_ranking"]] == [m for m, _ in b["final_ranking"]]  # same order, not only same set
            live_docs = [dm.mem_to_doc[m] for m, _ in a["final_ranking"]]
            sand_docs = P.harness_style_ranking({dm.mem_to_doc[m]: sc for m, sc in b["final_ranking"]})
            legacy_mismatch += int(live_docs[:10] != sand_docs[:10])
        assert legacy_mismatch >= 1  # the original check (memory-id vs document-id tie-break) misreports exact ties as divergence
    finally:
        ctx.close()
