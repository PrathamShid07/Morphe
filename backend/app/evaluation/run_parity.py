"""CLI: live-versus-sandbox parity investigation on real BEIR data (no adaptation; the initial deterministic architecture).

    python -m app.evaluation.run_parity --datasets nfcorpus,arguana --run-id parity_01

Writes `<artifacts>/beir_phase2/<run_id>/<dataset>/parity.json` (machine readable, one row per query). Never touches the
immutable v1/v2 baseline artifacts. Requires MongoDB (a throw-away database is created and dropped).
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..benchmark.corpus import DocMap, build_initial_architecture, embed_corpus, ingest_corpus, make_context, verify_mapping
from ..benchmark.methods import DEPTH, FALLBACK_K, embed_queries_timed, sandbox_engine
from ..benchmark.runner import BenchmarkConfig, environment_info, load_for_protocol
from ..benchmark.splits import build_split_protocol, group_queries
from ..common import iso
from ..config import Settings
from ..ingestion.embedder import build_embedder
from . import parity as P


def analyse_dataset(cfg: BenchmarkConfig, base: Settings, embedder, dataset_id: str, out_root: Path, run_id: str,
                    max_queries: int | None = None) -> dict[str, Any]:
    data = load_for_protocol(dataset_id, cfg)
    proto = build_split_protocol(data, cfg.seed, cfg.query_limit)
    queries = group_queries(data, proto, "test")  # test QUERIES only; no relevance labels are used in this module
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
        live_state = ctx.persistence.export_live_state()
        sandbox = sandbox_engine(ctx, live_state, vectors)
        live = ctx.retrieval.engine
        qids = sorted(queries)[: max_queries or None]
        qvecs, _ = embed_queries_timed(embedder, {q: queries[q] for q in qids})
        id_order = [doc_map.doc_to_mem[d] for d in ids]

        rows: list[dict[str, Any]] = []
        harness_mismatch_first40, harness_mismatch_all = 0, 0
        for n, q in enumerate(qids):
            lo = live.retrieve(qvecs[q], DEPTH, "graph", fallback_k=FALLBACK_K)
            so = sandbox.retrieve(qvecs[q], DEPTH, "graph", fallback_k=FALLBACK_K)
            a, b = P.stages_of(lo), P.stages_of(so)
            div = P.first_divergence(a, b, 10)
            row: dict[str, Any] = {"query_id": q, "in_original_sample_of_40": n < 40, "diverges_at": div, "cause": "identical",
                                   "fallback_live": lo.fallback_used, "fallback_sandbox": so.fallback_used}
            oracle_all = P.numpy_oracle(mat, qvecs[q], id_order, 101)
            row.update(P.cutoff_ties(oracle_all))
            # effect of the tie-safe search on this query: top-100 membership with it on (default) vs the legacy plain search
            for eng in (live,):
                eng.store.tie_safe = False
                legacy = eng.retrieve(qvecs[q], DEPTH, "graph", fallback_k=FALLBACK_K)
                eng.store.tie_safe = True
            row["top100_membership_changed_by_tie_safe_search"] = {h.memory_id for h in legacy.results} != {m for m, _ in a["final_ranking"]}
            # is the vector search itself exact? live GLOBAL mode vs float64 numpy over the stored matrix (tie-aware)
            go = live.retrieve(qvecs[q], 100, "global")
            g_scores = [h.score for h in go.results[:10]]
            row["global_top10_scores_match_exact_oracle"] = all(abs(x - y) <= 1e-6 for x, y in zip(g_scores, [s_ for _, s_ in oracle_all[:10]]))
            row["global_top10_ids_equal_oracle"] = [h.memory_id for h in go.results[:10]] == [m for m, _ in oracle_all[:10]]
            if div == "final_ranking" or div == "guided_ranking":
                cls = P.classify_ranking_difference(a["final_ranking"] if div == "final_ranking" else a["guided_ranking"],
                                                    b["final_ranking"] if div == "final_ranking" else b["guided_ranking"], 10)
                row.update(cls)
                row["cause"] = cls["cause"]
            elif div:
                row["cause"] = "structural"
            # original harness logic: live top-10 (memory-id tie-break) vs sandbox sorted by (-score, BEIR doc id)
            live_docs = [doc_map.mem_to_doc[m] for m, _ in a["final_ranking"]]
            sand_scores = {doc_map.mem_to_doc[m]: s for m, s in b["final_ranking"]}
            sand_docs = P.harness_style_ranking(sand_scores)
            row["harness_check_identical_top10"] = live_docs[:10] == sand_docs[:10]
            if not row["harness_check_identical_top10"]:
                harness_mismatch_all += 1
                harness_mismatch_first40 += int(n < 40)
                row["harness_live_top10"] = live_docs[:10]
                row["harness_sandbox_top10"] = sand_docs[:10]
                row["harness_scores_at_divergence"] = {"live": [round(s, 9) for _, s in a["final_ranking"][:10]],
                                                       "sandbox": [round(s, 9) for _, s in b["final_ranking"][:10]]}
            rows.append(row)

        report = {
            "run_id": run_id, "dataset_id": dataset_id, "created_at": iso(), "architecture": "initial deterministic KMeans graph (no adaptation)",
            "queries_checked": len(qids), "depth": DEPTH, "fallback_k": FALLBACK_K, "tie_safe_search": True,
            "queries_whose_top100_membership_changes_with_tie_safe_search": sum(1 for r in rows if r["top100_membership_changed_by_tie_safe_search"]),
            "engine_vs_engine": P.summarise(rows),
            "original_harness_logic": {"mismatch_in_first_40_sorted_query_ids": harness_mismatch_first40,
                                       "mismatch_over_all_queries_checked": harness_mismatch_all,
                                       "comparison": "live ranked by (-score, memory_id) vs sandbox re-sorted by (-score, BEIR doc id), top-10 list equality"},
            "vector_ties": P.tie_groups(mat, ids),
            "qdrant_config_live": P.qdrant_config(ctx.qdrant),
            "profile_vectors": P.profile_vector_diff(ctx.qdrant, live_state),
            "assignments": _assignment_check(ctx, live_state, doc_map),
            "graph": P.graph_diff(live.graph_provider(), sandbox.graph_provider()),
            "embedding": {"model": emb_info["model"], "dimension": emb_info["dimension"], "preprocess": emb_info["preprocess"],
                          "corpus_sha256": emb_info["corpus_sha256"], "same_query_vectors_for_both_engines": True},
            "environment": environment_info(embedder), "rows": rows}
        d = out_root / run_id / dataset_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "parity.json").write_text(json.dumps(report, indent=1, default=str))
        return report
    finally:
        try:
            ctx.mongo.client.drop_database(ctx.mongo.db.name)
        except Exception:
            pass
        ctx.close()


def _assignment_check(ctx, state, doc_map) -> dict[str, Any]:
    """Live Qdrant payloads (module_id, status, beir_doc_id) versus the architecture state the sandbox is built from."""
    want = state.module_of()
    seen, bad_module, bad_doc, inactive = 0, 0, 0, 0
    for mid, payload, _ in ctx.qdrant.scroll_memories(status=None):
        seen += 1
        bad_module += int(want.get(mid) != payload.get("module_id"))
        bad_doc += int(doc_map.mem_to_doc.get(mid) != payload.get("beir_doc_id"))
        inactive += int(payload.get("status") != "active")
    return {"live_points": seen, "state_memories": len(want), "module_id_mismatches": bad_module, "beir_doc_id_mismatches": bad_doc,
            "non_active_points": inactive, "counts_equal": seen == len(want) == len(doc_map.doc_to_mem)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", default="nfcorpus,arguana")
    ap.add_argument("--run-id", default=time.strftime("parity_%Y%m%dT%H%M%S"))
    ap.add_argument("--max-queries", type=int, default=None)
    ap.add_argument("--out", default=None, help="artifacts root (default: <beir_artifacts_dir>/../beir_phase2)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    base = Settings()
    cfg = BenchmarkConfig(data_dir=base.beir_data_dir, artifacts_dir=base.beir_artifacts_dir).resolve()
    out_root = Path(a.out) if a.out else Path(base.beir_artifacts_dir).parent / "beir_phase2"
    embedder = build_embedder(base)
    for ds in [d.strip() for d in a.datasets.split(",") if d.strip()]:
        r = analyse_dataset(cfg, base, embedder, ds, out_root, a.run_id, a.max_queries)
        s = r["engine_vs_engine"]
        print(f"{ds}: {s['queries']} queries | engine-vs-engine divergent={s['divergent']} {s['first_divergence_stage_counts']} causes={s['cause_counts']} | "
              f"original-harness logic mismatches first40={r['original_harness_logic']['mismatch_in_first_40_sorted_query_ids']} "
              f"all={r['original_harness_logic']['mismatch_over_all_queries_checked']} | ties={r['vector_ties']['groups_of_identical_vectors']} groups", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
