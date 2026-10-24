"""Exact-versus-filtered retrieval: the store's module-filtered search must equal an exact ranking over the same candidate rows."""
import numpy as np
import pytest

from app.evaluation import parity as P
from app.storage.qdrant_store import QdrantStore


@pytest.fixture()
def store_and_data():
    rng = np.random.default_rng(7)
    n, dim = 300, 24
    mat = rng.normal(size=(n, dim)).astype(np.float32)
    ids = [f"m{i:03d}" for i in range(n)]
    mods = [f"mod{i % 6}" for i in range(n)]
    s = QdrantStore.in_memory(dim)
    s.upsert_memories([(ids[i], mat[i], {"memory_id": ids[i], "module_id": mods[i], "status": "active" if i % 25 else "archived"})
                       for i in range(n)])
    return s, mat, ids, mods, rng


def _exact(mat, ids, keep, q, k):
    rows = [i for i in range(len(ids)) if keep(i)]
    o = P.numpy_oracle(mat[rows], q, [ids[i] for i in rows], k)
    return [m for m, _ in o]


def test_global_search_equals_exact_ranking_over_active_points(store_and_data):
    s, mat, ids, mods, rng = store_and_data
    for _ in range(5):
        q = rng.normal(size=24).astype(np.float32)
        got = [h.memory_id for h in s.search_memories(q, None, 25)]
        assert got == _exact(mat, ids, lambda i: i % 25 != 0, q, 25)  # archived points never appear


def test_module_filtered_search_equals_exact_ranking_over_candidate_rows(store_and_data):
    s, mat, ids, mods, rng = store_and_data
    for cand in (["mod1"], ["mod0", "mod3"], ["mod2", "mod4", "mod5"]):
        q = rng.normal(size=24).astype(np.float32)
        got = s.search_memories(q, cand, 15)
        assert [h.memory_id for h in got] == _exact(mat, ids, lambda i: mods[i] in cand and i % 25 != 0, q, 15)
        assert {h.module_id for h in got} <= set(cand)  # a hard filter can never return a document outside the candidate modules


def test_filtered_scores_equal_global_scores_for_the_same_document(store_and_data):
    s, mat, ids, mods, rng = store_and_data
    q = rng.normal(size=24).astype(np.float32)
    glob = {h.memory_id: h.score for h in s.search_memories(q, None, 300)}
    for h in s.search_memories(q, ["mod2", "mod5"], 40):
        assert h.score == pytest.approx(glob[h.memory_id], abs=1e-6)  # routing only changes the searched subset, never a score


def test_empty_candidate_list_returns_nothing_and_unknown_module_returns_nothing(store_and_data):
    s, _, _, _, rng = store_and_data
    q = rng.normal(size=24).astype(np.float32)
    assert s.search_memories(q, [], 10) == [] and s.search_memories(q, ["no-such-module"], 10) == []
