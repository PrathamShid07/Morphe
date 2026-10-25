"""Controlled comparison of four retrieval strategies on BEIR: global dense, fixed graph, progressive expansion, soft graph routing.

    python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id strat_01 --phase all

Protocol (written before any strategy was evaluated; see docs/BEIR_RETRIEVAL_ABLATIONS.md):
  tune      On the VALIDATION group only, sweep a small fixed grid of progressive-expansion and soft-routing settings and pick ONE
            configuration for each with the pre-declared rules below. Test labels are not read in this phase.
  freeze    The chosen configuration is written to `frozen_config.json` and its SHA-256 is recorded in the run manifest.
  evaluate  With the frozen configuration: validation group (development numbers, optimistic because they were used for tuning) and, only
            if a frozen config exists, the TEST group once (confirmatory where the test split is untouched; ArguAna's is an internal partition,
            and SciFact's test labels were already seen under benchmark protocol v1, so it is exploratory).
All strategies run through the real `RetrievalEngine` on the same cached embeddings, the same initial deterministic architecture (the adapted
architectures are deliberately NOT used: the retrieval strategy is the only variable) and the same ranked-list depth / metric code as the frozen
v1/v2 benchmark. Output goes to a NEW directory under `<artifacts>/beir_strategies/<run_id>/`; an existing run id is refused.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from ..benchmark.beir_eval import HEADLINE, METRIC_KEYS, consistency_gap, evaluate_beir, mean_of, per_query_metrics
from ..benchmark.corpus import DocMap, build_initial_architecture, embed_corpus, ingest_corpus, make_context, verify_mapping
from ..benchmark.methods import DEPTH, FALLBACK_K, MethodRun, embed_queries_timed, sandbox_engine, summarize_latency
from ..benchmark.runner import BenchmarkConfig, compare_runs, environment_info, load_for_protocol
from ..benchmark.splits import build_split_protocol, group_qrels, group_queries
from ..common import iso
from ..config import Settings
from ..ingestion.embedder import build_embedder
from ..retrieval.controller import RetrievalEngine
from .metrics import paired_bootstrap

STRATEGIES = ("global_dense", "fixed_graph", "progressive", "soft")
MODE = {"global_dense": "global", "fixed_graph": "graph", "progressive": "progressive", "soft": "soft"}
ROOT_NAME = "beir_strategies"

# ---- the grids and selection rules are fixed here, in code, before any strategy is evaluated ---------------------------------------
PROGRESSIVE_GRID = ([{"policy": p, "patience": pa, "step": st} for p in ("profile", "blend") for pa in (1, 2) for st in (2, 4)]
                    + [{"policy": "graph", "patience": 1, "step": 3}])
SOFT_GRID = [{"weight": 0.0, "share": 0.0}] + [{"weight": w, "share": g} for w in (0.01, 0.02, 0.05, 0.1) for g in (0.0, 0.5, 1.0)]
NON_INFERIORITY = 0.01  # a progressive config qualifies if its validation nDCG@10 is within this of global dense on EVERY dataset
SELECTION_RULES = {
    "progressive": (f"Among configurations whose validation nDCG@10 is within {NON_INFERIORITY} of global dense on every dataset, choose the one with the fewest "
                    "mean candidate records examined (including the fallback's whole corpus); ties broken by higher macro nDCG@10. If none qualifies, choose the "
                    "highest macro nDCG@10 and record that no configuration was non-inferior."),
    "soft": ("Among configurations with weight > 0 (weight 0 is plain dense retrieval), choose the highest macro validation nDCG@10; ties by smaller weight. "
             "Whether the gain is reliable is judged separately by paired bootstrap against weight 0."),
}


# ------------------------------------------------------------------------------------------------------------- environment
@dataclass
class Env:
    cfg: BenchmarkConfig
    base: Settings
    ctx: Any
    data: Any
    proto: Any
    doc_map: DocMap
    state: Any
    vectors: dict[str, np.ndarray]
    engine: RetrievalEngine
    mod_of: dict[str, str]
    dataset_id: str
    info: dict[str, Any] = field(default_factory=dict)


def strategy_settings(cfg: BenchmarkConfig) -> dict[str, Any]:
    return dict(seed_module_count=cfg.seed_module_count, max_candidate_modules=cfg.max_candidate_modules, fallback_min_score=cfg.fallback_min_score)


@contextlib.contextmanager
def dataset_env(cfg: BenchmarkConfig, base: Settings, embedder, dataset_id: str, run_id: str, **settings_over: Any) -> Iterator[Env]:
    data = load_for_protocol(dataset_id, cfg)
    proto = build_split_protocol(data, cfg.seed, cfg.query_limit)
    ids, mat, emb = embed_corpus(embedder, dataset_id, data.corpus, data.source["corpus_sha256"], str(Path(cfg.data_dir) / "embeddings"))
    doc_map = DocMap.build(dataset_id, ids)
    vectors = {doc_map.doc_to_mem[d]: mat[i] for i, d in enumerate(ids)}
    state0, ginfo = build_initial_architecture(dataset_id, doc_map, ids, mat, data.corpus, cfg.graph)
    capacity = int(cfg.capacity_factor * ginfo["module_size"]["median"])
    ctx = make_context(base, embedder, dataset_id, run_id, split_capacity_threshold=capacity, **{**strategy_settings(cfg), **settings_over})
    try:
        ingest_corpus(ctx, dataset_id, data.corpus, ids, mat, doc_map, state0)
        verify_mapping(ctx, doc_map)
        state = ctx.persistence.export_live_state()
        engine = sandbox_engine(ctx, state, vectors)
        yield Env(cfg, base, ctx, data, proto, doc_map, state, vectors, engine, state.module_of(), dataset_id,
                  {"documents": len(ids), "modules": ginfo["modules"], "groups": ginfo["groups"], "module_size": ginfo["module_size"],
                   "embedding": {k: emb[k] for k in ("model", "dimension", "preprocess", "corpus_sha256")}})
    finally:
        try:
            ctx.mongo.client.drop_database(ctx.mongo.db.name)
        except Exception:
            pass
        ctx.close()


def variant(env: Env, **over: Any) -> RetrievalEngine:
    """The same store and graph with different strategy settings; baseline engines are never modified."""
    return RetrievalEngine(env.ctx.settings.model_copy(update=over), env.engine.store, env.engine.graph_provider)


def progressive_over(c: dict[str, Any]) -> dict[str, Any]:
    return {"progressive_policy": c["policy"], "progressive_patience": c["patience"], "progressive_step_modules": c["step"]}


def soft_over(c: dict[str, Any]) -> dict[str, Any]:
    return {"soft_weight": c["weight"], "soft_graph_share": c["share"]}


def group_data(env: Env, group: str, limit: int | None) -> tuple[dict[str, str], dict[str, dict[str, int]]]:
    qs = group_queries(env.data, env.proto, group)
    qr = group_qrels(env.data, env.proto, group)
    keep = [q for q in sorted(qs) if any(g > 0 and d in env.doc_map.doc_to_mem for d, g in qr[q].items())]  # at least one retrievable relevant doc
    keep = keep[:limit] if limit else keep
    return {q: qs[q] for q in keep}, {q: qr[q] for q in keep}


# ------------------------------------------------------------------------------------------------------------------ tuning
def _stats(outs: dict[str, Any], n_corpus: int) -> dict[str, float]:
    n = len(outs)
    return {"mean_eligible_candidates": sum(o.guided_candidate_count for o in outs.values()) / n,
            "mean_candidates_incl_fallback": sum(o.candidate_count for o in outs.values()) / n,
            "fallback_rate": sum(1 for o in outs.values() if o.fallback_used) / n,
            "mean_modules_in_guided_stage": sum(len(o.candidates) for o in outs.values()) / n}


def run_config(engine: RetrievalEngine, mode: str, qvecs: dict[str, np.ndarray], qrels: dict, doc_map: DocMap) -> dict[str, Any]:
    outs = {q: engine.retrieve(v, DEPTH, mode, fallback_k=FALLBACK_K) for q, v in qvecs.items()}
    results = {q: {doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in o.results} for q, o in outs.items()}
    pq = per_query_metrics(qrels, results)
    return {"metrics": {k: mean_of(pq, k) for k in METRIC_KEYS}, "ndcg_per_query": [pq[q]["ndcg_at_10"] for q in sorted(qrels)],
            **_stats(outs, len(doc_map.doc_to_mem))}


def tune_dataset(env: Env, embedder, limit: int | None) -> dict[str, Any]:
    """Sweeps on the validation group. Test labels are never touched here."""
    queries, qrels = group_data(env, "validation", limit)
    qvecs, _ = embed_queries_timed(embedder, queries)
    g = run_config(env.engine, "global", qvecs, qrels, env.doc_map)
    fixed = run_config(env.engine, "graph", qvecs, qrels, env.doc_map)
    prog = [{"config": c, **run_config(variant(env, **progressive_over(c)), "progressive", qvecs, qrels, env.doc_map)} for c in PROGRESSIVE_GRID]
    soft = [{"config": c, **run_config(variant(env, **soft_over(c)), "soft", qvecs, qrels, env.doc_map)} for c in SOFT_GRID]
    return {"dataset": env.dataset_id, "group": "validation", "queries": len(qrels), "global_dense": g, "fixed_graph": fixed,
            "progressive": prog, "soft": soft}


def select_configs(tuning: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Pure function of the validation sweeps: applies SELECTION_RULES, returns the chosen configs and the full evidence."""
    ds = sorted(tuning)
    nd = lambda r: r["metrics"]["ndcg_at_10"]  # noqa: E731
    table_p = []
    for i, c in enumerate(PROGRESSIVE_GRID):
        deltas = {d: nd(tuning[d]["progressive"][i]) - nd(tuning[d]["global_dense"]) for d in ds}
        work = {d: tuning[d]["progressive"][i]["mean_candidates_incl_fallback"] for d in ds}
        # work = records examined, as a share of what global search examines, so datasets of different size weigh equally
        share = {d: work[d] / max(1.0, tuning[d]["global_dense"]["mean_candidates_incl_fallback"]) for d in ds}
        table_p.append({"config": c, "ndcg_delta_vs_global": deltas, "macro_ndcg": float(np.mean([nd(tuning[d]["progressive"][i]) for d in ds])),
                        "work_by_dataset": work, "work_share_by_dataset": share, "macro_work_share": float(np.mean(list(share.values()))),
                        "fallback_rate_by_dataset": {d: tuning[d]["progressive"][i]["fallback_rate"] for d in ds},
                        "non_inferior": all(v >= -NON_INFERIORITY for v in deltas.values())})
    ok = [t for t in table_p if t["non_inferior"]]
    if ok:
        best_p = min(ok, key=lambda t: (t["macro_work_share"], -t["macro_ndcg"]))
        note = "selected among configurations non-inferior to global dense on every validation dataset"
    else:
        best_p = max(table_p, key=lambda t: t["macro_ndcg"])
        note = "NO configuration was non-inferior to global dense on every validation dataset; the highest macro nDCG@10 was chosen"
    table_s = []
    for i, c in enumerate(SOFT_GRID):
        table_s.append({"config": c, "macro_ndcg": float(np.mean([nd(tuning[d]["soft"][i]) for d in ds])),
                        "ndcg_delta_vs_dense": {d: nd(tuning[d]["soft"][i]) - nd(tuning[d]["soft"][0]) for d in ds}})
    cand = [t for t in table_s if t["config"]["weight"] > 0]
    best_s = max(cand, key=lambda t: (round(t["macro_ndcg"], 6), -t["config"]["weight"]))
    si = SOFT_GRID.index(best_s["config"])
    boot = {d: paired_bootstrap(tuning[d]["soft"][0]["ndcg_per_query"], tuning[d]["soft"][si]["ndcg_per_query"], 1000, 13) for d in ds}
    return {"rules": SELECTION_RULES, "non_inferiority_margin": NON_INFERIORITY, "datasets": ds,
            "progressive": {"selected": best_p["config"], "note": note, "table": table_p},
            "soft": {"selected": best_s["config"], "table": table_s, "bootstrap_vs_weight_zero_on_validation": boot,
                     "reliable_gain": bool(all(b["mean_diff"] > 0 for b in boot.values()) and any(b["excludes_zero"] for b in boot.values())),
                     "note": "weight 0 is plain dense retrieval; a gain is called reliable only if it is positive on every dataset and the paired 95% interval excludes 0 on at least one"}}


