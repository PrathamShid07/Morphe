"""Retrieval engine (side-effect free; used live and inside evaluation sandboxes) and the live query service."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import networkx as nx
import numpy as np

from ..common import ValidationFailure, get_logger, iso, log_event, new_id, sha256_hex
from ..coordinator import MutationCoordinator
from ..ingestion.embedder import Embedder
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import Hit, QdrantStore
from .graph_selector import Candidate, expand_candidates
from .ranker import rank_hits, weak_reason

log = get_logger("retrieval")


@dataclass
class RetrievalOutcome:
    mode: str
    results: list[Hit]
    seeds: list[tuple[str, float]] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    candidate_count: int = 0  # active records in the searched modules (+ whole corpus if fallback ran)
    guided_candidate_count: int = 0
    fallback_used: bool = False
    fallback_reason: str | None = None
    guided_results: list[Hit] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # ms per stage: seed, expand, search, rank, fallback


def _graph_total(g) -> int:
    return sum(int(d.get("active_record_count", 0)) for _, d in g.nodes(data=True))


class RetrievalEngine:
    """Seeds -> graph expansion -> module-filtered vector search -> ranking -> global fallback.

    `candidate_count` always means: the number of active memory records inside the modules that were searched
    (when the global fallback also ran, the whole active corpus is added). It is the size of the search space,
    not the number of hits returned.
    """

    def __init__(self, settings, store: QdrantStore, graph_provider: Callable[[], nx.MultiGraph]):
        self.s = settings
        self.store = store
        self.graph_provider = graph_provider

    def retrieve(self, qvec: np.ndarray, top_k: int, mode: str = "graph", *, seed_count: int | None = None,
                 max_candidates: int | None = None, fallback: bool | None = None,
                 fallback_k: int | None = None) -> RetrievalOutcome:
        """`top_k` is the ranked-list depth returned; `fallback_k` is the depth the "too few results" fallback test uses
        (defaults to top_k), so routing decisions do not change when a caller merely asks for a deeper list."""
        fallback = self.s.fallback_enabled if fallback is None else fallback
        pc = time.perf_counter
        if mode == "global":
            t = pc()
            raw = self.store.search_memories(qvec, None, top_k)
            t_search = pc()
            hits = rank_hits(raw, top_k)
            t_rank = pc()
            g0 = self.graph_provider()
            # active records = sum of module counts (kept equal to the stores by the integrity check); no extra store scan
            total = _graph_total(g0) if g0.number_of_nodes() else self.store.count_memories(None)
            return RetrievalOutcome("global", hits, candidate_count=total, guided_candidate_count=total,
                                    timings={"search": (t_search - t) * 1000, "rank": (t_rank - t_search) * 1000})

        g = self.graph_provider()
        t0 = pc()
        seed_hits = self.store.search_modules(qvec, seed_count or self.s.seed_module_count)
        seeds = [(h.module_id, h.score) for h in seed_hits]
        t1 = pc()
        candidates = expand_candidates(seeds, g, max_candidates or self.s.max_candidate_modules)
        mids = [c.module_id for c in candidates]
        guided_space = (sum(int(g.nodes[m].get("active_record_count", 0)) for m in mids)
                        if all(m in g for m in mids) else self.store.count_memories(mids))
        t2 = pc()
        raw = self.store.search_memories(qvec, mids, max(top_k, self.s.per_module_candidates))
        t3 = pc()
        guided = rank_hits(raw, top_k)
        t3b = pc()
        out = RetrievalOutcome("graph", guided, seeds, candidates, guided_space, guided_space, False, None, guided,
                               {"seed": (t1 - t0) * 1000, "expand": (t2 - t1) * 1000, "search": (t3 - t2) * 1000,
                                "rank": (t3b - t3) * 1000, "fallback": 0.0})
        if fallback:
            total = _graph_total(g)
            reason = weak_reason(guided, fallback_k or top_k, total, self.s.fallback_min_score)
            if reason and guided_space < total:  # nothing to fall back to if the guided space was the whole corpus
                t4 = pc()
                out.results = rank_hits(self.store.search_memories(qvec, None, top_k), top_k)
                out.fallback_used, out.fallback_reason = True, reason
                out.candidate_count = guided_space + total
                out.timings["fallback"] = (pc() - t4) * 1000
        return out


class RetrievalService:
    """Live queries: embedding, shared read lock, tracking and (optionally) triggering adaptation checks."""

    def __init__(self, settings, embedder: Embedder, engine: RetrievalEngine, coordinator: MutationCoordinator,
                 mongo: MongoStore, tracker, version_provider: Callable[[], str | None],
                 on_query_completed: Callable[[dict[str, Any]], None] | None = None):
        self.s = settings
        self.embedder = embedder
        self.engine = engine
        self.coordinator = coordinator
        self.mongo = mongo
        self.tracker = tracker
        self.version_provider = version_provider
        self.on_query_completed = on_query_completed or (lambda event: None)

    def query(self, text: str, top_k: int | None = None, debug_trace: bool = False, mode: str = "graph",
              track: bool = True) -> dict[str, Any]:
        t0 = time.perf_counter()
        text = (text or "").strip()
        if not text:
            raise ValidationFailure("query must not be empty", code="empty_query")
        if len(text) > self.s.max_query_chars:
            raise ValidationFailure(f"query is longer than {self.s.max_query_chars} characters")
        top_k = self.s.top_k_default if top_k is None else top_k
        if not 1 <= top_k <= self.s.top_k_max:
            raise ValidationFailure(f"top_k must be between 1 and {self.s.top_k_max}")
        if mode not in ("graph", "global"):
            raise ValidationFailure("mode must be 'graph' or 'global'")
        qvec = self.embedder.encode([text])[0]
        with self.coordinator.read():
            outcome = self.engine.retrieve(qvec, top_k, mode)
        latency_ms = (time.perf_counter() - t0) * 1000
        version = self.version_provider()
        query_id = new_id("qry")

        metas = self.mongo.get_memory_metadata_many(h.memory_id for h in outcome.results)
        results = [{"memory_id": h.memory_id, "text": h.payload.get("text", ""), "score": round(h.score, 4),
                    "module_id": h.module_id, "module_name": self._module_name(h.module_id),
                    "source_name": h.payload.get("source_name"), "source_id": h.payload.get("source_id"),
                    "section": h.payload.get("section"),
                    "access_count": (metas.get(h.memory_id) or {}).get("access_count", 0)} for h in outcome.results]
        modules_accessed = sorted({h.module_id for h in outcome.results})
        success = bool(outcome.results) and outcome.results[0].score >= self.s.fallback_min_score
        event = {"query_id": query_id, "timestamp": iso(), "query_hash": sha256_hex(text)[:16],
                 "query_preview": text[:120], "mode": outcome.mode, "top_k": top_k,
                 "selected_modules": [m for m, _ in outcome.seeds],
                 "candidate_modules": [c.module_id for c in outcome.candidates],
                 "result_ids": [h.memory_id for h in outcome.results],
                 "result_module_ids": [h.module_id for h in outcome.results], "modules_accessed": modules_accessed,
                 "candidate_count": outcome.candidate_count, "latency_ms": round(latency_ms, 3),
                 "fallback_used": outcome.fallback_used, "architecture_version": version, "success": success,
                 "top_score": round(outcome.results[0].score, 4) if outcome.results else None, "tracked": track}
        if track:
            self.tracker.record(event)
        response: dict[str, Any] = {"query_id": query_id, "architecture_version": version, "top_k": top_k,
                                    "results": results, "latency_ms": round(latency_ms, 2), "mode": outcome.mode}
        if debug_trace and self.s.debug_trace_enabled:
            response["trace"] = self._trace(outcome, latency_ms, version)
        log_event(log, "query_completed", query_id=query_id, results=len(results), latency_ms=round(latency_ms, 2),
                  candidate_count=outcome.candidate_count, fallback=outcome.fallback_used)
        if track:
            self.on_query_completed(event)
        return response

    def _module_name(self, module_id: str) -> str | None:
        g = self.engine.graph_provider()
        return g.nodes[module_id].get("name") if module_id in g else None

    def _trace(self, o: RetrievalOutcome, latency_ms: float, version: str | None) -> dict[str, Any]:
        g = self.engine.graph_provider()
        name = lambda m: g.nodes[m].get("name", m) if m in g else m  # noqa: E731
        return {
            "mode": o.mode,
            "seed_modules": [{"module_id": m, "name": name(m), "score": round(s, 4)} for m, s in o.seeds],
            "candidate_modules": [{"module_id": c.module_id, "name": name(c.module_id), "origin": c.origin,
                                   "via": c.via, "via_name": name(c.via) if c.via else None,
                                   "record_count": g.nodes[c.module_id].get("active_record_count", 0)
                                   if c.module_id in g else 0} for c in o.candidates],
            "expanded_modules": [c.module_id for c in o.candidates if c.origin != "seed"],
            "candidate_count": o.candidate_count, "guided_candidate_count": o.guided_candidate_count,
            "candidate_count_definition": "active records inside the searched modules (plus the whole corpus when "
                                          "global fallback also ran)",
            "fallback_used": o.fallback_used, "fallback_reason": o.fallback_reason,
            "guided_result_ids": [h.memory_id for h in o.guided_results],
            "latency_ms": round(latency_ms, 2), "architecture_version": version,
        }
