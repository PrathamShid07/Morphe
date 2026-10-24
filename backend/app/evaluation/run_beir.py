"""CLI: run the BEIR benchmark.

    python -m app.evaluation.run_beir --datasets scifact --methods global_dense,fixed_graph,adaptive_graph --split test --query-limit 20
    python -m app.evaluation.run_beir --datasets scifact,nfcorpus,arguana --methods bm25,global_dense,fixed_graph,adaptive_graph --split test --download

Run from the `backend/` directory (so `../data/beir` resolves) with MongoDB running. See docs/BEIR_BENCHMARK_METHODOLOGY.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from ..benchmark.report import BATCH_DIR, batch_summary, text_report
from ..benchmark.runner import ALL_METHODS, BenchmarkConfig, run_dataset
from ..common import iso, new_id
from ..config import Settings
from ..ingestion.embedder import build_embedder


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m app.evaluation.run_beir", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--datasets", default="scifact", help="comma-separated BEIR dataset ids (default: scifact)")
    ap.add_argument("--methods", default=",".join(ALL_METHODS), help=f"comma-separated subset of {ALL_METHODS}")
    ap.add_argument("--split", default="test", help="final evaluation split (only 'test' is supported)")
    ap.add_argument("--query-limit", type=int, default=None, help="SMOKE: cap the test/validation groups; marks the run non-official")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--download", action="store_true", help="download missing datasets from the BEIR host (otherwise a clear error)")
    ap.add_argument("--force-embeddings", action="store_true", help="ignore the embedding cache")
    ap.add_argument("--max-windows", type=int, default=6, help="maximum adaptation windows of 50 workload queries")
    ap.add_argument("--protocol", choices=["v1", "v2"], default="v2", help="v1 = first protocol (single unpaired latency pass, no significance "
                    "or drift guard); v2 = paired interleaved latency, significance-gated benefit, cumulative guard (default)")
    ap.add_argument("--no-sweep", action="store_true", help="skip the validation-only routing sensitivity sweep")
    ap.add_argument("--no-safety-probe", action="store_true")
    ap.add_argument("--keep-db", action="store_true", help="keep the temporary MongoDB database of each dataset run")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--artifacts-dir", default=None)
    ap.add_argument("--mongo-backend", choices=["pymongo", "mongomock"], default=None, help="mongomock is for offline smoke tests only")
    ap.add_argument("--embedding-backend", choices=["sentence_transformers", "hashing"], default=None,
                    help="hashing is a test-only fake embedder (not a meaningful retrieval model)")
    ap.add_argument("--run-id", default=None)
    return ap.parse_args(argv)


def batch_id_now() -> str:
    return time.strftime("beir_%Y%m%dT%H%M%S") + "_" + new_id("x")[-4:].lower()


def build_config(a: argparse.Namespace, base: Settings) -> BenchmarkConfig:
    cfg = BenchmarkConfig(
        datasets=[d.strip() for d in a.datasets.split(",") if d.strip()], methods=[m.strip() for m in a.methods.split(",") if m.strip()],
        split=a.split, query_limit=a.query_limit, seed=a.seed, download=a.download, force_embeddings=a.force_embeddings,
        max_windows=a.max_windows, protocol=a.protocol, routing_sweep=not a.no_sweep, safety_probe=not a.no_safety_probe, keep_db=a.keep_db,
        data_dir=a.data_dir or base.beir_data_dir, artifacts_dir=a.artifacts_dir or base.beir_artifacts_dir)
    cfg.graph.seed = a.seed
    cfg.validate()
    return cfg


def main(argv: list[str] | None = None) -> int:
    a = parse(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    over = {k: v for k, v in (("mongo_backend", a.mongo_backend), ("embedding_backend", a.embedding_backend)) if v}
    base = Settings(**over)
    cfg = build_config(a, base)
    batch = a.run_id or batch_id_now()
    t = time.perf_counter()
    embedder = build_embedder(base)
    print(f"run {batch}: model loaded in {time.perf_counter() - t:.1f}s (cold start, excluded from query timing)", flush=True)
    failures = 0
    for ds in cfg.datasets:
        try:
            run_dataset(cfg, base, embedder, ds, batch)
        except Exception as exc:  # keep going so one missing dataset does not hide the others
            failures += 1
            print(f"!! {ds}: {type(exc).__name__}: {exc}", file=sys.stderr)
    root = Path(cfg.artifacts_dir) / BATCH_DIR
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{batch}.json").write_text(json.dumps({"run_id": batch, "datasets": cfg.datasets, "finished_at": iso(),
                                                    "failures": failures}, indent=1))
    try:
        print("\n" + text_report(cfg.artifacts_dir, batch))
    except FileNotFoundError:
        pass
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