def frozen_config(cfg: BenchmarkConfig, sel: dict[str, Any]) -> dict[str, Any]:
    p, s = sel["progressive"]["selected"], sel["soft"]["selected"]
    code = ["retrieval/strategies.py", "retrieval/controller.py", "evaluation/run_strategies.py"]
    base = Path(__file__).resolve().parent.parent
    return {"frozen_at": iso(), "protocol": "strategies-v1",
            "global_dense": {"mode": "global"}, "fixed_graph": {"mode": "graph", "seed_modules": cfg.seed_module_count, "max_candidate_modules": cfg.max_candidate_modules,
                                                             "fallback_min_score": cfg.fallback_min_score, "fallback_decision_depth": FALLBACK_K},
            "progressive": {**p, "seed_modules": 3, "max_modules": 12, "max_steps": 4, "fallback_on_budget": True, "fallback_min_score": cfg.fallback_min_score,
                            "stability_k": FALLBACK_K},
            "soft": {**s, "candidate_pool": 100, "seed_modules": 3, "graph_depth": 2, "min_coaccess_observations": 10},
            "depth": DEPTH, "selection_rules": SELECTION_RULES, "selected_from": "validation group only",
            "code_sha256": {c: hashlib.sha256((base / c).read_bytes()).hexdigest() for c in code}}


