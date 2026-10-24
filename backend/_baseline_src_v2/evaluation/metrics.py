"""Retrieval metrics. Definitions (also documented in docs/EVALUATION_AND_ROLLBACK.md):

* Recall@k  — fraction of the expected relevant memory IDs that appear in the top-k results.
* MRR       — mean over queries of 1/rank of the first relevant result within the top-k list (0 if none).
* p95       — 95th percentile (linear interpolation) of per-query latency across the evaluation run.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np


def recall_at_k(relevant: Iterable[str], retrieved: Sequence[str], k: int = 5) -> float:
    rel = set(relevant)
    if not rel:
        raise ValueError("recall requires at least one relevant id")
    return len(rel & set(retrieved[:k])) / len(rel)


def reciprocal_rank(relevant: Iterable[str], retrieved: Sequence[str], k: int | None = None) -> float:
    rel = set(relevant)
    for i, mid in enumerate(retrieved[:k] if k else retrieved, start=1):
        if mid in rel:
            return 1.0 / i
    return 0.0


def percentile(values: Sequence[float], p: float) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return float(np.percentile(np.asarray(vals, dtype=np.float64), p, method="linear"))


def mean(values: Sequence[float]) -> float | None:
    return float(sum(values) / len(values)) if values else None


# ---- graded / ranked metrics with trec_eval semantics (cross-checked against BEIR/pytrec_eval in the tests) -----------
def ndcg_at_k(grades: dict[str, int], retrieved: Sequence[str], k: int = 10) -> float:
    """nDCG with linear gain = relevance grade (BEIR/pytrec_eval `ndcg_cut`). Graded judgments are NOT binarised."""
    gains = [max(0, grades.get(d, 0)) for d in retrieved[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0


def precision_at_k(grades: dict[str, int], retrieved: Sequence[str], k: int = 10) -> float:
    """Relevant = grade >= 1; the denominator is k even when fewer documents were returned (trec_eval `P`)."""
    return sum(1 for d in retrieved[:k] if grades.get(d, 0) > 0) / k


def average_precision_at_k(grades: dict[str, int], retrieved: Sequence[str], k: int = 10) -> float:
    """trec_eval `map_cut`: sum of precision at each relevant rank <= k, divided by the TOTAL number of relevant documents."""
    n_rel = sum(1 for g in grades.values() if g > 0)
    if n_rel == 0:
        return 0.0
    hits, total = 0, 0.0
    for i, d in enumerate(retrieved[:k], start=1):
        if grades.get(d, 0) > 0:
            hits += 1
            total += hits / i
    return total / n_rel


def ranking_metrics(grades: dict[str, int], retrieved: Sequence[str]) -> dict[str, float]:
    """All per-query ranking metrics MORPHE reports, from one ranked list (which may be deeper than 10)."""
    rel = {d for d, g in grades.items() if g > 0}
    out = {
        "ndcg_at_10": ndcg_at_k(grades, retrieved, 10),
        "map_at_10": average_precision_at_k(grades, retrieved, 10),
        "precision_at_10": precision_at_k(grades, retrieved, 10),
        "mrr_at_10": reciprocal_rank(rel, retrieved, 10),
    }
    for k in (5, 10, 100):
        out[f"recall_at_{k}"] = recall_at_k(rel, retrieved, k) if rel else 0.0
    return out


def paired_bootstrap(a: list[float], b: list[float], n_resamples: int = 1000, seed: int = 13) -> dict[str, Any]:
    """Paired query-level percentile bootstrap of mean(b − a). The unit of resampling is the query; both systems share each
    resample. Assumes queries are exchangeable draws; says nothing about generalisation to other query distributions."""
    if len(a) != len(b) or not a:
        raise ValueError("paired samples must be non-empty and equal length")
    d = np.asarray(b, dtype=np.float64) - np.asarray(a, dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_resamples, len(d)))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"mean_diff": float(d.mean()), "ci95_low": float(lo), "ci95_high": float(hi), "n_queries": int(len(d)),
            "resamples": n_resamples, "seed": seed, "excludes_zero": bool(lo > 0 or hi < 0),
            "queries_improved": int((d > 1e-12).sum()), "queries_regressed": int((d < -1e-12).sum()),
            "queries_unchanged": int((np.abs(d) <= 1e-12).sum()),
            "note": "paired percentile bootstrap, resampling unit = query"}
