"""Deterministic demo helpers: reset, seed corpus, controlled scenarios and query replay.

Everything goes through the same services/APIs a user would use; nothing is faked. Scenarios only *create the
conditions* (a redundant module, a mixed-topic module, a repeated co-access pattern, budget pressure) — the adaptation
and compression engines still decide and validate on their own.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .common import MorpheError, NotFoundError, ValidationFailure, get_logger, iso, log_event
from .container import AppContext
from .versioning.state import ArchitectureState

log = get_logger("demo")


class DemoService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.s = ctx.settings

    # ----------------------------------------------------------- manifest
    def manifest(self) -> dict[str, Any]:
        p = Path(self.s.samples_dir) / "manifest.json"
        if not p.is_file():
            raise NotFoundError(f"sample manifest not found at {p}", code="samples_missing")
        return json.loads(p.read_text(encoding="utf-8"))

    def scenarios(self) -> dict[str, Any]:
        man = self.manifest()
        loaded_sources = {d["source_name"] for d in self.ctx.mongo.col("sources").find({"status": "active"})}
        out = []
        for name, info in man["scenarios"].items():
            files = [it["source_name"] for it in man["sets"].get(info.get("set", name), [])]
            out.append({"name": name, "title": info["title"], "description": info["description"],
                        "expected": info.get("expected"), "loaded": bool(files) and all(f in loaded_sources for f in files)
                        if files else None})
        return {"scenarios": out, "removes": "Only MORPHE's own Qdrant collections and MongoDB database are touched on reset."}

    # -------------------------------------------------------------- reset
    def reset(self, reload_dataset: bool = True) -> dict[str, Any]:
        c = self.ctx
        with c.coordinator.mutate("reset", meta={"scope": "morphe collections only"}):
            c.qdrant.reset()
            c.mongo.reset()
            c.graph.replace_from_state(ArchitectureState())
            c.versions.create(ArchitectureState(), reason="initial baseline (empty)", operation_type="baseline")
        ds = c.datasets.load_fixture() if reload_dataset else None
        log_event(log, "demo_reset")
        return {"status": "reset", "cleared": ["Qdrant memories + module profiles", f"MongoDB database '{self.s.mongodb_database}'"],
                "dataset": ds.summary() if ds else None}

    # --------------------------------------------------------------- seed
    def _ingest_set(self, set_name: str) -> list[dict[str, Any]]:
        man = self.manifest()
        if set_name not in man["sets"]:
            raise ValidationFailure(f"unknown sample set '{set_name}'")
        results = []
        for item in man["sets"][set_name]:
            text = (Path(self.s.samples_dir) / item["file"]).read_text(encoding="utf-8")
            r = self.ctx.ingestion.ingest(item["source_name"], text, item.get("metadata", {}), item.get("tags", []),
                                          item.get("importance", "normal"), item.get("pinned", False))
            results.append({"source_name": item["source_name"], "status": r["status"], "chunks": r["chunks_created"],
                            "modules": r["module_ids"]})
        return results

    def seed(self) -> dict[str, Any]:
        results = self._ingest_set("core")
        with self.ctx.coordinator.mutate("checkpoint"):
            v = self.ctx.lifecycle.ensure_checkpoint("baseline after seeding the core sample corpus")
        self.ctx.tracker.reset_window()
        return {"status": "seeded", "sources": results, "baseline_version": v["version_id"],
                "usage": self.ctx.budget.usage()}

    # ---------------------------------------------------------- scenarios
    def scenario(self, name: str) -> dict[str, Any]:
        man = self.manifest()
        if name not in man["scenarios"]:
            raise NotFoundError(f"unknown scenario '{name}'", code="scenario_not_found")
        if name == "shortcut":
            return self.replay()
        info = man["scenarios"][name]
        results = self._ingest_set(info.get("set", name))
        out: dict[str, Any] = {"scenario": name, "sources": results}
        if name in ("compression", "emergency"):
            usage = self.ctx.budget.usage()
            target_pct = 80.0 if name == "compression" else 93.0
            budget = math.ceil(usage["active_tokens"] / (target_pct / 100))
            self.ctx.budget.set_override(budget)
            out["budget_override"] = budget
            out["compression"] = self.ctx.compression.check("demo_scenario")
        out["usage"] = self.ctx.budget.usage()
        return out

    def replay(self, fresh_window: bool = True) -> dict[str, Any]:
        p = Path(self.s.replay_queries_path)
        if not p.is_file():
            raise NotFoundError(f"replay file not found: {p}", code="replay_missing")
        queries = json.loads(p.read_text(encoding="utf-8"))["queries"]
        if fresh_window:
            self.ctx.tracker.reset_window()
        done = 0
        for q in queries:
            self.ctx.retrieval.query(q, 5, False)
            done += 1
        w = self.ctx.tracker.window()
        return {"scenario": "shortcut", "queries_replayed": done, "window_queries": w.get("query_count"),
                "adaptation_threshold_reached": w.get("query_count", 0) >= self.s.adaptation_query_batch or
                                                bool(self.ctx.mongo.get_state("last_window_run")),
                "top_pairs": sorted(((k, v) for k, v in (w.get("pair_counts") or {}).items()),
                                    key=lambda kv: -kv[1])[:5],
                "note": "With AUTO_ADAPTATION enabled the 50th query closes the window and runs detection/evaluation; "
                        "otherwise use POST /adaptation/run-window."}
