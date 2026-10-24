"""Run the retrieval methods on one query group. Dense methods all go through MORPHE's real `RetrievalEngine`
(same cached embeddings, same preprocessing, same score semantics = cosine similarity); only the routing differs.

Timing protocol: query embeddings are produced once, one query at a time (batch 1, after a warm-up), and timed separately.
Every (method, query) retrieval is warmed up once and then repeated `repetitions` times; the per-query value is the median.
End-to-end latency = embedding + retrieval (BM25: tokenise + score + top-k).
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..evaluation.metrics import mean, percentile
from ..retrieval.controller import RetrievalEngine
from ..versioning.state import ArchitectureState
from .bm25 import BM25Index
from .corpus import DocMap

DEPTH = 100  # ranked-list depth returned to the evaluator (needed for Recall@100)
FALLBACK_K = 10  # routing/fallback decisions are made at the 10-result level, independent of the evaluation depth


@dataclass
class MethodRun:
    name: str
    results: dict[str, dict[str, float]]
    latency: dict[str, dict[str, float]]  # qid -> per-query timing + routing facts
    config: dict[str, Any] = field(default_factory=dict)


def embed_queries_timed(embedder, queries: dict[str, str]) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Batch-1 query embedding with a warm-up; returns vectors and per-query embedding latency (ms)."""
    inner = getattr(embedder, "inner", embedder)  # bypass the text cache so the timing is real
    inner.encode(["warm-up query for the embedding model"])
    vecs, times = {}, {}
    for qid, text in queries.items():
        t0 = time.perf_counter()
        v = inner.encode([text])[0]
        times[qid] = (time.perf_counter() - t0) * 1000
        vecs[qid] = v
    return vecs, times


def sandbox_engine(ctx, state: ArchitectureState, vectors: dict[str, np.ndarray]) -> RetrievalEngine:
    """Isolated in-memory copy of an architecture, queried by the production engine (see evaluation/evaluator.py)."""
    class _V:  # build_sandbox only needs `.vectors`
        pass
    v = _V()
    v.vectors = vectors  # type: ignore[attr-defined]
    return ctx.evaluator.build_sandbox(state, v)  # type: ignore[arg-type]


def run_dense(name: str, engine: RetrievalEngine, mode: str, qvecs: dict[str, np.ndarray], embed_ms: dict[str, float],
              doc_map: DocMap, repetitions: int = 3, depth: int = DEPTH) -> MethodRun:
    results: dict[str, dict[str, float]] = {}
    lat: dict[str, dict[str, float]] = {}
    for qid, qv in qvecs.items():
        engine.retrieve(qv, depth, mode, fallback_k=FALLBACK_K)  # warm-up
        totals, stages, out = [], {"seed": [], "expand": [], "search": [], "fallback": []}, None
        for _ in range(max(1, repetitions)):
            t0 = time.perf_counter()
            out = engine.retrieve(qv, depth, mode, fallback_k=FALLBACK_K)
            totals.append((time.perf_counter() - t0) * 1000)
            for k in stages:
                stages[k].append(out.timings.get(k, 0.0))
        assert out is not None
        results[qid] = {doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in out.results}
        r = statistics.median(totals)
        lat[qid] = {"retrieval_ms": r, "embed_ms": embed_ms[qid], "e2e_ms": r + embed_ms[qid],
                    **{f"{k}_ms": statistics.median(v) for k, v in stages.items()},
                    "eligible_candidates": float(out.guided_candidate_count), "candidates_incl_fallback": float(out.candidate_count),
                    "fallback": 1.0 if out.fallback_used else 0.0, "results_returned": float(len(out.results)),
                    "candidate_modules": float(len(out.candidates))}
    return MethodRun(name, results, lat, {"mode": mode, "depth": depth, "fallback_k": FALLBACK_K, "repetitions": repetitions})


def run_bm25(index: BM25Index, queries: dict[str, str], repetitions: int = 3, depth: int = DEPTH) -> MethodRun:
    first, times = index.search(queries, depth)  # also a warm-up pass
    best = dict(times)
    for _ in range(max(1, repetitions) - 1):
        _, t2 = index.search(queries, depth)
        best = {q: min(best[q], t2[q]) for q in best}  # min of repeats ~ steady state for a CPU-bound deterministic call
    lat = {q: {"retrieval_ms": t, "embed_ms": 0.0, "e2e_ms": t, "eligible_candidates": float(len(index.doc_ids)),
               "candidates_incl_fallback": float(len(index.doc_ids)), "fallback": 0.0, "results_returned": float(len(first[q]))}
           for q, t in best.items()}
    return MethodRun("bm25", first, lat, {"k1": index.k1, "b": index.b, "tokenizer": "lowercase \\w+, no stemming/stop-words",
                                          "depth": depth, "latency_statistic": "min of repeats"})


def summarize_latency(run: MethodRun, corpus_size: int) -> dict[str, Any]:
    L = list(run.latency.values())
    col = lambda k: [x[k] for x in L if k in x]  # noqa: E731
    out: dict[str, Any] = {"queries": len(L), "total_results": int(sum(col("results_returned")))}
    for key, label in (("retrieval_ms", "retrieval"), ("e2e_ms", "end_to_end"), ("embed_ms", "query_embedding")):
        v = col(key)
        out[label] = {"p50_ms": percentile(v, 50), "p95_ms": percentile(v, 95), "mean_ms": mean(v)}
    for k in ("seed", "expand", "search", "fallback"):
        if col(f"{k}_ms"):
            out[f"stage_{k}"] = {"mean_ms": mean(col(f"{k}_ms")), "p95_ms": percentile(col(f"{k}_ms"), 95)}
    elig = col("eligible_candidates")
    out["mean_eligible_candidates"] = mean(elig)
    out["candidate_pool_reduction_vs_corpus"] = 1.0 - mean(elig) / corpus_size if elig else None
    out["mean_candidates_incl_fallback"] = mean(col("candidates_incl_fallback"))
    out["fallback_rate"] = mean(col("fallback"))
    if run.name.endswith("graph"):
        fb = [x for x in L if x["fallback"]]
        out["fallback_ms_mean_when_used"] = mean([x["fallback_ms"] for x in fb]) if fb else None
    return out