def frozen_overrides(fz: dict[str, Any]) -> dict[str, dict[str, Any]]:
    p, s = fz["progressive"], fz["soft"]
    return {"progressive": {"progressive_policy": p["policy"], "progressive_patience": p["patience"], "progressive_step_modules": p["step"],
                            "progressive_seed_modules": p["seed_modules"], "progressive_max_modules": p["max_modules"], "progressive_max_steps": p["max_steps"],
                            "progressive_fallback_on_budget": p["fallback_on_budget"]},
            "soft": {"soft_weight": s["weight"], "soft_graph_share": s["share"], "soft_candidate_pool": s["candidate_pool"],
                     "soft_seed_modules": s["seed_modules"], "soft_graph_depth": s["graph_depth"]}}


# --------------------------------------------------------------------------------------------------------------- evaluation
def run_group(env: Env, embedder, fz: dict[str, Any], group: str, reps: int, limit: int | None) -> dict[str, Any]:
    queries, qrels = group_data(env, group, limit)
    qvecs, embed_ms = embed_queries_timed(embedder, queries)
    ov = frozen_overrides(fz)
    engines = {"global_dense": env.engine, "fixed_graph": env.engine, "progressive": variant(env, **ov["progressive"]), "soft": variant(env, **ov["soft"])}
    names = list(STRATEGIES)
    results: dict[str, dict] = {s: {} for s in names}
    lat: dict[str, dict] = {s: {} for s in names}
    rows: dict[str, dict] = {s: {} for s in names}
    for qi, (q, qv) in enumerate(qvecs.items()):
        r = qi % len(names)
        order = names[r:] + names[:r]  # rotate the order each query so machine drift hits every strategy equally
        for s in order:
            engines[s].retrieve(qv, DEPTH, MODE[s], fallback_k=FALLBACK_K)  # warm-up
        times = {s: [] for s in names}
        stage = {s: {k: [] for k in ("seed", "expand", "search", "rank", "fallback")} for s in names}
        last: dict[str, Any] = {}
        for _ in range(max(1, reps)):
            for s in order:
                t0 = time.perf_counter()
                last[s] = engines[s].retrieve(qv, DEPTH, MODE[s], fallback_k=FALLBACK_K)
                times[s].append((time.perf_counter() - t0) * 1000)
                for k in stage[s]:
                    stage[s][k].append(last[s].timings.get(k, 0.0))
        rel = {d for d, g in qrels[q].items() if g > 0 and d in env.doc_map.doc_to_mem}
        for s in names:
            o = last[s]
            results[s][q] = {env.doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in o.results}
            retr = statistics.median(times[s])
            searched = None if (s in ("global_dense", "soft") or o.fallback_used) else {c.module_id for c in o.candidates}
            excluded = 0 if searched is None else sum(1 for d in rel if env.mod_of[env.doc_map.doc_to_mem[d]] not in searched)
            useful = sum(1 for st in o.stages if st.get("top_k_changed"))
            prog = o.extra.get("progressive", {})
            sf = o.extra.get("soft", {})
            lat[s][q] = {"retrieval_ms": retr, "embed_ms": embed_ms[q], "e2e_ms": retr + embed_ms[q],
                         **{f"{k}_ms": statistics.median(v) for k, v in stage[s].items()},
                         "eligible_candidates": float(o.guided_candidate_count), "candidates_incl_fallback": float(o.candidate_count),
                         "fallback": 1.0 if o.fallback_used else 0.0, "results_returned": float(len(o.results)),
                         "candidate_modules": float(len(o.candidates))}
            rows[s][q] = {"modules_searched": None if searched is None else len(searched), "excluded_relevant": excluded, "relevant_total": len(rel),
                          "fallback_reason": o.fallback_reason, "stages_run": prog.get("stages_run"), "stop_reason": prog.get("stop_reason"),
                          "useful_expansion_steps": useful if o.stages else None, "soft_promoted": sf.get("promoted"), "soft_demoted": sf.get("demoted")}
    runs = {s: MethodRun(s, results[s], lat[s], {"mode": MODE[s], "depth": DEPTH, "fallback_k": FALLBACK_K, "repetitions": reps}) for s in names}
    n_corpus = len(env.doc_map.doc_to_mem)
    metrics, pq, gaps = {}, {}, {}
    for s, run in runs.items():
        agg = evaluate_beir(qrels, run.results)
        pq[s] = per_query_metrics(qrels, run.results)
        gaps[s] = consistency_gap(agg, pq[s])
        if gaps[s] > 1e-4:
            raise AssertionError(f"per-query metrics disagree with BEIR aggregate for {s}: {gaps[s]}")
        metrics[s] = {k: v for k, v in agg.items() if k != "raw"}
    latency = {s: summarize_latency(run, n_corpus) for s, run in runs.items()}
    comparisons = []
    for base_name in ("global_dense", "fixed_graph"):
        for s in ("fixed_graph", "progressive", "soft"):
            if s != base_name and not (base_name == "fixed_graph" and s == "fixed_graph"):
                comparisons.append(compare_runs(qrels, runs[base_name], runs[s], pq[base_name], pq[s], queries, 1000, env.cfg.seed, f"{s}_vs_{base_name}"))
    return {"group": group, "queries": len(qrels), "metrics": metrics, "latency": latency, "comparisons": comparisons, "metric_consistency_gap": gaps,
            "diagnostics": diagnostics(qrels, runs, pq, rows, env), "repetitions": reps,
            "rows": [{"query_id": q, "query": queries[q][:300], **{s: {**{k: pq[s][q][k] for k in METRIC_KEYS}, "e2e_ms": lat[s][q]["e2e_ms"], "retrieval_ms": lat[s][q]["retrieval_ms"],
                                            "eligible_candidates": lat[s][q]["eligible_candidates"], "fallback": lat[s][q]["fallback"], **rows[s][q]}
                                         for s in names}} for q in sorted(qrels)]}


