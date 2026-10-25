"""Side-by-side comparison of the four retrieval strategies on ONE query against the live architecture (demo + inspection).

Runs the real `RetrievalEngine` once per strategy (global dense, fixed graph, progressive expansion, soft routing) on the same query
vector. Nothing is tracked, so comparing never feeds the adaptation windows. When the query is one of the labeled evaluation queries the
response also reports quality metrics and routing diagnostics computed from those labels; they are clearly marked as evaluation-label
diagnostics (labels are never used by retrieval itself).
"""
from __future__ import annotations

import statistics
import time
from typing import Any

from ..common import NotFoundError, ValidationFailure
from .metrics import average_precision_at_k, ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank

STRATEGIES = ("global", "graph", "progressive", "soft")
LABELS = {"global": "Global dense", "graph": "Fixed graph (hard filter)", "progressive": "Progressive expansion", "soft": "Soft graph routing"}


def _searched_modules(outcome) -> list[str] | None:
    """Modules whose records were visible to the search; None when the whole corpus was searched."""
    if outcome.mode in ("global", "soft"):
        return None
    mods = [c.module_id for c in outcome.candidates]
    return None if outcome.fallback_used else mods


def compare_strategies(ctx, query: str | None, query_id: str | None, top_k: int = 5, modes: list[str] | None = None,
                       repetitions: int = 3) -> dict[str, Any]:
    modes = list(modes or STRATEGIES)
    bad = [m for m in modes if m not in STRATEGIES]
    if bad:
        raise ValidationFailure(f"unknown strategies {bad}; choose from {list(STRATEGIES)}")
    labeled = None
    if query_id:
        found = [q for q in ctx.datasets.current().queries if q.query_id == query_id]
        if not found:
            raise NotFoundError(f"evaluation query {query_id} not found", code="query_not_found")
        labeled = found[0]
        query = labeled.query
    query = (query or "").strip()
    if not query:
        raise ValidationFailure("provide a query or an evaluation query_id", code="empty_query")
    if len(query) > ctx.settings.max_query_chars:
        raise ValidationFailure(f"query is longer than {ctx.settings.max_query_chars} characters")
    if not 1 <= top_k <= ctx.settings.top_k_max:
        raise ValidationFailure(f"top_k must be between 1 and {ctx.settings.top_k_max}")

    te = time.perf_counter()
    qvec = ctx.embedder.encode([query])[0]
    embed_ms = (time.perf_counter() - te) * 1000
    graph = ctx.graph.graph
    name = lambda m: graph.nodes[m].get("name", m) if m in graph else m  # noqa: E731
    engine = ctx.retrieval.engine
    total = sum(int(d.get("active_record_count", 0)) for _, d in graph.nodes(data=True))

    rel_mem: dict[str, int] = {}
    rel_info: dict[str, dict[str, Any]] = {}
    if labeled:
        with ctx.coordinator.read():
            present = ctx.qdrant.get_memories(labeled.relevant_memory_ids)
        for mid in labeled.relevant_memory_ids:
            if mid in present:
                rel_mem[mid] = max(1, int(labeled.grades.get(mid, 1)))
                p = present[mid][0]
                rel_info[mid] = {"memory_id": mid, "module_id": p.get("module_id"), "module_name": name(p.get("module_id", "")),
                                 "text_preview": (p.get("text") or "")[:160]}

    out: dict[str, Any] = {}
    for mode in modes:
        with ctx.coordinator.read():
            engine.retrieve(qvec, top_k, mode, fallback_k=top_k, explain=True)  # warm-up
            times, o = [], None
            for _ in range(max(1, repetitions)):
                t0 = time.perf_counter()
                o = engine.retrieve(qvec, top_k, mode, fallback_k=top_k, explain=True)
                times.append((time.perf_counter() - t0) * 1000)
        assert o is not None
        ranked = [h.memory_id for h in o.results]
        searched = _searched_modules(o)
        entry: dict[str, Any] = {
            "strategy": mode, "label": LABELS[mode], "latency_ms": round(statistics.median(times), 3),
            "embedding_ms": round(embed_ms, 3), "timings_ms": {k: round(v, 3) for k, v in o.timings.items()},
            "candidate_count": o.candidate_count, "eligible_candidates": o.guided_candidate_count,
            "candidate_pool_reduction": None if not total else round(1 - o.guided_candidate_count / total, 4),
            "fallback_used": o.fallback_used, "fallback_reason": o.fallback_reason,
            "seed_modules": [{"module_id": m, "name": name(m), "score": round(s, 4)} for m, s in o.seeds],
            "searched_modules": [{"module_id": c.module_id, "name": name(c.module_id), "origin": c.origin, "stage": c.stage} for c in o.candidates]
                                if mode in ("graph", "progressive") else [],
            "stages": o.stages, "strategy_details": o.extra,
            "results": [{"rank": i + 1, "memory_id": h.memory_id, "score": round(h.score, 4), "module_id": h.module_id,
                         "module_name": name(h.module_id), "source_name": h.payload.get("source_name"),
                         "text": h.payload.get("text", ""), "relevant": (h.memory_id in rel_mem) if labeled else None,
                         "dense_score": round(h.payload["dense_score"], 4) if "dense_score" in h.payload else None} for i, h in enumerate(o.results)],
            "result_modules": sorted({h.module_id for h in o.results})}
        if labeled and rel_mem:
            k = top_k
            entry["metrics"] = {"recall_at_k": recall_at_k(rel_mem, ranked, k), "mrr": reciprocal_rank(rel_mem, ranked, k),
                                "ndcg_at_10": ndcg_at_k(rel_mem, ranked, 10), "precision_at_k": precision_at_k(rel_mem, ranked, k),
                                "map_at_10": average_precision_at_k(rel_mem, ranked, 10), "k": k}
            entry["relevant_retrieved"] = [m for m in rel_mem if m in ranked]
            entry["relevant_missed"] = [m for m in rel_mem if m not in ranked]
            if searched is not None:  # modules actually searched: a relevant memory outside them was excluded by routing
                s = set(searched)
                entry["relevant_excluded_by_routing"] = [rel_info[m] for m in rel_mem if rel_info[m]["module_id"] not in s]
            else:
                entry["relevant_excluded_by_routing"] = []
        out[mode] = entry
    result: dict[str, Any] = {
        "query": query, "query_id": query_id, "top_k": top_k, "labeled": bool(labeled and rel_mem),
        "architecture_version": ctx.versions.active_id(), "active_records": total, "strategies": out,
        "relevant_memories": list(rel_info.values()),
        "notes": ["Every strategy ran the real retrieval engine on the same query vector against the live architecture; nothing was tracked.",
                  "Latency is the median of repeated runs after a warm-up, query embedding excluded (embedding_ms is shared by all strategies).",
                  "Relevance flags, metrics and 'excluded by routing' come from the labeled evaluation set and are diagnostics, not retrieval inputs."],
    }
    if labeled and not rel_mem:
        result["notes"].append("None of this query's labeled memories is active, so no quality metrics are shown.")
    return result
