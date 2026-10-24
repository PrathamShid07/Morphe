"""Architecture evaluator.

A candidate (or historical) architecture is evaluated WITHOUT touching production state: an isolated in-memory
Qdrant store is populated from the candidate ArchitectureState (same memory vectors, candidate module assignments and
profile vectors) together with a NetworkX graph built from the candidate's edges. The *same* RetrievalEngine used for
live queries then answers the labeled queries, so seed selection, graph expansion, filtered search and fallback are
the real algorithms. Baseline and candidate are measured by the identical procedure on the identical dataset.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..common import get_logger, iso, log_event, new_id
from ..coordinator import MutationCoordinator
from ..graph.manager import graph_from_state
from ..ingestion.embedder import Embedder
from ..retrieval.controller import RetrievalEngine
from ..storage.qdrant_store import QdrantStore
from ..versioning.state import ArchitectureState, centroid
from .dataset import DatasetStore, EvalDataset, EvalQuery
from .metrics import mean, percentile, ranking_metrics, recall_at_k, reciprocal_rank

log = get_logger("evaluation")


@dataclass
class EvalContext:
    dataset: EvalDataset
    queries: list[EvalQuery]  # usable queries (labels resolved to active memories)
    relevant: dict[str, list[str]]
    vectors: dict[str, np.ndarray]
    query_vecs: dict[str, np.ndarray]
    skipped: list[dict[str, Any]] = field(default_factory=list)
    labels_missing: int = 0


class Evaluator:
    def __init__(self, settings, embedder: Embedder, live_store: QdrantStore, live_engine: RetrievalEngine,
                 datasets: DatasetStore, coordinator: MutationCoordinator, mongo):
        self.s = settings
        self.embedder = embedder
        self.live_store = live_store
        self.live_engine = live_engine
        self.datasets = datasets
        self.coordinator = coordinator
        self.mongo = mongo

    # --------------------------------------------------------------- context
    def build_context(self, splits: tuple[str, ...] | None = None) -> EvalContext:
        """Load the dataset, resolve labels against *active* memories, embed queries, snapshot memory vectors."""
        ds = self.datasets.current()
        with self.coordinator.read():  # consistent view of the vector store
            vectors = {mid: vec for mid, _, vec in self.live_store.scroll_memories(with_vectors=True)}
        usable: list[EvalQuery] = []
        relevant: dict[str, list[str]] = {}
        skipped: list[dict[str, Any]] = []
        missing = 0
        for q in ds.queries:
            if splits and q.split not in splits:
                continue
            live = [r for r in q.relevant_memory_ids if r in vectors]
            missing += len(q.relevant_memory_ids) - len(live)
            if not live:
                skipped.append({"query_id": q.query_id, "reason": "no labeled memory is active"})
                continue
            usable.append(q)
            relevant[q.query_id] = live
        qv = self.embedder.encode([q.query for q in usable]) if usable else np.zeros((0, self.embedder.dimension))
        return EvalContext(ds, usable, relevant, vectors, {q.query_id: qv[i] for i, q in enumerate(usable)},
                           skipped, missing)

    # --------------------------------------------------------------- sandbox
    def build_sandbox(self, state: ArchitectureState, ctx: EvalContext) -> RetrievalEngine:
        store = QdrantStore.in_memory(self.embedder.dimension)
        assign = state.module_of()
        missing = [m for m in assign if m not in ctx.vectors]
        if missing:
            raise ValueError(f"candidate architecture assigns {len(missing)} memories with no vector (e.g. {missing[:2]})")
        store.upsert_memories([(mid, ctx.vectors[mid], {"memory_id": mid, "module_id": mod, "status": "active"})
                               for mid, mod in assign.items()])
        profiles = []
        for m in state.content_modules():
            vec = state.modules[m].profile_vector
            if vec is None:
                vec = centroid([ctx.vectors[i] for i in state.assignments[m]])
            profiles.append((m, vec, {"display_name": state.modules[m].name}))
        store.upsert_module_profiles(profiles)
        graph = graph_from_state(state)
        return RetrievalEngine(self.s, store, lambda: graph)

    # -------------------------------------------------------------- evaluate
    def evaluate_state(self, state: ArchitectureState, ctx: EvalContext, label: str, mode: str = "graph"
                       ) -> dict[str, Any]:
        engine = self.build_sandbox(state, ctx)
        report = self._run(engine, ctx, mode, label)
        report["architecture_checksum"] = state.checksum()
        report["path"] = "sandbox"
        return report

    def evaluate_live(self, ctx: EvalContext, label: str = "live", mode: str = "graph") -> dict[str, Any]:
        report = self._run(self.live_engine, ctx, mode, label)
        report["path"] = "live"
        return report

    def evaluate_states(self, items: list[tuple[str, ArchitectureState]], ctx: EvalContext, mode: str = "graph"
                        ) -> dict[str, dict[str, Any]]:
        """Evaluate several architectures on the same queries. With `eval_interleaved` the engines are timed alternately
        (rotating order per query, one repetition at a time) so machine drift and ordering effects hit all of them equally
        — the only fair way to compare p95 latency of near-identical architectures."""
        if not self.s.eval_interleaved or len(items) == 1:
            return {label: self.evaluate_state(st, ctx, label, mode) for label, st in items}
        engines = {label: self.build_sandbox(st, ctx) for label, st in items}
        reports = self._run_multi(engines, ctx, mode)
        for label, st in items:
            reports[label]["architecture_checksum"] = st.checksum()
            reports[label]["path"] = "sandbox"
        return reports

    def _run(self, engine: RetrievalEngine, ctx: EvalContext, mode: str, label: str) -> dict[str, Any]:
        return self._run_multi({label: engine}, ctx, mode)[label]

    def _run_multi(self, engines: dict[str, RetrievalEngine], ctx: EvalContext, mode: str) -> dict[str, dict[str, Any]]:
        t_start = time.perf_counter()
        k = self.s.eval_top_k
        depth = max(k, self.s.eval_depth)  # ranked-list depth; legacy metrics are sliced to k, routing decisions use k
        reps = max(1, self.s.eval_repetitions)
        labels = list(engines)
        rows: dict[str, list[dict[str, Any]]] = {l: [] for l in labels}
        for qi, q in enumerate(ctx.queries):
            qv = ctx.query_vecs[q.query_id]
            rel = ctx.relevant[q.query_id]
            grades = {m: max(1, int(q.grades.get(m, 1))) for m in rel}  # graded labels are kept (default grade 1)
            r = qi % len(labels)
            order = labels[r:] + labels[:r]  # rotate the order each query
            for l in order:
                engines[l].retrieve(qv, depth, mode, fallback_k=k)  # warm-up (caches, allocator)
            times: dict[str, list[float]] = {l: [] for l in labels}
            outcome: dict[str, Any] = {}
            for _ in range(reps):
                for l in order:
                    t0 = time.perf_counter()
                    outcome[l] = engines[l].retrieve(qv, depth, mode, fallback_k=k)
                    times[l].append((time.perf_counter() - t0) * 1000)
            for l in labels:
                out = outcome[l]
                full = [h.memory_id for h in out.results]
                ids = full[:k]
                gids = [h.memory_id for h in out.guided_results][:k] if out.guided_results else ids
                rows[l].append({
                    "query_id": q.query_id, "split": q.split, "relevant": len(rel), **ranking_metrics(grades, full),
                    "recall": recall_at_k(rel, ids, k), "rr": reciprocal_rank(rel, ids, k),
                    "guided_recall": recall_at_k(rel, gids, k), "guided_rr": reciprocal_rank(rel, gids, k),
                    "latency_ms": float(np.median(times[l])), "candidate_count": out.candidate_count,
                    "fallback": out.fallback_used, "candidate_modules": [c.module_id for c in out.candidates],
                    "retrieved": ids})
        dur = round((time.perf_counter() - t_start) * 1000, 1)
        reports = {}
        for l in labels:
            reports[l] = self._aggregate(rows[l], ctx, mode, l)
            reports[l]["duration_ms"] = dur
            reports[l]["conditions"]["interleaved"] = len(labels) > 1
        return reports

    def _aggregate(self, rows: list[dict[str, Any]], ctx: EvalContext, mode: str, label: str) -> dict[str, Any]:
        def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
            return {"sample_size": len(rs),
                    "recall_at_5": mean([r["recall"] for r in rs]), "mrr": mean([r["rr"] for r in rs]),
                    "ndcg_at_10": mean([r["ndcg_at_10"] for r in rs]), "mrr_at_10": mean([r["mrr_at_10"] for r in rs]),
                    "map_at_10": mean([r["map_at_10"] for r in rs]), "precision_at_10": mean([r["precision_at_10"] for r in rs]),
                    "recall_at_10": mean([r["recall_at_10"] for r in rs]),
                    "guided_recall_at_5": mean([r["guided_recall"] for r in rs]),
                    "guided_mrr": mean([r["guided_rr"] for r in rs]),
                    "p50_latency_ms": percentile([r["latency_ms"] for r in rs], 50),
                    "p95_latency_ms": percentile([r["latency_ms"] for r in rs], 95),
                    "mean_candidate_count": mean([r["candidate_count"] for r in rs]),
                    "fallback_rate": mean([1.0 if r["fallback"] else 0.0 for r in rs])}
        metrics = agg(rows)
        by_split = {sp: agg([r for r in rows if r["split"] == sp]) for sp in sorted({r["split"] for r in rows})}
        valid = ctx.dataset.valid and len(rows) >= self.s.min_eval_queries
        reason = None
        if not ctx.dataset.valid:
            reason = "evaluation dataset is missing or invalid: " + "; ".join(ctx.dataset.issues[:3])
        elif len(rows) < self.s.min_eval_queries:
            reason = (f"only {len(rows)} labeled queries could be evaluated; at least {self.s.min_eval_queries} are "
                      "required for a trustworthy comparison")
        return {
            "run_id": new_id("eval"), "label": label, "mode": mode, "evaluated_at": iso(), "valid": valid,
            "invalid_reason": reason, "metrics": metrics, "by_split": by_split,
            "conditions": {"top_k": self.s.eval_top_k, "repetitions": self.s.eval_repetitions,
                           "latency_measures": "median of repeated retrievals per query, query embedding excluded",
                           "candidate_count": "active records in searched modules (+corpus when fallback ran)",
                           "dataset_hash": ctx.dataset.dataset_hash, "queries_total": len(ctx.dataset.queries),
                           "queries_evaluated": len(rows), "queries_skipped": len(ctx.skipped),
                           "labels_missing": ctx.labels_missing, "seed_modules": self.s.seed_module_count,
                           "max_candidate_modules": self.s.max_candidate_modules,
                           "fallback_enabled": self.s.fallback_enabled},
            "per_query": [{k: v for k, v in r.items() if k not in ("candidate_modules",)} for r in rows],
            "skipped": ctx.skipped[:50],
        }

    def persist(self, report: dict[str, Any], **extra: Any) -> dict[str, Any]:
        doc = {**report, **extra}
        self.mongo.col("evaluation_runs").insert_one(dict(doc))
        return doc
