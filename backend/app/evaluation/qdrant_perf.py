"""Phase-4 performance analysis: where does the time of filtered vector retrieval go, and does server mode change it?

Compares, on the SAME stored vectors and the SAME routed module lists (taken from the real router), per query:
  numpy_exact_global / numpy_exact_candidates   -- float32 matmul over all rows / only the candidate rows (diagnostic baseline, not a production path)
  local_*    Qdrant local (in-process) mode: unfiltered, status-only (what global retrieval sends today), status + module (what graph retrieval sends)
  server_*   Qdrant server mode (separate process, REST, localhost): payload indexes absent / present, plain scan / HNSW
Also verifies that server mode does not change retrieval semantics (same top-k as local through the real RetrievalEngine) and records the
payload schema actually present. Timing uses time.perf_counter, one warm-up, median of N repetitions per query, then p50/p95/mean over queries.
Every row carries its machine/deployment label; local and server numbers come from the same machine in the same session and are never mixed with other runs.
Only query vectors are used (no relevance labels).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np
from qdrant_client import QdrantClient, models

from ..benchmark.corpus import DocMap, build_initial_architecture, embed_corpus
from ..benchmark.methods import DEPTH, FALLBACK_K
from ..benchmark.runner import BenchmarkConfig, environment_info, load_for_protocol
from ..benchmark.splits import build_split_protocol, group_queries
from ..common import iso
from ..config import Settings
from ..graph.manager import graph_from_state
from ..ingestion.embedder import build_embedder
from ..retrieval.controller import RetrievalEngine
from ..retrieval.graph_selector import expand_candidates
from ..storage.qdrant_store import QdrantStore
from ..versioning.state import centroid

SERVER_URL = "http://127.0.0.1:6343"


def make_store(kind: str, dim: int, tag: str, *, payload_index: bool = True, hnsw: bool = False) -> QdrantStore:
    """kind: local | server. Server collections get unique names so experiments never collide."""
    if kind == "local":
        return QdrantStore.in_memory(dim, f"m_{tag}", f"p_{tag}")
    client = QdrantClient(url=SERVER_URL, timeout=120)
    store = QdrantStore(client, f"perf_m_{tag}_{uuid.uuid4().hex[:6]}", f"perf_p_{tag}_{uuid.uuid4().hex[:6]}", dim, None, is_local=True)
    opt = models.OptimizersConfigDiff(indexing_threshold=100) if hnsw else None  # default threshold is 20,000 vectors: below it search is a plain scan
    for name in (store.memory_collection, store.module_collection):
        client.create_collection(name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                                 **({"optimizers_config": opt} if (hnsw and name == store.memory_collection) else {}))
    if payload_index:  # indexes are created BEFORE ingestion, as in QdrantStore.ensure_collections for server mode
        for field in ("module_id", "status", "source_id"):
            client.create_payload_index(store.memory_collection, field, models.PayloadSchemaType.KEYWORD)
    store.is_local = kind == "local"
    return store


def populate(store: QdrantStore, state, vectors: dict[str, np.ndarray]) -> None:
    assign = state.module_of()
    store.upsert_memories([(mid, vectors[mid], {"memory_id": mid, "module_id": mod, "status": "active"}) for mid, mod in assign.items()])
    store.upsert_module_profiles([(m, state.modules[m].profile_vector if state.modules[m].profile_vector is not None
                                   else centroid([vectors[i] for i in state.assignments[m]]), {"display_name": state.modules[m].name})
                                  for m in state.content_modules()])
    if not store.is_local:  # wait until the server has finished optimising (indexing) so timings are steady-state
        for _ in range(120):
            info = store.client.get_collection(store.memory_collection)
            if str(info.status).lower().endswith("green") and info.points_count == len(assign):
                break
            time.sleep(0.5)


def _timeit(fn: Callable[[], Any], reps: int) -> float:
    fn()
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return float(statistics.median(xs))


def _summ(xs: list[float]) -> dict[str, float]:
    return {"p50_ms": float(np.percentile(xs, 50)), "p95_ms": float(np.percentile(xs, 95)), "mean_ms": float(np.mean(xs))}


def run_dataset(cfg: BenchmarkConfig, base: Settings, embedder, dataset_id: str, out_root: Path, run_id: str, n_queries: int, reps: int,
                with_server: bool) -> dict[str, Any]:
    data = load_for_protocol(dataset_id, cfg)
    proto = build_split_protocol(data, cfg.seed, cfg.query_limit)
    qs = group_queries(data, proto, "validation")
    qids = sorted(qs)[:n_queries]
    ids, mat, emb = embed_corpus(embedder, dataset_id, data.corpus, data.source["corpus_sha256"], str(Path(cfg.data_dir) / "embeddings"))
    doc_map = DocMap.build(dataset_id, ids)
    vectors = {doc_map.doc_to_mem[d]: mat[i] for i, d in enumerate(ids)}
    state, ginfo = build_initial_architecture(dataset_id, doc_map, ids, mat, data.corpus, cfg.graph)
    qv = {q: v for q, v in zip(qids, embedder.encode([qs[q] for q in qids]))}
    dim = mat.shape[1]
    N = len(ids)
    unit = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
    mem_order = [doc_map.doc_to_mem[d] for d in ids]
    row_of = {m: i for i, m in enumerate(mem_order)}
    rows_in_module = {m: np.array(sorted(row_of[x] for x in state.assignments[m]), dtype=np.int64) for m in state.content_modules()}

    stores: dict[str, QdrantStore] = {"local": make_store("local", dim, "perf")}
    if with_server:
        stores["server_index"] = make_store("server", dim, "idx", payload_index=True)
        stores["server_noindex"] = make_store("server", dim, "noidx", payload_index=False)
        stores["server_hnsw"] = make_store("server", dim, "hnsw", payload_index=True, hnsw=True)
    for s in stores.values():
        populate(s, state, vectors)
    graph = graph_from_state(state)
    router = RetrievalEngine(base.model_copy(update={"seed_module_count": cfg.seed_module_count, "max_candidate_modules": cfg.max_candidate_modules}),
                             stores["local"], lambda: graph)

    # routing per query from the real router (seeds -> expansion), shared by every variant
    route: dict[str, list[str]] = {}
    for q in qids:
        seeds = [(h.module_id, h.score) for h in stores["local"].search_modules(qv[q], cfg.seed_module_count)]
        route[q] = [c.module_id for c in expand_candidates(seeds, graph, cfg.max_candidate_modules)]
    eligible = {q: int(sum(len(rows_in_module[m]) for m in route[q])) for q in qids}

    per_variant: dict[str, list[float]] = {}

    def record(name: str, ms: float) -> None:
        per_variant.setdefault(name, []).append(ms)

    def np_global(q: str):
        s = unit @ (qv[q] / np.linalg.norm(qv[q])).astype(np.float32)
        idx = np.argpartition(-s, min(DEPTH, N - 1))[:DEPTH]
        return idx[np.argsort(-s[idx])]

    def np_cand(q: str):
        rows = np.concatenate([rows_in_module[m] for m in route[q]])
        s = unit[rows] @ (qv[q] / np.linalg.norm(qv[q])).astype(np.float32)
        k = min(DEPTH, len(rows))
        idx = np.argpartition(-s, k - 1)[:k]
        return rows[idx[np.argsort(-s[idx])]]

    for q in qids:
        record("numpy_exact_global", _timeit(lambda: np_global(q), reps))
        record("numpy_exact_candidates", _timeit(lambda: np_cand(q), reps))
        for name, st in stores.items():
            f_none = lambda st=st: st.client.query_points(st.memory_collection, query=qv[q].tolist(), limit=DEPTH, with_payload=True)  # noqa: E731
            record(f"{name}:unfiltered", _timeit(f_none, reps))
            record(f"{name}:status_only(global today)", _timeit(lambda st=st: st.search_memories(qv[q], None, DEPTH), reps))
            record(f"{name}:status+module(graph today)", _timeit(lambda st=st: st.search_memories(qv[q], route[q], DEPTH), reps))
            mod_only = models.Filter(must=[models.FieldCondition(key="module_id", match=models.MatchAny(any=route[q]))])
            record(f"{name}:module_only", _timeit(lambda st=st: st.client.query_points(st.memory_collection, query=qv[q].tolist(), limit=DEPTH,
                                                                                         query_filter=mod_only, with_payload=True), reps))
    summary = {k: _summ(v) for k, v in per_variant.items()}

    # exactness / semantic equivalence: every store must return the same top-k as the exact numpy ranking (HNSW may legitimately differ)
    sem: dict[str, Any] = {}
    for name, st in stores.items():
        same_g, same_c, overlap_g = 0, 0, []
        for q in qids:
            ex_g = [mem_order[i] for i in np_global(q)][:10]
            ex_c = [mem_order[i] for i in np_cand(q)][:10]
            got_g = [h.memory_id for h in st.search_memories(qv[q], None, DEPTH)][:10]
            got_c = [h.memory_id for h in st.search_memories(qv[q], route[q], DEPTH)][:10]
            same_g += int(set(ex_g) == set(got_g))
            same_c += int(set(ex_c) == set(got_c))
            overlap_g.append(len(set(ex_g) & set(got_g)) / 10)
        sem[name] = {"global_top10_set_equals_exact": f"{same_g}/{len(qids)}", "candidates_top10_set_equals_exact": f"{same_c}/{len(qids)}",
                     "mean_global_top10_overlap_with_exact": float(np.mean(overlap_g))}

    # end-to-end through the REAL engine on each store (routing + search + ranking + fallback), local vs server
    engine_rows: dict[str, Any] = {}
    ref: dict[str, dict[str, list[str]]] = {}
    for name, st in stores.items():
        eng = RetrievalEngine(base.model_copy(update={"seed_module_count": cfg.seed_module_count, "max_candidate_modules": cfg.max_candidate_modules,
                                                      "fallback_min_score": cfg.fallback_min_score}), st, lambda: graph)
        out: dict[str, Any] = {}
        for mode in ("global", "graph"):
            tot, stg, ids_by_q = [], {k: [] for k in ("seed", "expand", "search", "rank", "fallback")}, {}
            for q in qids:
                eng.retrieve(qv[q], DEPTH, mode, fallback_k=FALLBACK_K)
                rs = []
                for _ in range(reps):
                    t0 = time.perf_counter()
                    o = eng.retrieve(qv[q], DEPTH, mode, fallback_k=FALLBACK_K)
                    rs.append(((time.perf_counter() - t0) * 1000, dict(o.timings)))
                tot.append(statistics.median(x[0] for x in rs))
                for k in stg:
                    stg[k].append(statistics.median(x[1].get(k, 0.0) for x in rs))
                ids_by_q[q] = [h.memory_id for h in o.results][:10]
            ref.setdefault(mode, {})[name] = ids_by_q
            out[mode] = {"total": _summ(tot), "stage_mean_ms": {k: float(np.mean(v)) for k, v in stg.items()}}
        engine_rows[name] = out
    for name in stores:
        engine_rows[name]["top10_identical_to_local_engine"] = {mode: f"{sum(ref[mode][name][q] == ref[mode]['local'][q] for q in qids)}/{len(qids)}"
                                                               for mode in ("global", "graph")}
    schema = {}
    for name, st in stores.items():
        info = st.client.get_collection(st.memory_collection)
        schema[name] = {"mode": "local in-process" if name == "local" else "server (REST, localhost)", "points": info.points_count,
                        "payload_schema": {k: str(v.data_type) if hasattr(v, "data_type") else str(v) for k, v in (info.payload_schema or {}).items()},
                        "indexed_vectors_count": getattr(info, "indexed_vectors_count", None),
                        "hnsw_built": bool(getattr(info, "indexed_vectors_count", 0)), "status": str(info.status)}
    for name, st in stores.items():
        if name != "local":
            try:
                st.client.delete_collection(st.memory_collection)
                st.client.delete_collection(st.module_collection)
            except Exception:
                pass
    res = {"run_id": run_id, "dataset_id": dataset_id, "created_at": iso(), "documents": N, "queries": len(qids), "repetitions": reps,
           "module_filter": {"mean_modules_in_filter": float(np.mean([len(v) for v in route.values()])), "mean_eligible_docs": float(np.mean(list(eligible.values()))),
                             "eligible_share_of_corpus": float(np.mean(list(eligible.values()))) / N},
           "machine": environment_info(embedder) | {"qdrant_server": "1.20.0 binary (.tools/qdrant), localhost REST, default optimizers" if with_server else None},
           "label": "all rows measured on this one machine in one session; local = in-process qdrant-client 1.20.0, server = separate process",
           "raw_search_ms": summary, "semantics_vs_exact_numpy": sem, "engine_end_to_end_ms": engine_rows, "collections": schema,
           "note": "raw_search_ms rows time the store call only (candidate lists are given); engine_end_to_end_ms rows time the full RetrievalEngine.retrieve"}
    d = out_root / run_id / dataset_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "qdrant_perf.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", default="scifact,nfcorpus,arguana")
    ap.add_argument("--run-id", default=time.strftime("perf_%Y%m%dT%H%M%S"))
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--server", action="store_true", help=f"also measure a Qdrant server at {SERVER_URL} (start it first, isolated storage)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    base = Settings()
    cfg = BenchmarkConfig(data_dir=base.beir_data_dir, artifacts_dir=base.beir_artifacts_dir).resolve()
    out_root = Path(a.out) if a.out else Path(base.beir_artifacts_dir).parent / "beir_phase2"
    embedder = build_embedder(base)
    for ds in [d.strip() for d in a.datasets.split(",") if d.strip()]:
        r = run_dataset(cfg, base, embedder, ds, out_root, a.run_id, a.queries, a.reps, a.server)
        print(f"{ds}: " + " | ".join(f"{k} p50={v['p50_ms']:.2f}" for k, v in r["raw_search_ms"].items() if "global today" in k or "graph today" in k or k.startswith("numpy")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