def _top10(run: MethodRun, q: str) -> set[str]:
    return {d for d, _ in sorted(run.results[q].items(), key=lambda kv: (-kv[1], kv[0]))[:10]}


def diagnostics(qrels, runs: dict[str, MethodRun], pq, rows, env: Env) -> dict[str, Any]:
    """Counting rules: a query is improved/degraded when its nDCG@10 differs by more than 1e-12; a relevant document is 'lost' when it is in the baseline's
    top-10 and absent from the strategy's top-10, 'recovered/gained' for the reverse; 'excluded by routing' = a relevant document whose module the strategy never
    searched (global fallback searches everything, so a query that fell back has none)."""
    out: dict[str, Any] = {}
    rel = {q: {d for d, g in qrels[q].items() if g > 0 and d in env.doc_map.doc_to_mem} for q in qrels}
    for s in ("fixed_graph", "progressive", "soft"):
        per = {}
        for base in ("global_dense", "fixed_graph"):
            if base == s or (base == "fixed_graph" and s == "fixed_graph"):
                continue
            d = {q: pq[s][q]["ndcg_at_10"] - pq[base][q]["ndcg_at_10"] for q in qrels}
            lost = gained = 0
            for q in qrels:
                a, b = _top10(runs[base], q) & rel[q], _top10(runs[s], q) & rel[q]
                lost += len(a - b)
                gained += len(b - a)
            per[f"vs_{base}"] = {"queries_improved": sum(1 for v in d.values() if v > 1e-12), "queries_degraded": sum(1 for v in d.values() if v < -1e-12),
                                 "queries_unchanged": sum(1 for v in d.values() if abs(v) <= 1e-12), "relevant_top10_lost": lost, "relevant_top10_gained": gained}
        ex = [rows[s][q]["excluded_relevant"] for q in qrels]
        per["routing"] = {"relevant_documents_excluded_by_routing": int(sum(ex)), "queries_with_exclusion": sum(1 for e in ex if e > 0),
                          "queries_degraded_with_exclusion": sum(1 for q in qrels if rows[s][q]["excluded_relevant"] > 0 and
                                                                 pq[s][q]["ndcg_at_10"] < pq["global_dense"][q]["ndcg_at_10"] - 1e-12)}
        if s == "progressive":
            stops: dict[str, int] = {}
            stages: dict[int, int] = {}
            for q in qrels:
                stops[rows[s][q]["stop_reason"]] = stops.get(rows[s][q]["stop_reason"], 0) + 1
                stages[rows[s][q]["stages_run"]] = stages.get(rows[s][q]["stages_run"], 0) + 1
            per["progressive"] = {"stop_reasons": stops, "stages_run_distribution": {str(k): v for k, v in sorted(stages.items())},
                                  "queries_with_fallback": sum(1 for q in qrels if runs[s].latency[q]["fallback"]),
                                  "queries_where_expansion_never_changed_the_top_k": sum(1 for q in qrels if (rows[s][q]["stages_run"] or 1) > 1 and not rows[s][q]["useful_expansion_steps"]),
                                  "mean_useful_expansion_steps": float(np.mean([rows[s][q]["useful_expansion_steps"] or 0 for q in qrels]))}
        if s == "soft":
            per["soft"] = {"mean_promoted_in_top_k": float(np.mean([rows[s][q]["soft_promoted"] or 0 for q in qrels])),
                           "mean_demoted_in_top_k": float(np.mean([rows[s][q]["soft_demoted"] or 0 for q in qrels])),
                           "queries_with_any_reordering": sum(1 for q in qrels if (rows[s][q]["soft_promoted"] or 0) + (rows[s][q]["soft_demoted"] or 0) > 0)}
        out[s] = per
    return out


