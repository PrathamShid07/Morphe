"""BEIR benchmark routes (datasets, run, status, results, comparison, export, artifacts)."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ...benchmark.runner import ALL_METHODS
from ...benchmark.service import BenchmarkService
from ...benchmark.strategies_service import StrategyRuns
from ...container import AppContext
from ..deps import get_ctx
from ...common import MorpheError

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def get_benchmarks(request: Request) -> BenchmarkService:
    svc = getattr(request.app.state, "benchmarks", None)
    if svc is None:
        raise MorpheError("benchmark service is not ready", code="not_ready", status_code=503)
    return svc


class BenchmarkRunRequest(BaseModel):
    datasets: list[str] = Field(default_factory=lambda: ["scifact"], min_length=1, max_length=8,
                                description="BEIR dataset ids (e.g. scifact, nfcorpus, arguana)")
    methods: list[Literal["bm25", "global_dense", "fixed_graph", "adaptive_graph"]] = Field(default_factory=lambda: list(ALL_METHODS), min_length=1)
    split: Literal["test"] = "test"
    query_limit: int | None = Field(None, ge=1, le=100000, description="smoke run: caps the test/validation groups; marks the run non-official")
    retrieval_depth: int = Field(100, ge=100, le=1000, description="ranked-list depth (>= 100 so Recall@100 is defined)")
    seed: int = 13
    protocol: Literal["v1", "v2"] = Field("v2", description="validation protocol (see docs/BEIR_BENCHMARK_PLAN.md addendum)")
    max_windows: int = Field(6, ge=0, le=20)
    download: bool = False
    force_embeddings: bool = False
    safety_probe: bool = True


@router.get("/datasets")
def datasets(svc: BenchmarkService = Depends(get_benchmarks)):
    return svc.datasets()


@router.post("/run", status_code=202, responses={404: {"description": "dataset not cached"}, 409: {"description": "a benchmark is running"}})
def run(body: BenchmarkRunRequest, svc: BenchmarkService = Depends(get_benchmarks)):
    """Queue a benchmark. Returns 202 with the job document (`queued`); poll /benchmarks/status or /benchmarks/runs/{id}."""
    return svc.start(body.datasets, list(body.methods), split=body.split, query_limit=body.query_limit, download=body.download,
                     force_embeddings=body.force_embeddings, max_windows=body.max_windows, seed=body.seed,
                     safety_probe=body.safety_probe, depth=body.retrieval_depth, protocol=body.protocol)


@router.get("/status")
def status(svc: BenchmarkService = Depends(get_benchmarks)):
    return svc.status()


@router.get("/runs")
def runs(limit: int = Query(50, ge=1, le=200), svc: BenchmarkService = Depends(get_benchmarks)):
    return {"items": svc.list_runs(limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, svc: BenchmarkService = Depends(get_benchmarks)):
    return svc.get_run(run_id)


@router.get("/runs/{run_id}/results")
def results(run_id: str, svc: BenchmarkService = Depends(get_benchmarks)):
    return svc.results(run_id)


@router.get("/runs/{run_id}/compare")
def compare(run_id: str, dataset: str, baseline: str = "fixed_graph", treatment: str = "adaptive_graph",
            svc: BenchmarkService = Depends(get_benchmarks)):
    """Paired comparison (treatment − baseline) with bootstrap interval and the most improved / regressed queries."""
    return svc.compare(run_id, dataset, baseline, treatment)


@router.get("/runs/{run_id}/export")
def export(run_id: str, format: Literal["json", "csv"] = "json", svc: BenchmarkService = Depends(get_benchmarks)):
    body, media = svc.export(run_id, format)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{run_id}.{format}"'})


@router.get("/runs/{run_id}/artifacts")
def artifacts(run_id: str, svc: BenchmarkService = Depends(get_benchmarks)):
    return {"run_id": run_id, "datasets": svc.artifact_files(run_id)}


@router.get("/runs/{run_id}/artifacts/{dataset}/{name}")
def artifact(run_id: str, dataset: str, name: str, svc: BenchmarkService = Depends(get_benchmarks)):
    """Download a saved manifest / metrics file / TREC run file (allow-listed names, path traversal impossible)."""
    p = svc.artifact_path(run_id, dataset, name)
    return FileResponse(p, filename=f"{dataset}_{run_id}_{name}")


# ------------------------------------------------------------------------------------------ retrieval-strategy comparison runs
def strategy_runs(ctx: AppContext = Depends(get_ctx)) -> StrategyRuns:
    return StrategyRuns(ctx.settings.beir_strategies_dir)


@router.get("/strategies/runs", tags=["benchmarks"])
def list_strategy_runs(svc: StrategyRuns = Depends(strategy_runs)):
    """Saved comparisons of global dense / fixed graph / progressive expansion / soft routing (evaluation/run_strategies.py)."""
    return {"items": svc.list_runs()}


@router.get("/strategies/runs/{run_id}", tags=["benchmarks"])
def get_strategy_run(run_id: str, svc: StrategyRuns = Depends(strategy_runs)):
    """Manifest, frozen configuration and the validation-only selection evidence."""
    return svc.get_run(run_id)


@router.get("/strategies/runs/{run_id}/export", tags=["benchmarks"])
def export_strategy_run(run_id: str, format: Literal["json", "csv"] = "json", svc: StrategyRuns = Depends(strategy_runs)):
    body, media = svc.export(run_id, format)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{run_id}_strategies.{format}"'})


@router.get("/strategies/runs/{run_id}/{dataset}/{group}", tags=["benchmarks"])
def strategy_group(run_id: str, dataset: str, group: Literal["validation", "test"], svc: StrategyRuns = Depends(strategy_runs)):
    """Metrics, latency, paired comparisons and routing diagnostics for one dataset and group (per-query rows are served by /rows)."""
    return svc.group(run_id, dataset, group)


@router.get("/strategies/runs/{run_id}/{dataset}/{group}/rows", tags=["benchmarks"])
def strategy_rows(run_id: str, dataset: str, group: Literal["validation", "test"],
                  sort: str = Query("progressive_vs_global_dense", pattern="^(fixed_graph|progressive|soft)_vs_(global_dense|fixed_graph)$"),
                  order: Literal["worst", "best"] = "worst", search: str | None = Query(None, max_length=100),
                  only: Literal["all", "degraded", "improved", "excluded"] = "all", limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                  svc: StrategyRuns = Depends(strategy_runs)):
    """Per-query inspector rows (nDCG@10 and routing facts per strategy), sortable by the change against a control."""
    return svc.rows(run_id, dataset, group, sort=sort, order=order, search=search, only=only, limit=limit, offset=offset)
