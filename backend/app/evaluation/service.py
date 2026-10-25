"""Evaluation use-cases exposed by the API: live runs, historical-version runs, mode and version comparisons."""
from __future__ import annotations

from typing import Any

from ..common import NotFoundError, ValidationFailure
from ..graph.persistence import ArchitecturePersistence
from ..storage.mongo_store import MongoStore, clean
from ..versioning.compare import diff_states, summarize_diff
from ..versioning.rollback import ArchitectureLifecycle
from ..versioning.snapshots import VersionStore
from .evaluator import Evaluator
from .version_comparator import AcceptancePolicy

MODES = ("graph", "global", "progressive", "soft")


class EvaluationService:
    def __init__(self, evaluator: Evaluator, versions: VersionStore, lifecycle: ArchitectureLifecycle,
                 persistence: ArchitecturePersistence, policy: AcceptancePolicy, mongo: MongoStore):
        self.evaluator, self.versions, self.lifecycle = evaluator, versions, lifecycle
        self.persistence, self.policy, self.mongo = persistence, policy, mongo

    def _splits(self, splits: list[str] | None) -> tuple[str, ...] | None:
        if splits:
            bad = [s for s in splits if s not in ("dev", "heldout")]
            if bad:
                raise ValidationFailure("splits must be 'dev' and/or 'heldout'")
        return tuple(splits) if splits else None

    def run(self, mode: str = "graph", version_id: str | None = None, splits: list[str] | None = None) -> dict[str, Any]:
        if mode not in MODES:
            raise ValidationFailure(f"mode must be one of {MODES}")
        ctx = self.evaluator.build_context(self._splits(splits))
        active = self.versions.active_id()
        if version_id and version_id != active:
            self.versions.manifest(version_id)
            state, rec = self.lifecycle.reconcile_state(self.versions.snapshot_state(version_id))
            rep = self.evaluator.evaluate_state(state, ctx, f"version:{version_id}", mode)
            rep["reconciliation"] = rec
        else:
            rep = self.evaluator.evaluate_live(ctx, "live", mode)
        return self.evaluator.persist(rep, architecture_version=version_id or active, splits=list(splits or []))

    def list_runs(self, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        items, total = self.mongo.list_events("evaluation_runs", "evaluated_at", limit, offset)
        for it in items:
            it.pop("per_query", None)
        return items, total

    def get_run(self, run_id: str) -> dict[str, Any]:
        doc = self.mongo.get_event("evaluation_runs", "run_id", run_id)
        if not doc:
            raise NotFoundError(f"evaluation run {run_id} not found", code="run_not_found")
        return doc

    def compare_modes(self, splits: list[str] | None = None) -> dict[str, Any]:
        """Global vector search vs fixed (genesis) graph vs adaptive (current) graph on one dataset and memory set."""
        ctx = self.evaluator.build_context(self._splits(splits))
        live = self.persistence.export_live_state()
        genesis_id = self.versions.genesis_id()
        fixed_state, _ = (self.lifecycle.reconcile_state(self.versions.snapshot_state(genesis_id), live)
                          if genesis_id else (live, {}))
        reports = {
            "global": self.evaluator.evaluate_state(live, ctx, "global", "global"),
            "fixed_graph": self.evaluator.evaluate_state(fixed_state, ctx, f"fixed:{genesis_id}", "graph"),
            "adaptive_graph": self.evaluator.evaluate_state(live, ctx, f"adaptive:{self.versions.active_id()}", "graph"),
        }
        for k, r in reports.items():
            self.evaluator.persist(r, role=f"compare_modes:{k}")
        return {"modes": {k: {"metrics": r["metrics"], "by_split": r["by_split"], "valid": r["valid"],
                              "label": r["label"], "run_id": r["run_id"]} for k, r in reports.items()},
                "fixed_graph_version": genesis_id, "adaptive_graph_version": self.versions.active_id(),
                "conditions": reports["adaptive_graph"]["conditions"],
                "note": "Same dataset, labels, top-k and memories; only the retrieval path / architecture differs."}

    def compare_versions(self, a: str, b: str, evaluate: bool = True) -> dict[str, Any]:
        ma, mb = self.versions.manifest(a), self.versions.manifest(b)
        live = self.persistence.export_live_state()
        sa, _ = self.lifecycle.reconcile_state(self.versions.snapshot_state(a), live)
        sb, _ = self.lifecycle.reconcile_state(self.versions.snapshot_state(b), live)
        d = diff_states(sa, sb)
        out: dict[str, Any] = {"from_version": a, "to_version": b, "diff": d, "summary": summarize_diff(d),
                               "stored_evaluation": {"from": ma.get("evaluation"), "to": mb.get("evaluation")},
                               "note": "Both versions are aligned to the currently active memories so they are "
                                       "compared on the same data."}
        if evaluate:
            ctx = self.evaluator.build_context()
            ra = self.evaluator.evaluate_state(sa, ctx, f"version:{a}")
            rb = self.evaluator.evaluate_state(sb, ctx, f"version:{b}")
            out["evaluation"] = {"from": ra["metrics"], "to": rb["metrics"], "conditions": rb["conditions"],
                                 "decision": self.policy.decide(ra, rb)}
        return out
