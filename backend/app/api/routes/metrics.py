"""Metrics and evaluation routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...container import AppContext
from ..deps import get_ctx
from ..schemas.models import EvaluationRunRequest, StrategyCompareRequest, VersionCompareRequest

router = APIRouter(tags=["metrics"])


@router.get("/metrics/summary")
def summary(ctx: AppContext = Depends(get_ctx)):
    return ctx.metrics.summary()


@router.get("/metrics/history")
def history(window: str = Query("1h", pattern="^(1h|24h|7d)$"), ctx: AppContext = Depends(get_ctx)):
    return ctx.metrics.history(window)


@router.get("/evaluation/dataset", tags=["evaluation"])
def dataset(ctx: AppContext = Depends(get_ctx)):
    ds = ctx.datasets.current()
    ev = ctx.evaluator.build_context()
    return {**ds.summary(), "usable_queries": len(ev.queries), "skipped_queries": len(ev.skipped),
            "labels_missing": ev.labels_missing, "min_eval_queries": ctx.settings.min_eval_queries,
            "thresholds": ctx.policy.thresholds(),
            "sample": [{"query_id": q.query_id, "query": q.query, "split": q.split, "tags": q.tags,
                        "relevant": len(q.relevant_memory_ids)} for q in ds.queries[:100]]}


@router.post("/evaluation/dataset/load", tags=["evaluation"])
def load_dataset(ctx: AppContext = Depends(get_ctx)):
    """(Re)load the labeled fixture file into MongoDB. A broken fixture never replaces a good dataset."""
    ds = ctx.datasets.load_fixture()
    return ds.summary()


@router.post("/evaluation/run", tags=["evaluation"])
def run_eval(body: EvaluationRunRequest, ctx: AppContext = Depends(get_ctx)):
    """Evaluate the live architecture (or a historical version in an isolated sandbox) on the labeled dataset."""
    rep = ctx.evaluation.run(body.mode, body.version_id, body.splits)
    return {k: v for k, v in rep.items() if k != "per_query"} | {"per_query_count": len(rep.get("per_query", []))}


@router.get("/evaluation/runs", tags=["evaluation"])
def runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), ctx: AppContext = Depends(get_ctx)):
    items, total = ctx.evaluation.list_runs(limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/evaluation/runs/{run_id}", tags=["evaluation"])
def get_run(run_id: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.evaluation.get_run(run_id)


@router.post("/evaluation/compare-modes", tags=["evaluation"])
def compare_modes(ctx: AppContext = Depends(get_ctx)):
    """Global vector search vs fixed (initial) graph vs adaptive (current) graph on the same dataset."""
    return ctx.evaluation.compare_modes()


@router.post("/evaluation/compare-versions", tags=["evaluation"])
def compare_versions(body: VersionCompareRequest, ctx: AppContext = Depends(get_ctx)):
    return ctx.evaluation.compare_versions(body.from_version, body.to_version, body.evaluate)


@router.post("/evaluation/compare-strategies", tags=["evaluation"])
def compare_strategies(body: StrategyCompareRequest, ctx: AppContext = Depends(get_ctx)):
    """Run one query through global dense, fixed graph, progressive expansion and soft routing on the live architecture (not tracked)."""
    from ...evaluation.strategy_compare import compare_strategies as run
    return run(ctx, body.query, body.query_id, body.top_k, body.modes)
