"""Live queries for one signed-in user, on the real retrieval engine, inside that user's authorization scope."""
from __future__ import annotations

import time
from typing import Any

from ..common import ValidationFailure, get_logger, iso, log_event, new_id
from ..memory.personal import PersonalMemoryService
from ..storage.mongo_store import MongoStore
from ..storage.qdrant_store import QdrantStore
from ..storage.user_plane import UserVectorStore
from .controller import MODES, RetrievalEngine
from .scoped import ScopedStore, build_scope

log = get_logger("retrieval.personal")
_TRACE_DROP = {"guided_result_ids", "architecture_version"}


class PersonalRetrievalService:
    def __init__(self, settings, embedder, shared: QdrantStore, personal_vectors: UserVectorStore, personal: PersonalMemoryService,
                 mongo: MongoStore, shared_graph_provider, coordinator):
        self.s, self.embedder, self.shared, self.vectors = settings, embedder, shared, personal_vectors
        self.personal, self.mongo, self.shared_graph, self.coordinator = personal, mongo, shared_graph_provider, coordinator

    def scope(self, owner: str):
        """Build the caller's scope (own modules + published shared modules). Always derived from the verified owner id."""
        with self.coordinator.read():  # a consistent view of the shared architecture while administrators may be changing it
            return build_scope(owner, self.personal.graph(owner), self.shared_graph(), self.mongo, self.personal.data.stats(owner)["memories"])

    def query(self, owner: str, text: str, top_k: int | None = None, debug_trace: bool = False, mode: str = "graph") -> dict[str, Any]:
        t0 = time.perf_counter()
        text = (text or "").strip()
        if not text:
            raise ValidationFailure("ask a question first", code="empty_query")
        if len(text) > self.s.max_query_chars:
            raise ValidationFailure(f"questions are limited to {self.s.max_query_chars} characters")
        top_k = self.s.top_k_default if top_k is None else top_k
        if not 1 <= top_k <= self.s.user_top_k_max:
            raise ValidationFailure(f"top_k must be between 1 and {self.s.user_top_k_max}", code="top_k_out_of_range")
        if mode not in MODES:
            raise ValidationFailure(f"mode must be one of {', '.join(MODES)}")
        te = time.perf_counter()
        qvec = self.embedder.encode([text])[0]
        embed_ms = (time.perf_counter() - te) * 1000
        with self.coordinator.read():
            scope = build_scope(owner, self.personal.graph(owner), self.shared_graph(), self.mongo, self.personal.data.stats(owner)["memories"])
            engine = RetrievalEngine(self.s, ScopedStore(self.shared, self.vectors, scope), lambda: scope.graph)
            outcome = engine.retrieve(qvec, top_k, mode, explain=debug_trace)
        latency_ms = (time.perf_counter() - t0) * 1000
        g = scope.graph
        name = lambda m: g.nodes[m].get("name", m) if m in g else m  # noqa: E731
        # defence in depth: whatever the engine returned, a point owned by somebody else (or not explicitly shared) never leaves this function
        allowed = [h for h in outcome.results
                   if h.payload.get("owner_user_id") == owner or (h.payload.get("owner_user_id") is None and h.payload.get("visibility") == "shared")]
        results = [{"memory_id": h.memory_id, "text": h.payload.get("text", ""), "score": round(h.score, 4), "module_id": h.module_id,
                    "module_name": name(h.module_id), "source_name": h.payload.get("source_name"), "section": h.payload.get("section"),
                    "scope": "personal" if h.payload.get("owner_user_id") == owner else "shared"} for h in allowed]
        query_id = new_id("qry")
        co = self.personal.record_coaccess(owner, [r["module_id"] for r in results if r["scope"] == "personal"])
        resp: dict[str, Any] = {"query_id": query_id, "architecture_version": None, "top_k": top_k, "results": results, "latency_ms": round(latency_ms, 2),
                                "mode": outcome.mode, "created_at": iso(),
                                "memory_scope": {"personal_records": scope.personal_records, "shared_records": scope.shared_records,
                                                 "empty": scope.personal_records + scope.shared_records == 0},
                                "fallback_used": outcome.fallback_used}
        if debug_trace and self.s.debug_trace_enabled:
            resp["trace"] = self._trace(outcome, latency_ms, embed_ms, g)
        log_event(log, "personal_query_completed", query_id=query_id, results=len(results), latency_ms=round(latency_ms, 2), coaccess=co)
        return resp

    @staticmethod
    def _trace(o, latency_ms: float, embed_ms: float, g) -> dict[str, Any]:
        name = lambda m: g.nodes[m].get("name", m) if m in g else m  # noqa: E731
        recs = lambda m: g.nodes[m].get("active_record_count", 0) if m in g else 0  # noqa: E731
        return {
            "mode": o.mode, "strategy": o.mode,
            "seed_modules": [{"module_id": m, "name": name(m), "score": round(s, 4)} for m, s in o.seeds],
            "candidate_modules": [{"module_id": c.module_id, "name": name(c.module_id), "origin": c.origin, "via": c.via,
                                   "via_name": name(c.via) if c.via else None, "record_count": recs(c.module_id)} for c in o.candidates],
            "expanded_modules": [c.module_id for c in o.candidates if c.origin != "seed"],
            "candidate_count": o.candidate_count, "guided_candidate_count": o.guided_candidate_count,
            "fallback_used": o.fallback_used, "fallback_reason": o.fallback_reason, "latency_ms": round(latency_ms, 2),
            "embedding_ms": round(embed_ms, 3), "timings_ms": {k: round(v, 3) for k, v in o.timings.items()},
            "searched_modules": [{"module_id": c.module_id, "name": name(c.module_id), "stage": c.stage, "origin": c.origin, "records": recs(c.module_id)}
                                 for c in o.candidates] if o.mode in ("graph", "progressive") else [],
            "result_modules": sorted({h.module_id for h in o.results}), "stages": o.stages,
            **{k: v for k, v in o.extra.items() if k in ("progressive", "soft")}}