def baseline_context(dataset_id: str, artifacts_dir: str, n_test: int) -> dict[str, Any] | None:
    """BM25 and the frozen v2 numbers for the same test split, reported as context only (read from the immutable artifacts)."""
    d = Path(artifacts_dir) / dataset_id / "beir_full_v2_01"
    try:
        man = json.loads((d / "manifest.json").read_text())
        met = json.loads((d / "metrics.json").read_text())
    except Exception:
        return None
    ok = man.get("dataset", {}).get("test_group_queries") == n_test
    return {"source": f"{d.parent.name}/{d.name} (frozen v2)", "same_test_group_size": ok,
            "metrics": {k: {m: v[m] for m in METRIC_KEYS} for k, v in met["methods"].items()} if ok else None}


# --------------------------------------------------------------------------------------------------------------------- driver
def write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False), encoding="utf-8")


def run(cfg: BenchmarkConfig, base: Settings, embedder, datasets: list[str], out_root: Path, run_id: str, phase: str = "all", *, reps_val: int = 2,
        reps_test: int = 3, limit: int | None = None, skip_test: bool = False, progress=print) -> dict[str, Any]:
    run_dir = out_root / ROOT_NAME / run_id
    if phase in ("tune", "all") and run_dir.exists():
        raise FileExistsError(f"run id {run_id!r} already exists at {run_dir}; use a new --run-id (finished runs are never overwritten)")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        "run_id": run_id, "kind": "retrieval strategy comparison", "created_at": iso(), "datasets": datasets, "seed": cfg.seed, "query_limit": limit,
        "smoke": limit is not None, "strategies": list(STRATEGIES), "environment": environment_info(embedder),
        "architecture": "initial deterministic KMeans graph (retrieval strategy is the only variable; no adaptation)",
        "engine_settings": {**strategy_settings(cfg), "depth": DEPTH, "fallback_decision_depth": FALLBACK_K, "tie_safe_search": True}, "status": "running"}
    if phase in ("tune", "all"):
        tuning: dict[str, Any] = {}
        for ds in datasets:
            progress(f"[tune] {ds}: sweeping validation group")
            with dataset_env(cfg, base, embedder, ds, run_id) as env:
                tuning[ds] = tune_dataset(env, embedder, limit)
                manifest.setdefault("dataset_info", {})[ds] = {**env.info, "official_test_split": env.proto.official_test, "split_sizes": env.proto.to_dict()["sizes"],
                                                               "split_provenance": env.proto.provenance}
        sel = select_configs(tuning)
        fz = frozen_config(cfg, sel)
        write(run_dir / "tuning.json", {"sweeps": tuning, "selection": sel, "grids": {"progressive": PROGRESSIVE_GRID, "soft": SOFT_GRID}})
        write(run_dir / "frozen_config.json", fz)
        manifest["frozen_config_sha256"] = hashlib.sha256((run_dir / "frozen_config.json").read_bytes()).hexdigest()
        manifest["frozen_at"] = fz["frozen_at"]
        write(manifest_path, manifest)
        progress(f"[freeze] progressive={fz['progressive']['policy']}/patience {fz['progressive']['patience']}/step {fz['progressive']['step']}  "
                 f"soft weight={fz['soft']['weight']} share={fz['soft']['share']}")
    if phase in ("evaluate", "all"):
        fpath = run_dir / "frozen_config.json"
        if not fpath.exists():
            raise FileNotFoundError("no frozen_config.json: run the tune phase first; the test split is never evaluated before the configuration is frozen")
        if hashlib.sha256(fpath.read_bytes()).hexdigest() != manifest.get("frozen_config_sha256"):
            raise ValueError("frozen_config.json was modified after it was frozen; refusing to evaluate")
        fz = json.loads(fpath.read_text())
        for ds in datasets:
            with dataset_env(cfg, base, embedder, ds, run_id) as env:
                for group in (["validation"] + ([] if skip_test else ["test"])):
                    progress(f"[evaluate] {ds}: {group} group")
                    res = run_group(env, embedder, fz, group, reps_test if group == "test" else reps_val, limit)
                    res.update({"run_id": run_id, "dataset_id": ds, "frozen_config_sha256": manifest["frozen_config_sha256"],
                                "status": {"validation": "development (configuration was tuned on this group: optimistic)",
                                           "test": ("confirmatory (test split untouched by tuning)" if env.proto.official_test and ds != "scifact" else
                                                    "exploratory: SciFact test labels were seen under benchmark protocol v1" if ds == "scifact" else
                                                    "internal partition of the official queries, NOT an official test score")}[group],
                                "official_test_split": bool(env.proto.official_test) if group == "test" else None, "smoke": limit is not None})
                    if group == "test":
                        res["baseline_context"] = baseline_context(ds, cfg.artifacts_dir, res["queries"])
                    write(run_dir / ds / f"{group}.json", res)
                    manifest.setdefault("evaluated", {}).setdefault(ds, []).append(group)
        manifest.update({"status": "completed", "finished_at": iso()})
        write(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", default="scifact,nfcorpus,arguana")
    ap.add_argument("--run-id", required=True, help="unique id; an existing run id is refused")
    ap.add_argument("--phase", choices=["tune", "evaluate", "all"], default="all")
    ap.add_argument("--reps-val", type=int, default=2)
    ap.add_argument("--reps-test", type=int, default=3)
    ap.add_argument("--max-queries", type=int, default=None, help="SMOKE: cap every group (marks the run non-official)")
    ap.add_argument("--skip-test", action="store_true", help="tune + validation only (test labels untouched)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--mongo-backend", choices=["pymongo", "mongomock"], default=None)
    ap.add_argument("--embedding-backend", choices=["sentence_transformers", "hashing"], default=None)
    a = ap.parse_args(argv)
    over = {k: v for k, v in (("mongo_backend", a.mongo_backend), ("embedding_backend", a.embedding_backend)) if v}
    base = Settings(**over)
    cfg = BenchmarkConfig(data_dir=base.beir_data_dir, artifacts_dir=base.beir_artifacts_dir).resolve()
    out_root = Path(a.out) if a.out else Path(base.beir_artifacts_dir).parent
    embedder = build_embedder(base)
    man = run(cfg, base, embedder, [d.strip() for d in a.datasets.split(",") if d.strip()], out_root, a.run_id, a.phase, reps_val=a.reps_val,
              reps_test=a.reps_test, limit=a.max_queries, skip_test=a.skip_test, progress=lambda m: print(m, flush=True))
    print(f"done: {out_root / ROOT_NAME / a.run_id}  status={man['status']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
