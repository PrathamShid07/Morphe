"""Benchmark job manager for the API: durable job records in MongoDB, one in-process worker, safe artifact access.

Jobs are not a distributed queue: the application already serialises heavy work in-process. A job document is written
as `queued` before anything runs, moves to `running` when the worker starts and to `completed`/`failed` only when the work
has actually finished. Jobs found `queued`/`running` at startup were interrupted by a restart and are marked `failed`.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..common import ConflictError, MorpheError, NotFoundError, ValidationFailure, get_logger, iso, log_event
from ..container import AppContext
from .datasets import DatasetNotAvailable, is_cached, list_datasets, REGISTRY
from .report import BATCH_DIR, batch_summary, load_run, read_json, run_dirs
from .runner import ALL_METHODS, BenchmarkConfig, run_dataset

log = get_logger("benchmark.service")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
ARTIFACT_NAMES = {"manifest.json", "metrics.json", "per_query_metrics.json", "latency.json", "comparisons.json",
                  "adaptation.json", "safety.json", "sweep.json", "logs.jsonl"}
TREC_RE = re.compile(r"^run_(bm25|global_dense|fixed_graph|adaptive_graph)\.trec$")
COLLECTION = "benchmark_runs"


class BenchmarkService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.s = ctx.settings
        self.mongo = ctx.mongo
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="morphe-beir")
        self._lock = threading.Lock()
        self._active: str | None = None
        n = self.mongo.col(COLLECTION).update_many(
            {"status": {"$in": ["queued", "running"]}},
            {"$set": {"status": "failed", "finished_at": iso(), "error": "interrupted by backend restart"}}).modified_count
        if n:
            log_event(log, "benchmark_jobs_interrupted", count=n)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- datasets
    def datasets(self) -> dict[str, Any]:
        rows = list_datasets(self.s.beir_data_dir)
        return {"items": rows, "data_dir": self.s.beir_data_dir, "methods": list(ALL_METHODS),
                "note": "Datasets are cached locally and never committed to git; a missing dataset must be downloaded explicitly."}

    # --------------------------------------------------------------------- run
    def start(self, datasets: list[str], methods: list[str], *, split: str = "test", query_limit: int | None = None,
              download: bool = False, force_embeddings: bool = False, max_windows: int = 6, seed: int = 13,
              safety_probe: bool = True, depth: int = 100, protocol: str = "v2") -> dict[str, Any]:
        for d in datasets:
            if not ID_RE.match(d):
                raise ValidationFailure(f"invalid dataset id {d!r}")
        cfg = BenchmarkConfig(datasets=datasets, methods=methods, split=split, query_limit=query_limit, seed=seed, download=download,
                              force_embeddings=force_embeddings, max_windows=max_windows, safety_probe=safety_probe,
                              data_dir=self.s.beir_data_dir, artifacts_dir=self.s.beir_artifacts_dir, depth=depth, protocol=protocol)
        cfg.graph.seed = seed
        try:
            cfg.validate()
        except ValueError as exc:
            raise ValidationFailure(str(exc), code="invalid_benchmark_config") from exc
        missing = [d for d in datasets if not is_cached(self.s.beir_data_dir, d)]
        if missing and not download:
            raise DatasetNotAvailable(f"dataset(s) {missing} are not cached in {self.s.beir_data_dir}; pass download=true "
                                      "to fetch them from the BEIR host (no substitute dataset is used)")
        with self._lock:
            if self._active:
                raise ConflictError(f"benchmark {self._active} is already running", code="benchmark_running")
            run_id = time.strftime("beir_%Y%m%dT%H%M%S") + "_" + iso()[-4:-1].replace(".", "")
            doc = {"run_id": run_id, "status": "queued", "created_at": iso(), "started_at": None, "finished_at": None,
                   "config": cfg.to_dict(), "datasets": {d: {"status": "queued"} for d in datasets}, "error": None,
                   "progress": [], "official": query_limit is None}
            self.mongo.col(COLLECTION).insert_one(dict(doc))
            self._active = run_id
        self._pool.submit(self._work, run_id, cfg)
        return doc

    def _work(self, run_id: str, cfg: BenchmarkConfig) -> None:
        col = self.mongo.col(COLLECTION)
        col.update_one({"run_id": run_id}, {"$set": {"status": "running", "started_at": iso()}})
        errors: list[str] = []
        embedder = getattr(self.ctx.embedder, "inner", self.ctx.embedder)
        try:
            for ds in cfg.datasets:
                col.update_one({"run_id": run_id}, {"$set": {f"datasets.{ds}.status": "running"}})

                def sink(rec: dict[str, Any], ds: str = ds) -> None:
                    col.update_one({"run_id": run_id}, {"$set": {f"datasets.{ds}.phase": rec["phase"], f"datasets.{ds}.message": rec["message"]},
                                                        "$push": {"progress": {"$each": [{k: rec[k] for k in ("t", "phase", "message")} | {"dataset": ds}],
                                                                               "$slice": -60}}})
                try:
                    run_dataset(cfg, self.s, embedder, ds, run_id, sink=sink)
                    col.update_one({"run_id": run_id}, {"$set": {f"datasets.{ds}.status": "completed"}})
                except Exception as exc:
                    msg = f"{type(exc).__name__}: {exc}"[:400]
                    errors.append(f"{ds}: {msg}")
                    col.update_one({"run_id": run_id}, {"$set": {f"datasets.{ds}.status": "failed", f"datasets.{ds}.error": msg}})
            col.update_one({"run_id": run_id}, {"$set": {"status": "failed" if errors else "completed", "finished_at": iso(),
                                                         "error": "; ".join(errors) or None}})
        except BaseException as exc:  # never leave a job stuck as running
            col.update_one({"run_id": run_id}, {"$set": {"status": "failed", "finished_at": iso(), "error": f"{type(exc).__name__}: {exc}"[:400]}})
        finally:
            with self._lock:
                self._active = None

    # ------------------------------------------------------------------- reads
    def status(self) -> dict[str, Any]:
        doc = self.mongo.col(COLLECTION).find_one({"run_id": self._active}) if self._active else None
        last = list(self.mongo.col(COLLECTION).find({}).sort("created_at", -1).limit(1))
        return {"running": bool(self._active), "active_run": _clean(doc) if doc else None,
                "last_run": _clean(last[0]) if last else None}

    def _job(self, run_id: str) -> dict[str, Any] | None:
        d = self.mongo.col(COLLECTION).find_one({"run_id": run_id})
        return _clean(d) if d else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for d in self.mongo.col(COLLECTION).find({}).sort("created_at", -1).limit(200):
            rows[d["run_id"]] = {**_clean(d), "source": "job"}
        for d in run_dirs(self.s.beir_artifacts_dir):
            m = _manifest_brief(d)
            r = rows.setdefault(m["run_id"], {"run_id": m["run_id"], "status": "completed", "created_at": m.get("started_at"),
                                              "datasets": {}, "source": "artifacts", "error": None, "official": None})
            r["datasets"].setdefault(m["dataset_id"], {"status": m["status"]})
            r["datasets"][m["dataset_id"]].update({"status": m["status"], "official_test_split": m.get("official_test_split"),
                                                   "smoke": m.get("smoke"), "queries": m.get("queries")})
            if r["source"] == "artifacts" and m["status"] != "completed":
                r["status"] = m["status"]
        out = sorted(rows.values(), key=lambda r: r.get("created_at") or "", reverse=True)
        return out[:limit]

    def get_run(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        job = self._job(run_id)
        try:
            summary = batch_summary(self.s.beir_artifacts_dir, run_id)
        except FileNotFoundError:
            summary = None
        if not job and not summary:
            raise NotFoundError(f"benchmark run {run_id} not found", code="benchmark_run_not_found")
        return {"run_id": run_id, "job": job, "manifests": summary["datasets"] if summary else {}}

    def results(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        try:
            summary = batch_summary(self.s.beir_artifacts_dir, run_id)
        except FileNotFoundError:
            if self._job(run_id):
                return {"run_id": run_id, "datasets": {}, "macro_average": {}, "note": "no results saved yet", "job": self._job(run_id)}
            raise NotFoundError(f"benchmark run {run_id} not found", code="benchmark_run_not_found") from None
        out: dict[str, Any] = {"run_id": run_id, "macro_average": summary["macro_average"], "macro_note": summary["macro_note"], "datasets": {}}
        for ds, man in summary["datasets"].items():
            entry: dict[str, Any] = {"manifest": man}
            if man["status"] == "completed":
                r = load_run(Path(self.s.beir_artifacts_dir) / ds / run_id)
                entry.update({"metrics": r["metrics"]["methods"], "latency": r["latency"]["methods"], "system": r["latency"]["system"],
                              "comparisons": [{k: v for k, v in c.items() if k not in ("most_improved", "most_regressed")} for c in r["comparisons"]],
                              "adaptation": r["adaptation"], "safety": r["safety"], "sweep": r.get("sweep")})
            out["datasets"][ds] = entry
        return out

    def compare(self, run_id: str, dataset: str, baseline: str, treatment: str) -> dict[str, Any]:
        self._check_id(run_id), self._check_id(dataset)
        d = Path(self.s.beir_artifacts_dir) / dataset / run_id
        if not (d / "comparisons.json").exists():
            raise NotFoundError(f"no comparisons saved for {dataset}/{run_id}", code="benchmark_run_not_found")
        for c in read_json(d / "comparisons.json"):
            if c["baseline"] == baseline and c["treatment"] == treatment:
                return c
        have = [f"{c['baseline']}→{c['treatment']}" for c in read_json(d / "comparisons.json")]
        raise NotFoundError(f"comparison {baseline}→{treatment} is not available for this run (have: {have})", code="comparison_not_found")

    # ------------------------------------------------------------- artifacts / export
    @staticmethod
    def _check_id(x: str) -> None:
        if not ID_RE.match(x):
            raise ValidationFailure(f"invalid identifier {x!r}")

    def artifact_files(self, run_id: str) -> dict[str, list[str]]:
        self._check_id(run_id)
        out: dict[str, list[str]] = {}
        for d in run_dirs(self.s.beir_artifacts_dir):
            if d.name == run_id:
                out[d.parent.name] = sorted(p.name for p in d.iterdir() if p.name in ARTIFACT_NAMES or TREC_RE.match(p.name))
        if not out:
            raise NotFoundError(f"no artifacts for run {run_id}", code="benchmark_run_not_found")
        return out

    def artifact_path(self, run_id: str, dataset: str, name: str) -> Path:
        self._check_id(run_id), self._check_id(dataset)
        if name not in ARTIFACT_NAMES and not TREC_RE.match(name):
            raise NotFoundError(f"unknown artifact {name!r}", code="artifact_not_found")
        root = Path(self.s.beir_artifacts_dir).resolve()
        p = (root / dataset / run_id / name).resolve()
        if root not in p.parents or not p.is_file():
            raise NotFoundError(f"artifact {dataset}/{run_id}/{name} not found", code="artifact_not_found")
        return p

    def export(self, run_id: str, fmt: str) -> tuple[str, str]:
        res = self.results(run_id)
        if fmt == "json":
            import json
            return json.dumps(res, indent=1, default=str), "application/json"
        buf = io.StringIO()
        w = csv.writer(buf)
        cols = ["ndcg_at_10", "map_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10", "precision_at_10"]
        w.writerow(["dataset", "official_test_split", "smoke", "method", *cols, "p50_e2e_ms", "p95_e2e_ms", "p50_retrieval_ms", "p95_retrieval_ms",
                    "mean_eligible_candidates", "candidate_pool_reduction", "fallback_rate"])
        for ds, e in res["datasets"].items():
            if "metrics" not in e:
                continue
            for m, v in e["metrics"].items():
                lat = e["latency"][m]
                w.writerow([ds, e["manifest"].get("official_test_split"), e["manifest"].get("smoke"), m, *[v[c] for c in cols],
                            lat["end_to_end"]["p50_ms"], lat["end_to_end"]["p95_ms"], lat["retrieval"]["p50_ms"], lat["retrieval"]["p95_ms"],
                            lat["mean_eligible_candidates"], lat.get("candidate_pool_reduction_vs_corpus"), lat["fallback_rate"]])
        return buf.getvalue(), "text/csv"


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    d = dict(d)
    d.pop("_id", None)
    return d


def _manifest_brief(d: Path) -> dict[str, Any]:
    m = read_json(d / "manifest.json")
    return {"run_id": m["run_id"], "dataset_id": m["dataset_id"], "status": m["status"], "started_at": m.get("started_at"),
            "official_test_split": m.get("official_test_split"), "smoke": m.get("smoke"),
            "queries": (m.get("dataset") or {}).get("test_group_queries")}
