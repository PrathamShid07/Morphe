"""BEIR metric computation. Aggregates come from BEIR's own `EvaluateRetrieval`; per-query values (needed for paired
bootstrap and per-query inspection) come from pytrec_eval with the *same measures* and are cross-checked against BEIR.
Graded judgments are passed to nDCG unchanged (no binarisation)."""
from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np
import pytrec_eval
from beir.retrieval.evaluation import EvaluateRetrieval

from ..evaluation.metrics import paired_bootstrap  # noqa: F401  (re-exported; defined next to the other pure metrics)

K_VALUES = [1, 3, 5, 10, 100]
HEADLINE = "ndcg_at_10"
METRIC_KEYS = ["ndcg_at_10", "map_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10", "precision_at_10"]


def _drop_identical(results: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """BEIR's default (`ignore_identical_ids=True`): a document whose id equals the query id is not a valid answer."""
    return {q: {d: s for d, s in docs.items() if d != q} for q, docs in results.items()}


def check_results(qrels: dict[str, dict[str, int]], results: dict[str, dict[str, float]]) -> None:
    missing = [q for q in qrels if q not in results]
    if missing:  # pytrec_eval silently ignores queries a system did not answer, which would inflate every metric
        raise ValueError(f"{len(missing)} judged queries have no result entry (e.g. {missing[:3]})")
    bad = [q for q, d in results.items() if any(not math.isfinite(s) for s in d.values())]
    if bad:
        raise ValueError(f"non-finite scores for queries {bad[:3]}")


def evaluate_beir(qrels: dict[str, dict[str, int]], results: dict[str, dict[str, float]],
                  k_values: list[int] = K_VALUES) -> dict[str, Any]:
    """Aggregate nDCG/MAP/Recall/Precision via BEIR plus MRR via BEIR's custom metric."""
    check_results(qrels, results)
    res = copy.deepcopy(results)  # EvaluateRetrieval.evaluate mutates its input when dropping identical ids
    ndcg, _map, recall, precision = EvaluateRetrieval.evaluate(qrels, res, k_values)
    mrr = EvaluateRetrieval.evaluate_custom(qrels, _drop_identical(results), k_values, metric="mrr")
    out: dict[str, Any] = {
        "ndcg_at_10": ndcg["NDCG@10"], "map_at_10": _map["MAP@10"], "precision_at_10": precision["P@10"],
        "recall_at_5": recall["Recall@5"], "recall_at_10": recall["Recall@10"], "recall_at_100": recall["Recall@100"],
        "mrr_at_10": mrr["MRR@10"], "queries_evaluated": len(qrels),
        "raw": {**ndcg, **_map, **recall, **precision, **mrr},
    }
    return out


def per_query_metrics(qrels: dict[str, dict[str, int]], results: dict[str, dict[str, float]],
                      k_values: list[int] = K_VALUES) -> dict[str, dict[str, float]]:
    check_results(qrels, results)
    res = _drop_identical(results)
    s = lambda p: p + "." + ",".join(map(str, k_values))  # noqa: E731
    ev = pytrec_eval.RelevanceEvaluator(qrels, {s("map_cut"), s("ndcg_cut"), s("recall"), s("P")})
    scores = ev.evaluate(res)
    out: dict[str, dict[str, float]] = {}
    for qid in qrels:
        sc = scores.get(qid, {})
        ranked = sorted(res.get(qid, {}).items(), key=lambda kv: kv[1], reverse=True)[:10]
        rel = {d for d, g in qrels[qid].items() if g > 0}
        rr = next((1.0 / (i + 1) for i, (d, _) in enumerate(ranked) if d in rel), 0.0)
        out[qid] = {"ndcg_at_10": sc.get("ndcg_cut_10", 0.0), "map_at_10": sc.get("map_cut_10", 0.0),
                    "precision_at_10": sc.get("P_10", 0.0), "recall_at_5": sc.get("recall_5", 0.0),
                    "recall_at_10": sc.get("recall_10", 0.0), "recall_at_100": sc.get("recall_100", 0.0), "mrr_at_10": rr}
    return out


def mean_of(per_query: dict[str, dict[str, float]], key: str) -> float:
    return float(np.mean([v[key] for v in per_query.values()])) if per_query else float("nan")


def consistency_gap(agg: dict[str, Any], per_query: dict[str, dict[str, float]]) -> float:
    """Largest |BEIR aggregate − mean of per-query values| over the headline metrics (BEIR rounds to 5 decimals)."""
    return max(abs(agg[k] - mean_of(per_query, k)) for k in METRIC_KEYS)
