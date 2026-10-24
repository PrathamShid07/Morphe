"""Query-level diagnostics: why does graph routing lose relevant documents compared with global dense retrieval?

Uses the VALIDATION group only (never the test group): its qrels are the legitimate evaluation labels for analysis and tuning.
Relevance labels are never derived from access patterns and never used at retrieval time; they are read here only to explain results.

Key structural fact the analysis relies on (and verifies per query): the graph path scores documents exactly like global search
(cosine, same vectors) but over a subset S of the corpus. Hence a relevant document that global search ranks in the top-10 and that lies
inside S MUST also be in the graph top-10; if it is missing the cause is outside S (routing/expansion) -- or a consistency error, which
is counted separately. Graph retrieval can still *beat* global search when excluding distractors lifts a relevant document into the top-10.

Failure taxonomy (primary category for a query whose graph nDCG@10 is lower than global):
  routing_miss    every lost relevant document sits in a module the seed/expansion policy could not reach from any seed
  expansion_miss  every lost relevant document sits in a module the graph could reach but the candidate cap / tier order dropped
  mixed_miss      both kinds among the lost documents
  ranking_miss    (non-exclusive flag) a relevant document is eligible yet absent from the top-10 of BOTH systems (embedding-model limit, not routing)
Non-exclusive flags: fallback_miss (loss, fallback did not run although global fallback would have recovered it), system_overhead (graph slower
than global without a quality gain), mapping_error (a judged document missing from the store, or a lost-but-eligible document).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..benchmark.beir_eval import per_query_metrics
from ..benchmark.corpus import DocMap, build_initial_architecture, embed_corpus, ingest_corpus, make_context, verify_mapping
from ..benchmark.methods import DEPTH, FALLBACK_K, embed_queries_timed, sandbox_engine
from ..benchmark.runner import BenchmarkConfig, environment_info, load_for_protocol
from ..benchmark.splits import build_split_protocol, group_qrels, group_queries
from ..common import iso
from ..config import Settings
from ..ingestion.embedder import build_embedder
from ..retrieval.graph_selector import expand_candidates

EPS = 1e-9
METRICS = ("ndcg_at_10", "recall_at_5", "recall_at_10", "recall_at_100", "mrr_at_10")
STAGES = ("seed", "expand", "search", "rank", "fallback")


def _ranks(hits: Sequence[Any]) -> dict[str, int]:
    return {h.memory_id: i + 1 for i, h in enumerate(hits)}


def _med(xs: Sequence[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def timed_retrieve(engine, qvec, mode: str, reps: int) -> tuple[Any, dict[str, float]]:
    """Warm-up, then the median of `reps` repetitions for the total and every stage (ms)."""
    engine.retrieve(qvec, DEPTH, mode, fallback_k=FALLBACK_K)
    tot, st, out = [], {k: [] for k in STAGES}, None
    for _ in range(max(1, reps)):
        t0 = time.perf_counter()
        out = engine.retrieve(qvec, DEPTH, mode, fallback_k=FALLBACK_K)
        tot.append((time.perf_counter() - t0) * 1000)
        for k in STAGES:
            st[k].append(out.timings.get(k, 0.0))
    return out, {"total_ms": _med(tot), **{f"{k}_ms": _med(v) for k, v in st.items()}}


def diagnose_queries(engine, state, doc_map: DocMap, queries: dict[str, str], qvecs: dict[str, np.ndarray],
                     qrels: dict[str, dict[str, int]], corpus: dict[str, dict[str, str]], *, reps: int = 3,
                     seed_count: int, max_candidates: int) -> dict[str, Any]:
    g = engine.graph_provider()
    mod_of = state.module_of()
    content = state.content_modules()
    mod_name = {m: state.modules[m].name for m in state.modules}
    members = {m: set(state.assignments[m]) for m in content}
    results: dict[str, dict[str, dict[str, float]]] = {"global": {}, "graph": {}, "guided_only": {}}
    raw: dict[str, dict[str, Any]] = {}
    for qid, text in queries.items():
        qv = qvecs[qid]
        rel_docs = {d: gr for d, gr in qrels[qid].items() if gr > 0}
        rel_mem = {doc_map.doc_to_mem[d]: gr for d, gr in rel_docs.items() if d in doc_map.doc_to_mem}
        gout, gt = timed_retrieve(engine, qv, "global", reps)
        hout, ht = timed_retrieve(engine, qv, "graph", reps)
        mod_rank = {h.module_id: i + 1 for i, h in enumerate(engine.store.search_modules(qv, len(content)))}
        mod_score = {h.module_id: h.score for h in engine.store.search_modules(qv, len(content))}
        full_order = [c.module_id for c in expand_candidates(hout.seeds, g, 10 ** 6)]  # everything the graph can reach from the seeds
        cand_origin = {c.module_id: c.origin for c in hout.candidates}
        seeds = {m for m, _ in hout.seeds}
        eligible = set().union(*(members[m] for m in cand_origin)) if cand_origin else set()
        glob_top, graph_top, guided_top = _ranks(gout.results[:10]), _ranks(hout.results[:10]), _ranks(hout.guided_results[:10])
        rel_info = []
        for mid in sorted(rel_mem):
            mod = mod_of.get(mid)
            in_store = mod is not None
            status = "missing_from_store" if not in_store else ("seed" if mod in seeds else "expanded" if mod in cand_origin else "excluded")
            rel_info.append({"memory_id": mid, "doc_id": doc_map.mem_to_doc[mid], "grade": rel_mem[mid], "module_id": mod,
                             "module_name": mod_name.get(mod), "module_status": status,
                             "module_profile_rank": mod_rank.get(mod), "module_profile_score": round(mod_score.get(mod, float("nan")), 4),
                             "graph_reachable_from_seeds": mod in full_order if mod else None,
                             "expansion_origin": cand_origin.get(mod),
                             "in_global_top10": mid in glob_top, "in_graph_top10": mid in graph_top, "in_guided_only_top10": mid in guided_top,
                             "global_rank": _ranks(gout.results).get(mid), "graph_rank": _ranks(hout.results).get(mid),
                             "title": (corpus[doc_map.mem_to_doc[mid]].get("title") or "")[:140]})
        results["global"][qid] = {doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in gout.results}
        results["graph"][qid] = {doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in hout.results}
        results["guided_only"][qid] = {doc_map.mem_to_doc[h.memory_id]: float(h.score) for h in hout.guided_results}
        raw[qid] = {"rel_info": rel_info, "seeds": hout.seeds, "cand_origin": cand_origin, "eligible": len(eligible),
                    "guided_space": hout.guided_candidate_count, "fallback": hout.fallback_used, "fallback_reason": hout.fallback_reason,
                    "gt": gt, "ht": ht, "candidates_incl_fallback": hout.candidate_count,
                    "module_count": len(content), "full_reach": len(full_order)}

    pq = {k: per_query_metrics({q: qrels[q] for q in queries}, v) for k, v in results.items()}
    rows, examples_pool = [], {}
    for qid, text in queries.items():
        r, info = raw[qid], raw[qid]["rel_info"]
        mg, mh, mo = pq["global"][qid], pq["graph"][qid], pq["guided_only"][qid]
        d_ndcg = mh["ndcg_at_10"] - mg["ndcg_at_10"]
        outcome = "graph_better" if d_ndcg > EPS else "graph_worse" if d_ndcg < -EPS else "equal"
        rel_n = len(info)
        located = [x for x in info if x["module_status"] != "missing_from_store"]
        cov = [x for x in located if x["module_status"] != "excluded"]
        lost = [x for x in info if x["in_global_top10"] and not x["in_graph_top10"]]
        gained = [x for x in info if x["in_graph_top10"] and not x["in_global_top10"]]
        lost_excluded = [x for x in lost if x["module_status"] == "excluded"]
        lost_eligible = [x for x in lost if x["module_status"] in ("seed", "expanded")]
        unreachable = [x for x in lost_excluded if not x["graph_reachable_from_seeds"]]
        reachable = [x for x in lost_excluded if x["graph_reachable_from_seeds"]]
        category = None
        if outcome == "graph_worse":
            category = ("routing_miss" if unreachable and not reachable else "expansion_miss" if reachable and not unreachable
                        else "mixed_miss" if reachable and unreachable else "unattributed")
        ranking_miss = [x for x in info if x["module_status"] in ("seed", "expanded") and not x["in_graph_top10"] and not x["in_global_top10"]]
        recovered = [x for x in info if r["fallback"] and x["in_graph_top10"] and not x["in_guided_only_top10"]]
        fallback_gain = mh["ndcg_at_10"] - mo["ndcg_at_10"]
        rows.append({
            "query_id": qid, "query": text[:300], "relevant_total": rel_n, "relevant_in_store": len(located),
            "outcome": outcome, "category": category,
            "coverage_before_ranking": (len(cov) / len(located)) if located else None,
            "relevant_in_seed_modules": sum(x["module_status"] == "seed" for x in located),
            "relevant_in_expanded_modules": sum(x["module_status"] == "expanded" for x in located),
            "relevant_excluded": sum(x["module_status"] == "excluded" for x in located),
            "relevant_eligible_not_in_graph_top10": sum(1 for x in cov if not x["in_graph_top10"]),
            "relevant_lost_vs_global_top10": len(lost), "relevant_lost_excluded": len(lost_excluded),
            "relevant_gained_vs_global_top10": len(gained),
            "relevant_recovered_by_fallback": len(recovered), "ndcg_gain_from_fallback": fallback_gain,
            "ranking_miss_docs": len(ranking_miss), "distinct_modules_of_relevant": len({x["module_id"] for x in located}),
            "seed_modules": [m for m, _ in r["seeds"]], "seed_scores": [round(s, 4) for _, s in r["seeds"]],
            "n_seed_modules": len(r["seeds"]), "n_expanded_modules": len(r["cand_origin"]) - len(r["seeds"]),
            "n_candidate_modules": len(r["cand_origin"]), "modules_reachable_from_seeds": r["full_reach"],
            "eligible_candidate_docs": r["guided_space"], "candidates_incl_fallback": r["candidates_incl_fallback"],
            "fallback_used": r["fallback"], "fallback_reason": r["fallback_reason"],
            "metrics_global": {k: mg[k] for k in METRICS}, "metrics_graph": {k: mh[k] for k in METRICS},
            "metrics_guided_only": {k: mo[k] for k in METRICS}, "metrics_delta": {k: mh[k] - mg[k] for k in METRICS},
            "timing_global": r["gt"], "timing_graph": r["ht"],
            "flags": {"fallback_miss": bool(lost_excluded) and not r["fallback"] and outcome == "graph_worse",
                      "system_overhead": r["ht"]["total_ms"] > r["gt"]["total_ms"] and d_ndcg <= EPS,
                      "ranking_miss": bool(ranking_miss),
                      "mapping_error": bool(len(located) < rel_n or lost_eligible)},
            "relevant_documents": info})
        if category:
            examples_pool.setdefault(category, []).append(rows[-1])
    return {"rows": rows, "pq": pq}


def aggregate(rows: list[dict[str, Any]], corpus_size: int) -> dict[str, Any]:
    n = len(rows)
    mean = lambda xs: float(np.mean(xs)) if len(xs) else None  # noqa: E731
    cats = Counter(r["category"] for r in rows if r["category"])
    outcomes = Counter(r["outcome"] for r in rows)
    worse = [r for r in rows if r["outcome"] == "graph_worse"]
    excluded_docs = [x for r in rows for x in r["relevant_documents"] if x["module_status"] == "excluded"]
    lost_docs = [x for r in rows for x in r["relevant_documents"] if x["in_global_top10"] and not x["in_graph_top10"]]
    cov = [r["coverage_before_ranking"] for r in rows if r["coverage_before_ranking"] is not None]
    ranks = [x["module_profile_rank"] for x in excluded_docs if x["module_profile_rank"]]
    mean_over = lambda k, sub=rows: mean([x[k] for x in sub])  # noqa: E731
    stage = {}
    for who, key in (("global", "timing_global"), ("graph", "timing_graph")):
        stage[who] = {k: mean([r[key][k] for r in rows]) for k in ("total_ms", *[f"{s}_ms" for s in STAGES])}
        stage[who]["total_p50_ms"] = _med([r[key]["total_ms"] for r in rows])
        stage[who]["total_p95_ms"] = float(np.percentile([r[key]["total_ms"] for r in rows], 95)) if rows else None
    return {
        "queries": n, "outcomes_vs_global_ndcg10": dict(outcomes),
        "failure_taxonomy": {"routing_miss": cats.get("routing_miss", 0), "expansion_miss": cats.get("expansion_miss", 0),
                             "mixed_miss": cats.get("mixed_miss", 0), "unattributed": cats.get("unattributed", 0),
                             "graph_worse_total": len(worse)},
        "flags": {"ranking_miss_queries": sum(r["flags"]["ranking_miss"] for r in rows),
                  "fallback_miss_queries": sum(r["flags"]["fallback_miss"] for r in rows),
                  "fallback_miss_share_of_graph_worse": (sum(r["flags"]["fallback_miss"] for r in rows) / len(worse)) if worse else None,
                  "system_overhead_queries": sum(r["flags"]["system_overhead"] for r in rows),
                  "mapping_or_consistency_error_queries": sum(r["flags"]["mapping_error"] for r in rows)},
        "coverage_before_ranking": {"mean_fraction_of_relevant_in_selected_modules": mean(cov),
                                    "queries_all_relevant_covered": sum(1 for c in cov if c == 1.0),
                                    "queries_partially_covered": sum(1 for c in cov if 0 < c < 1.0),
                                    "queries_no_relevant_covered": sum(1 for c in cov if c == 0.0),
                                    "relevant_docs_in_seed_modules": sum(r["relevant_in_seed_modules"] for r in rows),
                                    "relevant_docs_in_expanded_modules": sum(r["relevant_in_expanded_modules"] for r in rows),
                                    "relevant_docs_excluded": sum(r["relevant_excluded"] for r in rows),
                                    "relevant_docs_total_in_store": sum(r["relevant_in_store"] for r in rows)},
        "excluded_relevant_documents": {"count": len(excluded_docs),
                                        "module_profile_rank_median": _med(ranks) if ranks else None,
                                        "module_profile_rank_mean": mean(ranks),
                                        "share_rank_4_to_10": mean([4 <= x <= 10 for x in ranks]),
                                        "share_rank_over_10": mean([x > 10 for x in ranks]),
                                        "graph_reachable_from_seeds_share": mean([bool(x["graph_reachable_from_seeds"]) for x in excluded_docs])},
        "lost_vs_global_top10": {"relevant_docs_lost": len(lost_docs),
                                 "lost_in_excluded_modules": sum(x["module_status"] == "excluded" for x in lost_docs),
                                 "lost_although_eligible (must be 0)": sum(x["module_status"] in ("seed", "expanded") for x in lost_docs),
                                 "relevant_docs_gained": sum(r["relevant_gained_vs_global_top10"] for r in rows)},
        "fallback": {"queries_with_fallback": sum(r["fallback_used"] for r in rows),
                     "fallback_rate": mean([r["fallback_used"] for r in rows]),
                     "mean_ndcg_gain_when_fallback_ran": mean([r["ndcg_gain_from_fallback"] for r in rows if r["fallback_used"]]),
                     "queries_where_fallback_improved_ndcg": sum(1 for r in rows if r["fallback_used"] and r["ndcg_gain_from_fallback"] > EPS),
                     "queries_where_fallback_changed_nothing": sum(1 for r in rows if r["fallback_used"] and abs(r["ndcg_gain_from_fallback"]) <= EPS),
                     "relevant_docs_recovered_by_fallback": sum(r["relevant_recovered_by_fallback"] for r in rows)},
        "modules": {"mean_seed_modules": mean_over("n_seed_modules"), "mean_expanded_modules": mean_over("n_expanded_modules"),
                    "mean_candidate_modules": mean_over("n_candidate_modules"), "mean_modules_reachable_from_seeds": mean_over("modules_reachable_from_seeds"),
                    "mean_distinct_modules_holding_relevant_docs": mean_over("distinct_modules_of_relevant"),
                    "mean_eligible_docs": mean_over("eligible_candidate_docs"),
                    "mean_eligible_share_of_corpus": (mean_over("eligible_candidate_docs") or 0) / corpus_size},
        "metrics_mean": {who: {k: mean([r[f"metrics_{who}"][k] for r in rows]) for k in METRICS} for who in ("global", "graph", "guided_only")},
        "metrics_delta_graph_minus_global_mean": {k: mean([r["metrics_delta"][k] for r in rows]) for k in METRICS},
        "coverage_vs_probe_depth": _probe_curve(rows, corpus_size),
        "stage_timing_ms": stage, "timing_note": "median of repeated runs per query, then mean over queries; global/graph measured back to back on the same engine",
    }


def _probe_curve(rows: list[dict[str, Any]], corpus_size: int) -> dict[str, Any]:
    """DIAGNOSTIC ESTIMATE (not a measured retrieval result): if the top-n modules by profile similarity alone were searched -- ignoring graph
    expansion -- what share of the relevant documents would be reachable, and at what share of the corpus? Uses validation qrels for analysis only."""
    docs = [x for r in rows for x in r["relevant_documents"] if x["module_profile_rank"]]
    per_q = [[x["module_profile_rank"] for x in r["relevant_documents"] if x["module_profile_rank"]] for r in rows]
    per_q = [p for p in per_q if p]
    sizes = [r["eligible_candidate_docs"] / max(1, r["n_candidate_modules"]) for r in rows]
    avg_module = float(np.mean(sizes)) if sizes else 0.0
    curve = []
    for n in (1, 2, 3, 5, 8, 12, 16, 24, 36):
        curve.append({"probe_modules": n, "share_of_relevant_docs_reachable": float(np.mean([x["module_profile_rank"] <= n for x in docs])) if docs else None,
                      "share_of_queries_with_all_relevant_reachable": float(np.mean([max(p) <= n for p in per_q])) if per_q else None,
                      "share_of_queries_with_any_relevant_reachable": float(np.mean([min(p) <= n for p in per_q])) if per_q else None,
                      "approx_share_of_corpus_searched": min(1.0, n * avg_module / corpus_size)})
    return {"kind": "diagnostic estimate from module-profile ranks only (no hierarchy expansion, no fallback)", "curve": curve}


def pick_examples(rows: list[dict[str, Any]], per_category: int = 3) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for cat in ("routing_miss", "expansion_miss", "mixed_miss"):
        sub = sorted((r for r in rows if r["category"] == cat), key=lambda r: (r["metrics_delta"]["ndcg_at_10"], r["query_id"]))[:per_category]
        out[cat] = [_example(r) for r in sub]
    out["ranking_miss"] = [_example(r) for r in sorted((r for r in rows if r["flags"]["ranking_miss"]), key=lambda r: r["query_id"])[:per_category]]
    out["graph_better"] = [_example(r) for r in sorted((r for r in rows if r["outcome"] == "graph_better"),
                                                       key=lambda r: (-r["metrics_delta"]["ndcg_at_10"], r["query_id"]))[:per_category]]
    out["system_overhead"] = [_example(r) for r in sorted((r for r in rows if r["flags"]["system_overhead"]), key=lambda r: r["query_id"])[:2]]
    return out


def _example(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("query_id", "query", "outcome", "category", "coverage_before_ranking", "seed_modules", "seed_scores",
                              "n_candidate_modules", "eligible_candidate_docs", "fallback_used", "metrics_global", "metrics_graph", "timing_global",
                              "timing_graph")} | {"relevant_documents": [{k: x[k] for k in ("doc_id", "title", "grade", "module_name", "module_status",
                                                                                      "module_profile_rank", "graph_reachable_from_seeds",
                                                                                      "global_rank", "graph_rank")} for x in r["relevant_documents"]]}


def run_dataset_diagnostics(cfg: BenchmarkConfig, base: Settings, embedder, dataset_id: str, out_root: Path, run_id: str, reps: int = 3,
                            max_queries: int | None = None) -> dict[str, Any]:
    data = load_for_protocol(dataset_id, cfg)
    proto = build_split_protocol(data, cfg.seed, cfg.query_limit)
    queries_all = group_queries(data, proto, "validation")
    qrels_all = group_qrels(data, proto, "validation")
    ids, mat, emb_info = embed_corpus(embedder, dataset_id, data.corpus, data.source["corpus_sha256"], str(Path(cfg.data_dir) / "embeddings"))
    doc_map = DocMap.build(dataset_id, ids)
    vectors = {doc_map.doc_to_mem[d]: mat[i] for i, d in enumerate(ids)}
    state0, ginfo = build_initial_architecture(dataset_id, doc_map, ids, mat, data.corpus, cfg.graph)
    capacity = int(cfg.capacity_factor * ginfo["module_size"]["median"])
    ctx = make_context(base, embedder, dataset_id, run_id, seed_module_count=cfg.seed_module_count, max_candidate_modules=cfg.max_candidate_modules,
                       fallback_min_score=cfg.fallback_min_score, split_capacity_threshold=capacity)
    try:
        ingest_corpus(ctx, dataset_id, data.corpus, ids, mat, doc_map, state0)
        verify_mapping(ctx, doc_map)
        state = ctx.persistence.export_live_state()
        engine = sandbox_engine(ctx, state, vectors)
        keep = [q for q in sorted(queries_all) if any(g > 0 and d in doc_map.doc_to_mem for d, g in qrels_all[q].items())]
        skipped = len(queries_all) - len(keep)
        keep = keep[: max_queries or None]
        queries = {q: queries_all[q] for q in keep}
        qvecs, _ = embed_queries_timed(embedder, queries)
        res = diagnose_queries(engine, state, doc_map, queries, qvecs, {q: qrels_all[q] for q in keep}, data.corpus, reps=reps,
                               seed_count=cfg.seed_module_count, max_candidates=cfg.max_candidate_modules)
        rows = res["rows"]
        out = {"run_id": run_id, "dataset_id": dataset_id, "created_at": iso(), "kind": "diagnostic (analysis of recorded retrieval behaviour; not a benchmark result)",
               "group": "validation", "labels": "official qrels of the validation group only; test group untouched", "official_test_split_used": False,
               "architecture": "initial deterministic KMeans graph (fixed-graph baseline)",
               "engine_settings": {"seed_module_count": cfg.seed_module_count, "max_candidate_modules": cfg.max_candidate_modules,
                                   "fallback_min_score": cfg.fallback_min_score, "fallback_decision_depth": FALLBACK_K, "depth": DEPTH,
                                   "per_module_candidates": base.per_module_candidates, "tie_safe_search": True},
               "queries": {"validation_group": len(queries_all), "diagnosed": len(rows), "skipped_no_retrievable_relevant_doc": skipped},
               "corpus_documents": len(ids), "modules": ginfo["modules"], "module_size": ginfo["module_size"],
               "aggregate": aggregate(rows, len(ids)), "examples": pick_examples(rows), "environment": environment_info(embedder),
               "embedding": {k: emb_info[k] for k in ("model", "dimension", "preprocess", "corpus_sha256")}, "rows": rows}
        bad = out["aggregate"]["flags"]["mapping_or_consistency_error_queries"]
        out["valid"] = bad == 0
        if bad:  # the evaluated state does not match the labels/corpus: the numbers explain nothing, so never present them as a result
            out["invalid_reason"] = f"{bad} queries have judged documents missing from the evaluated architecture state or lost although eligible"
        d = out_root / run_id / dataset_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "routing_diagnostics.json").write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False))
        return out
    finally:
        try:
            ctx.mongo.client.drop_database(ctx.mongo.db.name)
        except Exception:
            pass
        ctx.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Query-level routing failure diagnostics on the validation group")
    ap.add_argument("--datasets", default="scifact,nfcorpus,arguana")
    ap.add_argument("--run-id", default=time.strftime("diag_%Y%m%dT%H%M%S"))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--max-queries", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    base = Settings()
    cfg = BenchmarkConfig(data_dir=base.beir_data_dir, artifacts_dir=base.beir_artifacts_dir).resolve()
    out_root = Path(a.out) if a.out else Path(base.beir_artifacts_dir).parent / "beir_phase2"
    embedder = build_embedder(base)
    failures = 0
    for ds in [d.strip() for d in a.datasets.split(",") if d.strip()]:
        r = run_dataset_diagnostics(cfg, base, embedder, ds, out_root, a.run_id, a.reps, a.max_queries)
        ag = r["aggregate"]
        if not r["valid"]:
            print(f"!! {ds}: INVALID RUN: {r['invalid_reason']}", flush=True)
            failures += 1
        print(f"{ds}: {ag['queries']} validation queries | outcome {ag['outcomes_vs_global_ndcg10']} | taxonomy {ag['failure_taxonomy']} | "
              f"coverage {ag['coverage_before_ranking']['mean_fraction_of_relevant_in_selected_modules']:.3f} | consistency errors "
              f"{ag['flags']['mapping_or_consistency_error_queries']}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
