"""End-to-end BEIR experiment for one dataset, built on MORPHE's real services.

Procedure (see docs/BEIR_BENCHMARK_METHODOLOGY.md): load official BEIR files → leakage-free query groups → cached embeddings →
deterministic initial graph (content only) → isolated MORPHE context → workload windows through the real adaptation engine,
validated on the validation group → freeze → evaluate every method once on the test group → statistics → safety probes → artifacts.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

import numpy as np
import psutil

from ..common import iso, new_id
from ..evaluation.dataset import EvalQuery
from ..versioning.state import ArchitectureState, centroid
from .bm25 import BM25Index
from .beir_eval import (HEADLINE, METRIC_KEYS, consistency_gap, evaluate_beir, mean_of, paired_bootstrap,
                        per_query_metrics)
from .corpus import (DocMap, GraphInitConfig, build_initial_architecture, embed_corpus, ingest_corpus, make_context,
                     verify_mapping)
from .datasets import BeirData, load_beir, split_files
from .methods import (DEPTH, MethodRun, embed_queries_timed, run_bm25, run_dense, sandbox_engine, summarize_latency)
from .splits import SplitProtocol, build_split_protocol, group_qrels, group_queries

ALL_METHODS = ("bm25", "global_dense", "fixed_graph", "adaptive_graph")
log = logging.getLogger("morphe.benchmark")


@dataclass
class BenchmarkConfig:
    datasets: list[str] = field(default_factory=lambda: ["scifact"])
    methods: list[str] = field(default_factory=lambda: list(ALL_METHODS))
    split: str = "test"
    query_limit: int | None = None
    seed: int = 13
    download: bool = False
    force_embeddings: bool = False
    graph: GraphInitConfig = field(default_factory=GraphInitConfig)
    # --- engine configuration used for the graph methods (frozen before any test evaluation) ---
    seed_module_count: int = 3
    max_candidate_modules: int = 8
    fallback_min_score: float = 0.30
    # --- adaptation protocol ---
    adaptation_query_batch: int = 50
    max_windows: int = 6
    capacity_factor: float = 1.5  # split_capacity_threshold = capacity_factor x median initial module size
    protocol: str = "v2"  # v1 = first pre-registered protocol (kept reproducible); v2 = noise-aware revision, see the plan
    validation_repetitions: int | None = None  # default: v1 -> 1, v2 -> 3
    validation_cap: int = 0  # candidate evaluation uses at most this many validation queries (seeded subset); default v1 200, v2 150
    max_proposals_per_window: int = 0  # default v1 9, v2 6
    routing_sweep: bool = True  # validation-only sensitivity of the fixed graph to (seed modules, candidate cap)
    # --- measurement / statistics ---
    latency_repetitions: int = 3
    depth: int = 100  # ranked-list depth returned by every method (>= 100 for Recall@100)
    bootstrap_resamples: int = 1000
    safety_probe: bool = True
    keep_db: bool = False
    data_dir: str = ""
    artifacts_dir: str = ""

    def resolve(self) -> "BenchmarkConfig":
        v2 = self.protocol == "v2"
        if self.validation_repetitions is None:
            self.validation_repetitions = 3 if v2 else 1
        if not self.validation_cap:
            self.validation_cap = 150 if v2 else 200
        if not self.max_proposals_per_window:
            self.max_proposals_per_window = 6 if v2 else 9
        if v2 and self.max_windows == 6:
            self.max_windows = 5
        return self

    def protocol_flags(self) -> dict[str, Any]:
        v2 = self.protocol == "v2"
        return {"protocol": self.protocol, "eval_interleaved": v2, "benefit_requires_ci": v2, "cumulative_guard": v2}

    def protocol_flags_settings(self) -> dict[str, Any]:
        f = self.protocol_flags()
        f.pop("protocol")
        return f

    def validate(self) -> None:
        if self.protocol not in ("v1", "v2"):
            raise ValueError("protocol must be 'v1' or 'v2'")
        bad = [m for m in self.methods if m not in ALL_METHODS]
        if bad:
            raise ValueError(f"unknown methods {bad}; choose from {ALL_METHODS}")
        if self.split != "test":
            raise ValueError("final evaluation always uses the frozen 'test' group (split must be 'test')")
        if "adaptive_graph" in self.methods and "fixed_graph" not in self.methods:
            raise ValueError("adaptive_graph is defined relative to fixed_graph; include both")
        if self.query_limit is not None and self.query_limit < 1:
            raise ValueError("query_limit must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Progress:
    """Structured JSONL log + console line; also used by the API job manager for live status."""

    def __init__(self, path: Path | None, sink: Callable[[dict[str, Any]], None] | None = None):
        self.path, self.sink = path, sink
        self.start = time.perf_counter()

    def __call__(self, phase: str, message: str, **fields: Any) -> None:
        rec = {"t": iso(), "elapsed_s": round(time.perf_counter() - self.start, 1), "phase": phase, "message": message, **fields}
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        if self.sink:
            self.sink(rec)
        log.info("[%s] %s", phase, message)


def environment_info(embedder) -> dict[str, Any]:
    def ver(n: str) -> str | None:
        try:
            return metadata.version(n)
        except metadata.PackageNotFoundError:
            return None
    device = None
    try:
        import torch
        device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        pass
    return {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
            "cpu_count": os.cpu_count(), "memory_gb": round(psutil.virtual_memory().total / 2**30, 1), "torch_device": device,
            "packages": {n: ver(n) for n in ("beir", "pytrec-eval-terrier", "sentence-transformers", "torch", "transformers",
                                              "qdrant-client", "numpy", "scikit-learn", "networkx", "scipy", "pymongo", "fastapi")},
            "embedding_model": getattr(embedder, "model_name", None), "embedding_dimension": getattr(embedder, "dimension", None),
            "embedding_max_seq_length": getattr(getattr(embedder, "inner", embedder), "max_seq_length", None)}


def load_for_protocol(dataset_id: str, cfg: BenchmarkConfig) -> BeirData:
    from .datasets import dataset_path, is_cached
    if not is_cached(cfg.data_dir, dataset_id):
        load_beir(dataset_id, cfg.data_dir, ("test",), download=cfg.download)  # downloads or raises DatasetNotAvailable
    avail = [s for s in split_files(dataset_path(cfg.data_dir, dataset_id)) if s in ("train", "dev", "test")]
    return load_beir(dataset_id, cfg.data_dir, tuple(avail), download=False)


def _rss_mb() -> float:
    return round(psutil.Process().memory_info().rss / 2**20, 1)


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str, ensure_ascii=False), encoding="utf-8")


def validation_queries(data: BeirData, proto: SplitProtocol, doc_map: DocMap, cap: int = 0,
                       seed: int = 13) -> tuple[list[EvalQuery], dict[str, int]]:
    qs = group_queries(data, proto, "validation")
    qr = group_qrels(data, proto, "validation")
    out, skipped = [], 0
    for qid, text in qs.items():
        grades = {doc_map.doc_to_mem[d]: g for d, g in qr[qid].items() if g > 0 and d in doc_map.doc_to_mem}
        if not grades:
            skipped += 1  # all judged documents are missing from the corpus (cannot be retrieved)
            continue
        out.append(EvalQuery(query_id=qid, query=text, relevant_memory_ids=sorted(grades), split="dev", tags=["beir-validation"],
                             grades=grades))
    info = {"skipped_no_retrievable_relevant": skipped, "available": len(out)}
    if cap and len(out) > cap:
        rng = random.Random(f"{seed}:validation-cap")
        out = sorted(rng.sample(sorted(out, key=lambda q: q.query_id), cap), key=lambda q: q.query_id)
        info["capped_to"] = cap
    return out, info


# --------------------------------------------------------------------------------------------- adaptation
def run_adaptation(ctx, workload: dict[str, str], cfg: BenchmarkConfig, progress: Progress) -> dict[str, Any]:
    """Replay workload windows through the *live* retrieval path (tracked) and let the real adaptation engine act."""
    texts = list(workload.values())
    batch = cfg.adaptation_query_batch
    windows = min(cfg.max_windows, len(texts) // batch)
    ctx.embedder.encode(texts)  # warm the query-embedding cache
    summaries = []
    for w in range(windows):
        chunk = texts[w * batch:(w + 1) * batch]
        ctx.tracker.reset_window()
        for t in chunk:
            ctx.retrieval.query(t, 10, False)
        before = ctx.versions.active_id()
        t0 = time.perf_counter()
        s = ctx.adaptation.run_window("benchmark_window")
        evs = [ctx.events.get_adaptation(e["event_id"]) for e in s["evaluated"]]
        progress("adaptation", f"window {w + 1}/{windows}: {s['proposals_detected']} proposals, {s['applied']} applied "
                 f"({time.perf_counter() - t0:.0f}s)", window=w + 1, proposals=s["proposals_detected"], applied=s["applied"])
        summaries.append({"window": w + 1, "queries": len(chunk), "proposals_detected": s["proposals_detected"],
                          "applied": s["applied"], "events": [{"event_id": e["event_id"], "operation": e["operation"],
                                                               "status": e["status"]} for e in evs if e],
                          "version_before": before, "version_after": ctx.versions.active_id(),
                          "monitoring": (s.get("monitoring") or {}).get("status")})
    return {"windows_planned": windows, "workload_queries": len(texts), "windows": summaries}


def collect_adaptation_events(ctx) -> list[dict[str, Any]]:
    events, _ = ctx.mongo.list_events("adaptation_events", "started_at", limit=500)
    keep = ("event_id", "operation", "trigger", "reason", "status", "affected_modules", "affected_record_count", "decision",
            "decision_reason", "rejection_stage", "structural_summary", "created_module_ids", "removed_module_ids",
            "before_version", "new_version", "rolled_back_to", "integrity_check", "post_commit_verification", "started_at",
            "duration_ms", "error", "recovered")
    out = []
    for e in reversed(events):  # chronological
        row = {k: e.get(k) for k in keep}
        ev = e.get("evaluation")
        if isinstance(ev, dict) and "baseline" in ev:
            row["baseline_metrics"] = ev["baseline"]
            row["candidate_metrics"] = ev["candidate"]
            row["checks"] = [{k: c.get(k) for k in ("name", "passed", "detail")} for c in ev["decision"]["checks"]]
            row["benefit"] = next((c["detail"] for c in ev["decision"]["checks"] if c["name"] == "meaningful_benefit"), None)
        out.append(row)
    return out


def routing_sweep(ctx, state: ArchitectureState, progress: Progress) -> dict[str, Any]:
    """Validation-group sensitivity of the FIXED graph to the routing breadth. Informational: the primary comparison keeps the
    MORPHE default routing (3 seeds, 8 candidate modules) that was fixed before any evaluation; nothing here selects a config."""
    s = ctx.settings
    saved = (s.seed_module_count, s.max_candidate_modules)
    ectx = ctx.evaluator.build_context()
    rows = []
    try:
        g = ctx.evaluator.evaluate_state(state, ectx, "global", "global")
        rows.append({"config": "global dense (no routing)", "seed_modules": None, "max_candidate_modules": None, **_sweep_row(g)})
        for seeds, cap in ((3, 8), (5, 12), (8, 20), (12, 32)):
            s.seed_module_count, s.max_candidate_modules = seeds, cap
            r = ctx.evaluator.evaluate_state(state, ectx, f"graph-{seeds}-{cap}", "graph")
            rows.append({"config": f"graph seeds={seeds} cap={cap}", "seed_modules": seeds, "max_candidate_modules": cap, **_sweep_row(r)})
            progress("sweep", f"seeds={seeds} cap={cap}: nDCG@10 {rows[-1]['ndcg_at_10']:.4f}, mean candidates {rows[-1]['mean_candidates']:.0f}")
    finally:
        s.seed_module_count, s.max_candidate_modules = saved
    return {"group": "validation", "queries": len(ectx.queries), "note": "informational; the primary results use the default routing", "rows": rows}


def _sweep_row(rep: dict[str, Any]) -> dict[str, Any]:
    m = rep["metrics"]
    return {"ndcg_at_10": m["ndcg_at_10"], "recall_at_5": m["recall_at_5"], "mrr_at_10": m["mrr_at_10"], "mean_candidates": m["mean_candidate_count"],
            "p95_latency_ms": m["p95_latency_ms"], "fallback_rate": m["fallback_rate"]}


# --------------------------------------------------------------------------------------------- safety probes
def _scramble(state: ArchitectureState, seed: int) -> ArchitectureState:
    """Deliberately harmful candidate: documents shuffled across modules, routing profiles left stale."""
    rng = random.Random(seed)
    bad = state.copy()
    mods = state.content_modules()
    ids = sorted(state.module_of())
    bad.assignments = {m: [] for m in mods}
    for mid in ids:
        bad.assignments[rng.choice(mods)].append(mid)
    for m in mods:
        bad.assignments[m].sort()
    return bad


def safety_probe(ctx, workload: dict[str, str], cfg: BenchmarkConfig, progress: Progress) -> dict[str, Any]:
    """Q5 evidence on the real benchmark corpus. Probe-only settings are applied to this context copy and restored."""
    out: dict[str, Any] = {"note": "Probes run after the test evaluation on the benchmark context; they use an injected harmful "
                                   "candidate / injected fault and relaxed gating (coaccess thresholds, benefit requirement) "
                                   "ONLY to make a candidate available. Validation, acceptance, commit and recovery are the real code."}
    s = ctx.settings
    saved = (s.coaccess_min_count, s.coaccess_min_window_queries, s.require_benefit, s.max_edges_total, s.max_coaccess_per_node,
             s.cumulative_guard)
    try:
        s.cumulative_guard = False  # drift guard concerns accumulated change; probes only need a *valid* candidate
        s.coaccess_min_count, s.coaccess_min_window_queries, s.max_edges_total, s.max_coaccess_per_node = 1, 1, 10_000, 10_000
        texts = list(workload.values())[: cfg.adaptation_query_batch]
        ctx.tracker.reset_window()
        for t in texts:
            ctx.retrieval.query(t, 10, False)
        window = ctx.tracker.window()
        linked = {frozenset((e.source, e.target)) for e in ctx.persistence.export_live_state().edges.values()}
        pairs = [tuple(k.split("|")) for k, _ in sorted(window.get("pair_counts", {}).items(), key=lambda kv: -kv[1])
                 if frozenset(k.split("|")) not in linked]
        out["candidate_pairs_available"] = len(pairs)
        if not pairs:
            out["inconclusive"] = "no co-accessed module pair available to build a probe candidate"
            return out
        before_ck = ctx.persistence.export_live_state().checksum()
        before_v = ctx.versions.active_id()
        # (1) harmful candidate must be rejected before commit and leave everything untouched
        ev = ctx.adaptation.process("add_shortcut_edge", list(pairs[0]), apply=True, trigger="safety_probe",
                                    plan_transform=lambda st: _scramble(st, cfg.seed), window=window)
        out["harmful_candidate"] = {"status": ev["status"], "rejection_stage": ev.get("rejection_stage"),
                                    "decision_reason": ev.get("decision_reason"),
                                    "state_unchanged": ctx.persistence.export_live_state().checksum() == before_ck,
                                    "version_unchanged": ctx.versions.active_id() == before_v,
                                    "baseline_ndcg_at_10": (ev.get("evaluation") or {}).get("baseline", {}).get("ndcg_at_10"),
                                    "candidate_ndcg_at_10": (ev.get("evaluation") or {}).get("candidate", {}).get("ndcg_at_10")}
        progress("safety", f"harmful candidate -> {ev['status']} ({ev.get('rejection_stage')})")
        # (2) a valid candidate with an injected post-commit failure must be rolled back to the exact previous state
        before_ck = ctx.persistence.export_live_state().checksum()
        before_v = ctx.versions.active_id()
        out["harmful_candidate"]["conclusive"] = out["harmful_candidate"]["status"] == "rejected"
        s.require_benefit = False
        out["post_commit_fault"] = {"status": "inconclusive", "detail": "no probe candidate passed pre-commit validation"}
        for pair in pairs[:6]:
            ev = ctx.adaptation.process("add_shortcut_edge", list(pair), apply=True, trigger="safety_probe",
                                        fault="post_commit", window=window)
            if ev["status"] == "rejected":
                continue
            out["post_commit_fault"] = {
                "status": ev["status"], "recovered": ev.get("recovered"), "rolled_back_to": ev.get("rolled_back_to"),
                "error": ev.get("error"), "state_restored_exactly": ctx.persistence.export_live_state().checksum() == before_ck,
                "version_pointer_restored": ctx.versions.active_id() in (before_v, ev.get("rolled_back_to")),
                "integrity_passed": ctx.validator.check()["passed"], "pair": list(pair)}
            break
        progress("safety", f"post-commit fault -> {out['post_commit_fault']['status']}")
        # (3) explicit restoration around a real committed change: commit -> restore previous -> restore the change again
        pre_ck, pre_v = ctx.persistence.export_live_state().checksum(), ctx.versions.active_id()
        out["restoration"] = {"status": "inconclusive", "detail": "no probe candidate passed pre-commit validation"}
        for pair in pairs[:6]:
            ev = ctx.adaptation.process("add_shortcut_edge", list(pair), apply=True, trigger="safety_probe", window=window)
            if ev["status"] != "completed":
                continue
            post_v, post_ck = ctx.versions.active_id(), ctx.persistence.export_live_state().checksum()
            exp_pre, _ = ctx.lifecycle.reconcile_state(ctx.versions.snapshot_state(ev["before_version"]))
            r1 = ctx.lifecycle.restore(ev["before_version"], reason="safety probe: restore pre-change architecture", evaluate=False)
            at_pre = ctx.persistence.export_live_state().checksum()
            r2 = ctx.lifecycle.restore(post_v, reason="safety probe: restore the changed architecture", evaluate=False)
            out["restoration"] = {
                "committed_version": post_v, "restored_to": ev["before_version"], "restore_status": r1["status"],
                "state_matches_pre_change_snapshot": at_pre == exp_pre.checksum(),
                "restored_again": r2["status"], "state_matches_changed_architecture": ctx.persistence.export_live_state().checksum() == post_ck,
                "integrity_passed": ctx.validator.check()["passed"], "pair": list(pair)}
            break
        progress("safety", f"restoration -> {out['restoration'].get('restore_status') or out['restoration']['status']}")
        del pre_ck, pre_v
    finally:
        (s.coaccess_min_count, s.coaccess_min_window_queries, s.require_benefit, s.max_edges_total, s.max_coaccess_per_node,
         s.cumulative_guard) = saved
    return out


# --------------------------------------------------------------------------------------------- comparisons
def compare_runs(qrels: dict[str, dict[str, int]], a: MethodRun, b: MethodRun, pq_a: dict, pq_b: dict,
                 queries: dict[str, str], resamples: int, seed: int, name: str) -> dict[str, Any]:
    """b − a (e.g. adaptive − fixed). Paired query-level bootstrap on nDCG@10 and Recall@5, plus all metric deltas."""
    qids = sorted(qrels)
    out: dict[str, Any] = {"comparison": name, "baseline": a.name, "treatment": b.name, "queries": len(qids), "metric_deltas": {}}
    for k in METRIC_KEYS:
        out["metric_deltas"][k] = {"baseline": mean_of(pq_a, k), "treatment": mean_of(pq_b, k),
                                   "diff": mean_of(pq_b, k) - mean_of(pq_a, k)}
    out["bootstrap"] = {k: paired_bootstrap([pq_a[q][k] for q in qids], [pq_b[q][k] for q in qids], resamples, seed)
                        for k in ("ndcg_at_10", "recall_at_5")}
    diffs = sorted(((pq_b[q]["ndcg_at_10"] - pq_a[q]["ndcg_at_10"], q) for q in qids))
    row = lambda d, q: {"query_id": q, "query": queries.get(q, "")[:200], "ndcg_baseline": pq_a[q]["ndcg_at_10"],  # noqa: E731
                        "ndcg_treatment": pq_b[q]["ndcg_at_10"], "delta": d}
    out["most_regressed"] = [row(d, q) for d, q in diffs[:15] if d < -1e-12]
    out["most_improved"] = [row(d, q) for d, q in reversed(diffs[-15:]) if d > 1e-12]
    return out


# --------------------------------------------------------------------------------------------- main per-dataset run
def run_dataset(cfg: BenchmarkConfig, base_settings, embedder, dataset_id: str, batch_id: str, *, mongo=None,
                sink: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    cfg.resolve().validate()
    out_dir = Path(cfg.artifacts_dir) / dataset_id / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(out_dir / "logs.jsonl", sink)
    caveats: list[str] = []
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    manifest: dict[str, Any] = {"run_id": batch_id, "dataset_id": dataset_id, "started_at": iso(), "status": "running",
                                "config": cfg.to_dict(), "environment": environment_info(embedder)}
    _write(out_dir / "manifest.json", manifest)

    # 1. data + splits
    progress("data", f"loading {dataset_id} with the official BEIR loader")
    data = load_for_protocol(dataset_id, cfg)
    proto = build_split_protocol(data, cfg.seed, cfg.query_limit)
    caveats += data.issues
    if proto.notes:
        caveats.append(proto.notes)
    if proto.dropped_for_text_overlap:
        caveats.append(f"{proto.dropped_for_text_overlap} workload/validation queries were dropped because their text equals a test query")
    test_qrels = group_qrels(data, proto, "test")
    test_queries = group_queries(data, proto, "test")
    workload = group_queries(data, proto, "workload")
    n_docs = len(data.corpus)
    progress("data", f"corpus={n_docs} test_queries={len(test_queries)} validation={len(proto.validation)} workload={len(workload)} "
             f"official_test={proto.official_test}")

    # 2. embeddings + deterministic graph (content only)
    t = time.perf_counter()
    ids, mat, emb_info = embed_corpus(embedder, dataset_id, data.corpus, data.source["corpus_sha256"],
                                      str(Path(cfg.data_dir) / "embeddings"), force=cfg.force_embeddings,
                                      progress=lambda m: progress("embed", m))
    timings["corpus_embedding_s"] = round(time.perf_counter() - t, 2)
    doc_map = DocMap.build(dataset_id, ids)
    vectors = {doc_map.doc_to_mem[d]: mat[i] for i, d in enumerate(ids)}
    t = time.perf_counter()
    state0, ginfo = build_initial_architecture(dataset_id, doc_map, ids, mat, data.corpus, cfg.graph)
    timings["graph_init_s"] = round(time.perf_counter() - t, 2)
    progress("graph", f"initial graph: {ginfo['modules']} modules, {ginfo['groups']} groups, module size {ginfo['module_size']}")

    # 3. isolated MORPHE context (real services) + ingestion
    median = ginfo["module_size"]["median"]
    capacity = int(cfg.capacity_factor * median)
    settings_over = dict(seed_module_count=cfg.seed_module_count, max_candidate_modules=cfg.max_candidate_modules,
                         fallback_min_score=cfg.fallback_min_score, adaptation_query_batch=cfg.adaptation_query_batch,
                         split_capacity_threshold=capacity, eval_repetitions=cfg.validation_repetitions, **cfg.protocol_flags_settings(),
                         adaptation_round_robin=True, adaptation_max_proposals_per_window=cfg.max_proposals_per_window)
    ctx = make_context(base_settings, embedder, dataset_id, batch_id, mongo=mongo, **settings_over)
    try:
        ing = ingest_corpus(ctx, dataset_id, data.corpus, ids, mat, doc_map, state0)
        timings["ingest_s"] = ing["seconds"]
        mapping = verify_mapping(ctx, doc_map)
        progress("ingest", f"{ing['documents']} documents ingested in {ing['seconds']}s; BEIR id mapping verified; "
                 f"initial version {ing['initial_version']}")
        initial_version = ing["initial_version"]
        val_q, val_info = validation_queries(data, proto, doc_map, cfg.validation_cap, cfg.seed)
        ds = ctx.datasets.replace_queries(val_q, {"name": f"BEIR {dataset_id} validation group", "label_provenance": "official qrels"})
        if not ds.valid:
            raise ValueError(f"validation set invalid: {ds.issues[:3]}")
        sweep = routing_sweep(ctx, state0, progress) if cfg.routing_sweep else None
        if sweep:
            _write(out_dir / "sweep.json", sweep)
        # 4. adaptation on workload + validation (test group untouched)
        t = time.perf_counter()
        adaptation = run_adaptation(ctx, workload, cfg, progress) if "adaptive_graph" in cfg.methods else {"skipped": True}
        timings["adaptation_s"] = round(time.perf_counter() - t, 2)
        adaptive_version = ctx.versions.active_id()
        events = collect_adaptation_events(ctx)
        applied = [e for e in events if e["status"] == "completed" and e["operation"] != "restore_version"]
        progress("adaptation", f"frozen adaptive architecture {adaptive_version} after {len(applied)} accepted operations")

        # 5. frozen evaluation on the test group (each method once; BM25 independent)
        fixed_state, _ = ctx.lifecycle.reconcile_state(ctx.versions.snapshot_state(initial_version))
        adaptive_state = ctx.persistence.export_live_state()
        qvecs, embed_ms = embed_queries_timed(embedder, test_queries)
        runs: dict[str, MethodRun] = {}
        engines: dict[str, Any] = {}
        t = time.perf_counter()
        if "bm25" in cfg.methods:
            progress("eval", "BM25")
            idx = BM25Index(ids, [(data.corpus[d].get("title") or "") + " " + (data.corpus[d].get("text") or "") for d in ids])
            runs["bm25"] = run_bm25(idx, test_queries, cfg.latency_repetitions, cfg.depth)
        fixed_engine = sandbox_engine(ctx, fixed_state, vectors)
        if "global_dense" in cfg.methods:
            progress("eval", "global dense")
            runs["global_dense"] = run_dense("global_dense", fixed_engine, "global", qvecs, embed_ms, doc_map, cfg.latency_repetitions, cfg.depth)
        if "fixed_graph" in cfg.methods:
            progress("eval", "fixed graph")
            runs["fixed_graph"] = run_dense("fixed_graph", fixed_engine, "graph", qvecs, embed_ms, doc_map, cfg.latency_repetitions, cfg.depth)
        if "adaptive_graph" in cfg.methods:
            progress("eval", "adaptive graph")
            ad_engine = sandbox_engine(ctx, adaptive_state, vectors)
            runs["adaptive_graph"] = run_dense("adaptive_graph", ad_engine, "graph", qvecs, embed_ms, doc_map, cfg.latency_repetitions, cfg.depth)
            # parity: the sandbox the numbers come from must reproduce the live production engine
            live_check = _live_parity(ctx, qvecs, runs["adaptive_graph"], doc_map, cfg.depth)
        timings["test_evaluation_s"] = round(time.perf_counter() - t, 2)

        # 6. metrics (BEIR) + per-query + statistics
        metrics: dict[str, Any] = {}
        per_query: dict[str, Any] = {}
        pq_raw: dict[str, dict] = {}
        gaps = {}
        for name, run in runs.items():
            agg = evaluate_beir(test_qrels, run.results)
            pq = per_query_metrics(test_qrels, run.results)
            gaps[name] = consistency_gap(agg, pq)
            if gaps[name] > 1e-4:
                raise AssertionError(f"per-query metrics disagree with BEIR aggregate for {name}: gap {gaps[name]}")
            metrics[name] = agg
            pq_raw[name] = pq
            per_query[name] = {q: {**pq[q], **{k: v for k, v in run.latency[q].items()}} for q in pq}
        latency = {n: summarize_latency(r, n_docs) for n, r in runs.items()}
        comparisons = []
        for a, b, label in (("fixed_graph", "adaptive_graph", "adaptive_vs_fixed"), ("global_dense", "fixed_graph", "fixed_vs_global"),
                            ("global_dense", "adaptive_graph", "adaptive_vs_global"), ("bm25", "global_dense", "global_dense_vs_bm25")):
            if a in runs and b in runs:
                comparisons.append(compare_runs(test_qrels, runs[a], runs[b], pq_raw[a], pq_raw[b], test_queries,
                                                cfg.bootstrap_resamples, cfg.seed, label))
        # 7. artifacts
        from beir import util as beir_util
        for name, run in runs.items():
            beir_util.save_runfile(str(out_dir / f"run_{name}.trec"), run.results, run_name=name, top_k=cfg.depth)
        graph_info = {"initial": {"nodes": len(state0.modules), "edges": len(state0.edges), "content_modules": len(state0.content_modules())},
                      "adaptive": {"nodes": len(adaptive_state.modules), "edges": len(adaptive_state.edges),
                                   "content_modules": len(adaptive_state.content_modules()),
                                   "co_access_edges": len(adaptive_state.co_access_edges())}}
        system = {"timings_s": timings, "process_rss_mb": _rss_mb(), "graph": graph_info,
                  "embedding_cache_mb": round(Path(emb_info["path"]).stat().st_size / 2**20, 1),
                  "qdrant_storage": "in-memory (no on-disk size)", "initial_graph": ginfo, "documents": n_docs,
                  "live_parity": locals().get("live_check")}
        _write(out_dir / "metrics.json", {"methods": metrics, "headline": HEADLINE, "k_values": [1, 3, 5, 10, 100],
                                          "beir_vs_per_query_max_gap": gaps})
        _write(out_dir / "per_query_metrics.json", {"queries": test_queries, "methods": per_query})
        _write(out_dir / "latency.json", {"methods": latency, "system": system, "timing_protocol": TIMING_PROTOCOL})
        _write(out_dir / "comparisons.json", comparisons)
        _write(out_dir / "adaptation.json", {"protocol": adaptation, "events": events, "initial_version": initial_version,
                                              "adaptive_version": adaptive_version,
                                              "accepted": len(applied),
                                              "rejected": sum(1 for e in events if e["status"] == "rejected"),
                                              "failed": sum(1 for e in events if e["status"] == "failed"),
                                              "rolled_back": sum(1 for e in events if e["status"] == "rolled_back"),
                                              "proposals_evaluated": len(events)})
        safety = safety_probe(ctx, workload, cfg, progress) if (cfg.safety_probe and "adaptive_graph" in cfg.methods) else {"skipped": True}
        _write(out_dir / "safety.json", safety)
        manifest.update({
            "status": "completed", "finished_at": iso(), "duration_s": round(time.perf_counter() - t_all, 1),
            "split": cfg.split, "official_test_split": proto.official_test, "smoke": cfg.query_limit is not None,
            "truncated": proto.truncated, "query_limit": cfg.query_limit, "seed": cfg.seed,
            "dataset": {"counts": data.counts(), "source": data.source, "test_group_queries": len(test_queries),
                        "test_group_qrels": sum(len(v) for v in test_qrels.values()), "relevant_grades": sorted(
                            {g for v in test_qrels.values() for g in v.values()})},
            "split_protocol": proto.to_dict(), "validation": {"queries": len(val_q), **val_info},
            "embedding": emb_info, "preprocessing": {"id": "beir_title_text_v1", "text": "title + ' ' + text",
                                                     "truncation": f"model max_seq_length={getattr(getattr(embedder, 'inner', embedder), 'max_seq_length', None)} wordpieces"},
            "graph_init": ginfo, "id_mapping": mapping, "methods": list(runs),
            "retrieval_settings": {"depth": cfg.depth, "seed_module_count": cfg.seed_module_count, "max_candidate_modules": cfg.max_candidate_modules,
                                   "fallback_min_score": cfg.fallback_min_score, "fallback_decision_depth": 10,
                                   "split_capacity_threshold": capacity},
            "acceptance_thresholds": ctx.policy.thresholds(),
            "protocol": cfg.protocol_flags(), "routing_sweep": bool(sweep),
            "adaptation": {"windows": adaptation.get("windows_planned"), "accepted": len(applied),
                           "initial_version": initial_version, "adaptive_version": adaptive_version},
            "architecture_version_before": initial_version, "architecture_version_after": adaptive_version,
            "timing_methodology": TIMING_PROTOCOL, "caveats": caveats,
            "artifacts": sorted(p.name for p in out_dir.iterdir())})
        _write(out_dir / "manifest.json", manifest)
        progress("done", f"{dataset_id} finished in {manifest['duration_s']}s")
        return {"manifest": manifest, "metrics": metrics, "latency": latency, "comparisons": comparisons,
                "adaptation": {"accepted": len(applied)}, "out_dir": str(out_dir)}
    except Exception as exc:
        manifest.update({"status": "failed", "finished_at": iso(), "error": f"{type(exc).__name__}: {exc}"[:500], "caveats": caveats})
        _write(out_dir / "manifest.json", manifest)
        progress("error", manifest["error"])
        raise
    finally:
        if not cfg.keep_db and getattr(ctx.mongo, "client", None) is not None:
            try:
                ctx.mongo.client.drop_database(ctx.mongo.db.name)
            except Exception:
                pass
        ctx.close()


def _live_parity(ctx, qvecs: dict[str, np.ndarray], run: MethodRun, doc_map: DocMap, depth: int, sample: int = 40) -> dict[str, Any]:
    """Compare the sandbox rankings used for the numbers against the live production engine (same architecture)."""
    from .methods import FALLBACK_K
    qids = sorted(qvecs)[:sample]
    same = 0
    for q in qids:
        out = ctx.retrieval.engine.retrieve(qvecs[q], depth, "graph", fallback_k=FALLBACK_K)
        live = [doc_map.mem_to_doc[h.memory_id] for h in out.results]
        sand = [d for d, _ in sorted(run.results[q].items(), key=lambda kv: (-kv[1], kv[0]))]
        same += int(live[:10] == sand[:10])
    return {"checked": len(qids), "identical_top10": same}


TIMING_PROTOCOL = ("time.perf_counter; models, indexes and sandboxes are built before timing; query embedding timed separately "
                   "(batch 1, warm); one warm-up retrieval per query then the median of N repetitions (BM25: minimum of N); "
                   "end-to-end = embedding + retrieval; methods run sequentially on one machine; cold-start costs are reported "
                   "separately under system.timings_s")
