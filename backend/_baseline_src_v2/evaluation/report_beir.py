"""CLI: print the summary/comparison of a saved benchmark run.

    python -m app.evaluation.report_beir --latest
    python -m app.evaluation.report_beir --run-id beir_20261025T101500_ab12 [--json]
"""
from __future__ import annotations

import argparse
import json

from ..benchmark.report import batch_summary, latest_batch_id, text_report
from ..config import Settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.evaluation.report_beir", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--run-id")
    ap.add_argument("--artifacts-dir")
    ap.add_argument("--json", action="store_true", help="print the batch summary as JSON")
    a = ap.parse_args(argv)
    root = a.artifacts_dir or Settings().beir_artifacts_dir
    rid = a.run_id or (latest_batch_id(root) if a.latest else None)
    if not rid:
        ap.error("give --run-id or --latest (no saved runs found)" if a.latest else "give --run-id or --latest")
    print(json.dumps(batch_summary(root, rid), indent=1, default=str) if a.json else text_report(root, rid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
