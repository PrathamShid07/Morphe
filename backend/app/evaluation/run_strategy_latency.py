"""Latency of the four retrieval strategies on an INDEXED Qdrant SERVER (the deployment where filtered search is cheap), using the frozen configuration
of a finished strategy run and the same engine code. Query vectors only: the VALIDATION group's text is embedded; no relevance label is read.

    python -m app.evaluation.run_strategy_latency --run-id strat_01 --datasets scifact,nfcorpus,arguana     (server on 127.0.0.1:6343, see qdrant_perf.py)

Writes `<run dir>/server_latency.json`. Also checks that each strategy returns the SAME top-10 on the server as on local Qdrant (semantics unchanged).
Timing: one warm-up, median of N repetitions per query, strategies interleaved with a rotating order; machine/deployment recorded in the artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

import numpy as np

from ..benchmark.methods import DEPTH, FALLBACK_K, embed_queries_timed
from ..benchmark.runner import BenchmarkConfig, environment_info
from ..common import iso
from ..config import Settings
from ..ingestion.embedder import build_embedder
from ..retrieval.controller import RetrievalEngine
from .qdrant_perf import SERVER_URL, make_store, populate
from .run_strategies import MODE, ROOT_NAME, STRATEGIES, dataset_env, frozen_overrides, group_data, variant


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--datasets", default="scifact,nfcorpus,arguana")
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    base = Settings()
    cfg = BenchmarkConfig(data_dir=base.beir_data_dir, artifacts_dir=base.beir_artifacts_dir).resolve()
    out_root = Path(a.out) if a.out else Path(base.beir_artifacts_dir).parent
    run_dir = out_root / ROOT_NAME / a.run_id
    fz_path = run_dir / "frozen_config.json"
    fz = json.loads(fz_path.read_text())
    ov = frozen_overrides(fz)
    embedder = build_embedder(base)
    result: dict = {"run_id": a.run_id, "created_at": iso(), "frozen_config_sha256": hashlib.sha256(fz_path.read_bytes()).hexdigest(),
                    "deployment": f"Qdrant server 1.20.0 (separate process, REST, localhost) at {SERVER_URL}, keyword payload indexes on module_id/status/source_id created before ingestion, default optimizer (plain scan: collections are below the 20,000-vector HNSW threshold)",
                    "group": "validation (query text only)", "queries": a.queries, "repetitions": a.reps, "machine": environment_info(embedder), "datasets": {}}
    for ds in [d.strip() for d in a.datasets.split(",") if d.strip()]:
        with dataset_env(cfg, base, embedder, ds, a.run_id + "_lat") as env:
            queries, _ = group_data(env, "validation", a.queries)
            qvecs, _ = embed_queries_timed(embedder, queries)
            store = make_store("server", env.ctx.settings.embedding_dimension, "lat", payload_index=True)
            try:
                populate(store, env.state, env.vectors)
                engines = {"global_dense": RetrievalEngine(env.ctx.settings, store, env.engine.graph_provider), "fixed_graph": RetrievalEngine(env.ctx.settings, store, env.engine.graph_provider),
                           "progressive": RetrievalEngine(env.ctx.settings.model_copy(update=ov["progressive"]), store, env.engine.graph_provider),
                           "soft": RetrievalEngine(env.ctx.settings.model_copy(update=ov["soft"]), store, env.engine.graph_provider)}
                local = {"global_dense": env.engine, "fixed_graph": env.engine, "progressive": variant(env, **ov["progressive"]), "soft": variant(env, **ov["soft"])}
                names = list(STRATEGIES)
                times = {s: [] for s in names}
                same = {s: 0 for s in names}
                for qi, (q, qv) in enumerate(qvecs.items()):
                    order = names[qi % len(names):] + names[:qi % len(names)]
                    for s in order:
                        engines[s].retrieve(qv, DEPTH, MODE[s], fallback_k=FALLBACK_K)
                    t = {s: [] for s in names}
                    last = {}
                    for _ in range(a.reps):
                        for s in order:
                            t0 = time.perf_counter()
                            last[s] = engines[s].retrieve(qv, DEPTH, MODE[s], fallback_k=FALLBACK_K)
                            t[s].append((time.perf_counter() - t0) * 1000)
                    for s in names:
                        times[s].append(statistics.median(t[s]))
                        ref = local[s].retrieve(qv, DEPTH, MODE[s], fallback_k=FALLBACK_K)
                        same[s] += int([h.memory_id for h in last[s].results][:10] == [h.memory_id for h in ref.results][:10])
                result["datasets"][ds] = {"documents": env.info["documents"], "queries": len(qvecs), "strategies": {s: {
                    "retrieval_p50_ms": float(np.percentile(times[s], 50)), "retrieval_p95_ms": float(np.percentile(times[s], 95)), "retrieval_mean_ms": float(np.mean(times[s])),
                    "top10_identical_to_local_qdrant": f"{same[s]}/{len(qvecs)}"} for s in names}}
                print(ds, {s: round(result["datasets"][ds]["strategies"][s]["retrieval_p50_ms"], 2) for s in names}, flush=True)
            finally:
                for c in (store.memory_collection, store.module_collection):
                    try:
                        store.client.delete_collection(c)
                    except Exception:
                        pass
    (run_dir / "server_latency.json").write_text(json.dumps(result, indent=1, default=str))
    print("wrote", run_dir / "server_latency.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
