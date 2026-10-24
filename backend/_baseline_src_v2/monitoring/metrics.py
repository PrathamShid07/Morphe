"""Dashboard metrics. Anything that cannot be measured is reported as null with a reason — never as a fake zero."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil

from ..common import iso, utcnow
from ..evaluation.metrics import mean, percentile
from ..graph.manager import GraphManager
from ..memory.budget_tracker import BudgetTracker
from ..storage.mongo_store import MongoStore, clean
from ..storage.qdrant_store import QdrantStore

WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
MIN_LATENCY_SAMPLES = 5


class MetricsService:
    def __init__(self, settings, mongo: MongoStore, qdrant: QdrantStore, graph: GraphManager, budget: BudgetTracker,
                 version_provider):
        self.s = settings
        self.mongo, self.qdrant, self.graph, self.budget = mongo, qdrant, graph, budget
        self.version_provider = version_provider
        self._last_sample = 0.0
        self._proc = psutil.Process(os.getpid())

    # ---------------------------------------------------------------- summary
    def rss_mb(self) -> float:
        return round(self._proc.memory_info().rss / 1_048_576, 1)

    def storage_mb(self) -> float | None:
        b = self.qdrant.storage_size_bytes()
        return None if b is None else round(b / 1_048_576, 2)

    def query_stats(self, limit: int = 500, version: str | None = None) -> dict[str, Any]:
        q: dict[str, Any] = {"tracked": True}
        if version:
            q["architecture_version"] = version
        docs = list(self.mongo.col("query_events").find(q, {"latency_ms": 1, "candidate_count": 1, "fallback_used": 1,
                                                            "timestamp": 1}).sort("timestamp", -1).limit(limit))
        n = len(docs)
        enough = n >= MIN_LATENCY_SAMPLES
        lat = [d["latency_ms"] for d in docs]
        return {"samples": n, "enough_data": enough,
                "p50_latency_ms": percentile(lat, 50) if enough else None,
                "p95_latency_ms": percentile(lat, 95) if enough else None,
                "mean_latency_ms": mean(lat) if enough else None,
                "mean_candidate_count": mean([d["candidate_count"] for d in docs]) if n else None,
                "fallback_rate": mean([1.0 if d["fallback_used"] else 0.0 for d in docs]) if n else None,
                "reason": None if enough else f"Not enough data yet ({n} of {MIN_LATENCY_SAMPLES} queries)"}

    def latest_quality(self) -> dict[str, Any] | None:
        doc = self.mongo.col("evaluation_runs").find_one(
            {"path": "live", "valid": True}, sort=[("evaluated_at", -1)])
        if not doc:
            return None
        return {"run_id": doc["run_id"], "evaluated_at": doc["evaluated_at"], "metrics": doc["metrics"],
                "sample_size": doc["metrics"]["sample_size"], "mode": doc["mode"],
                "architecture_version": doc.get("architecture_version")}

    def summary(self) -> dict[str, Any]:
        usage = self.budget.usage()
        nodes, edges = self.graph.counts()
        g = self.graph.graph
        content = sum(1 for _, d in g.nodes(data=True) if d.get("active_record_count", 0) > 0)
        qs = self.query_stats()
        quality = self.latest_quality()
        return {
            "active_memory_count": usage["active_memories"],
            "source_count": self.mongo.col("sources").count_documents({"status": "active"}),
            "module_count": content, "node_count": nodes, "edge_count": edges,
            "active_tokens": usage["active_tokens"], "budget_tokens": usage["budget_tokens"],
            "usage_percent": usage["usage_percent"], "token_budget_note": "active-text tokens, not RAM or disk",
            "p50_latency_ms": qs["p50_latency_ms"], "p95_latency_ms": qs["p95_latency_ms"],
            "latency_samples": qs["samples"], "latency_note": qs["reason"],
            "average_candidate_count": qs["mean_candidate_count"], "fallback_rate": qs["fallback_rate"],
            "process_rss_mb": self.rss_mb(), "qdrant_storage_mb": self.storage_mb(),
            "retrieval_quality": quality,
            "retrieval_quality_note": None if quality else "Not enough data yet — run an evaluation on the labeled dataset",
            "architecture_version": self.version_provider(),
            "thresholds": {"compression_start_pct": self.s.compression_start_pct,
                           "emergency_cleanup_pct": self.s.emergency_cleanup_pct},
        }

    # ---------------------------------------------------------------- history
    def sample(self, force: bool = False) -> dict[str, Any] | None:
        now = time.monotonic()
        if not force and now - self._last_sample < 3.0:
            return None
        self._last_sample = now
        usage = self.budget.usage()
        nodes, edges = self.graph.counts()
        doc = {"timestamp": iso(), "active_tokens": usage["active_tokens"], "budget_tokens": usage["budget_tokens"],
               "usage_percent": usage["usage_percent"], "record_count": usage["active_memories"],
               "module_count": nodes, "edge_count": edges, "process_rss_mb": self.rss_mb(),
               "qdrant_storage_mb": self.storage_mb(), "architecture_version": self.version_provider()}
        self.mongo.col("system_metrics").insert_one(dict(doc))
        return doc

    def history(self, window: str = "1h") -> dict[str, Any]:
        delta = WINDOWS.get(window, WINDOWS["1h"])
        cutoff = (utcnow() - delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        sys_pts = [clean(d) for d in self.mongo.col("system_metrics").find({"timestamp": {"$gte": cutoff}}).sort("timestamp", 1)]
        events = list(self.mongo.col("query_events").find(
            {"tracked": True, "timestamp": {"$gte": cutoff}},
            {"timestamp": 1, "latency_ms": 1, "candidate_count": 1, "fallback_used": 1, "architecture_version": 1}).sort("timestamp", 1))
        bucket_len = {"1h": 5, "24h": 13, "7d": 16}[window if window in WINDOWS else "1h"]  # ISO prefix: minute / hour / day
        buckets: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            buckets.setdefault(e["timestamp"][:bucket_len], []).append(e)
        qpts = []
        for k, es in sorted(buckets.items()):
            lat = [x["latency_ms"] for x in es]
            qpts.append({"timestamp": k, "queries": len(es), "p95_latency_ms": percentile(lat, 95) if len(lat) >= 2 else lat[0],
                         "mean_latency_ms": mean(lat), "mean_candidate_count": mean([x["candidate_count"] for x in es]),
                         "fallback_rate": mean([1.0 if x["fallback_used"] else 0.0 for x in es])})
        evals = [{"timestamp": d["evaluated_at"], "recall_at_5": d["metrics"]["recall_at_5"], "mrr": d["metrics"]["mrr"],
                  "p95_latency_ms": d["metrics"]["p95_latency_ms"], "sample_size": d["metrics"]["sample_size"],
                  "architecture_version": d.get("architecture_version"), "mode": d["mode"]}
                 for d in self.mongo.col("evaluation_runs").find({"path": "live", "valid": True,
                                                                  "evaluated_at": {"$gte": cutoff}}).sort("evaluated_at", 1)]
        return {"window": window, "points": sys_pts, "query_points": qpts, "evaluation_points": evals,
                "note": None if sys_pts or qpts else "Not enough data yet"}
