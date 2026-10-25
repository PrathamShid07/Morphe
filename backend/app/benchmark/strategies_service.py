"""Read-only access to retrieval-strategy comparison runs written by `evaluation/run_strategies.py` (artifacts only; nothing is computed here)."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from ..common import NotFoundError, ValidationFailure

_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
GROUPS = ("validation", "test")
STRATS = ("global_dense", "fixed_graph", "progressive", "soft")


class StrategyRuns:
    def __init__(self, root: str):
        self.root = Path(root)

    @staticmethod
    def _id(x: str, what: str) -> str:
        if not _ID.match(x or ""):
            raise ValidationFailure(f"invalid {what}", code="invalid_id")
        return x

    def _dir(self, run_id: str) -> Path:
        d = self.root / self._id(run_id, "run id")
        if not (d / "manifest.json").is_file():
            raise NotFoundError(f"strategy run {run_id} not found", code="run_not_found")
        return d

    @staticmethod
    def _read(p: Path) -> Any:
        return json.loads(p.read_text(encoding="utf-8"))

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        out = []
        for m in sorted(self.root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                man = self._read(m)
            except Exception:
                continue
            ds = man.get("evaluated", {})
            out.append({"run_id": man.get("run_id", m.parent.name), "status": man.get("status"), "created_at": man.get("created_at"),
                        "finished_at": man.get("finished_at"), "datasets": man.get("datasets", []), "evaluated": ds, "smoke": man.get("smoke", False),
                        "frozen": (m.parent / "frozen_config.json").exists()})
        return out

    def get_run(self, run_id: str) -> dict[str, Any]:
        d = self._dir(run_id)
        out: dict[str, Any] = {"manifest": self._read(d / "manifest.json")}
        if (d / "frozen_config.json").exists():
            out["frozen_config"] = self._read(d / "frozen_config.json")
        if (d / "server_latency.json").exists():  # optional: latency of the four strategies on an indexed Qdrant server (evaluation/run_strategy_latency.py)
            out["server_latency"] = self._read(d / "server_latency.json")
        if (d / "tuning.json").exists():
            t = self._read(d / "tuning.json")
            out["selection"] = t.get("selection")
            out["grids"] = t.get("grids")
        return out

    def group(self, run_id: str, dataset: str, group: str, with_rows: bool = False) -> dict[str, Any]:
        d = self._dir(run_id)
        if group not in GROUPS:
            raise ValidationFailure("group must be 'validation' or 'test'")
        p = d / self._id(dataset, "dataset") / f"{group}.json"
        if not p.is_file():
            raise NotFoundError(f"{dataset}/{group} was not evaluated in run {run_id}", code="group_not_found")
        r = self._read(p)
        if not with_rows:
            r = {k: v for k, v in r.items() if k != "rows"}
        return r

    def rows(self, run_id: str, dataset: str, group: str, *, sort: str = "progressive_vs_global_dense", order: str = "worst", search: str | None = None,
             only: str = "all", limit: int = 25, offset: int = 0) -> dict[str, Any]:
        r = self.group(run_id, dataset, group, with_rows=True)
        rows = r["rows"]
        if search:
            s = search.lower()
            rows = [x for x in rows if s in x["query_id"].lower() or s in x.get("query", "").lower()]
        m = re.fullmatch(r"(fixed_graph|progressive|soft)_vs_(global_dense|fixed_graph)", sort)
        if not m:
            raise ValidationFailure("sort must look like progressive_vs_global_dense")
        s_name, b_name = m.group(1), m.group(2)
        delta = lambda x: x[s_name]["ndcg_at_10"] - x[b_name]["ndcg_at_10"]  # noqa: E731
        if only == "degraded":
            rows = [x for x in rows if delta(x) < -1e-12]
        elif only == "improved":
            rows = [x for x in rows if delta(x) > 1e-12]
        elif only == "excluded":
            rows = [x for x in rows if (x[s_name].get("excluded_relevant") or 0) > 0]
        elif only != "all":
            raise ValidationFailure("only must be all, degraded, improved or excluded")
        rows = sorted(rows, key=lambda x: (delta(x), x["query_id"]), reverse=(order == "best"))
        return {"total": len(rows), "limit": limit, "offset": offset, "sort": sort, "order": order,
                "items": [{**x, "delta_ndcg_at_10": delta(x)} for x in rows[offset:offset + limit]]}

    def export(self, run_id: str, fmt: str) -> tuple[str, str]:
        d = self._dir(run_id)
        man = self._read(d / "manifest.json")
        results: dict[str, Any] = {}
        for ds in man.get("datasets", []):
            for g in GROUPS:
                p = d / ds / f"{g}.json"
                if p.is_file():
                    r = self._read(p)
                    results.setdefault(ds, {})[g] = {k: v for k, v in r.items() if k != "rows"}
        if fmt == "json":
            return json.dumps({"manifest": man, "results": results}, indent=1), "application/json"
        buf = io.StringIO()
        w = csv.writer(buf)
        keys = ["ndcg_at_10", "map_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10", "precision_at_10"]
        w.writerow(["run_id", "dataset", "group", "status", "strategy", *keys, "p50_e2e_ms", "p95_e2e_ms", "mean_eligible_candidates", "candidate_pool_reduction", "fallback_rate"])
        for ds, gs in results.items():
            for g, r in gs.items():
                for s in STRATS:
                    m, lat = r["metrics"][s], r["latency"][s]
                    w.writerow([run_id, ds, g, r.get("status", ""), s, *[f"{m[k]:.6f}" for k in keys], f"{lat['end_to_end']['p50_ms']:.3f}",
                                f"{lat['end_to_end']['p95_ms']:.3f}", f"{lat['mean_eligible_candidates']:.1f}",
                                "" if lat["candidate_pool_reduction_vs_corpus"] is None else f"{lat['candidate_pool_reduction_vs_corpus']:.4f}", f"{lat['fallback_rate']:.4f}"])
        return buf.getvalue(), "text/csv"
