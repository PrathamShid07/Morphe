"""Read saved benchmark artifacts: per-run summaries, batch macro-averages and text reports (shared by the CLI and the API)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .beir_eval import METRIC_KEYS

BATCH_DIR = "_batches"


def read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def run_dirs(artifacts_dir: str) -> list[Path]:
    root = Path(artifacts_dir)
    if not root.is_dir():
        return []
    return sorted((m.parent for m in root.glob("*/*/manifest.json") if m.parent.parent.name != BATCH_DIR),
                  key=lambda p: p.name, reverse=True)


def load_run(d: Path) -> dict[str, Any]:
    m = read_json(d / "manifest.json")
    out: dict[str, Any] = {"manifest": m}
    for name in ("metrics", "latency", "comparisons", "adaptation", "safety", "sweep"):
        f = d / f"{name}.json"
        out[name] = read_json(f) if f.exists() else None
    return out


def macro_average(per_dataset: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Equal weight per dataset; relevance judgments are never pooled across datasets."""
    methods = sorted({m for d in per_dataset.values() for m in d})
    out: dict[str, Any] = {}
    for m in methods:
        rows = [d[m] for d in per_dataset.values() if m in d]
        out[m] = {"datasets": len(rows), **{k: sum(r[k] for r in rows) / len(rows) for k in METRIC_KEYS}}
    return out


def batch_summary(artifacts_dir: str, batch_id: str) -> dict[str, Any]:
    runs = {}
    for d in run_dirs(artifacts_dir):
        if d.name == batch_id:
            runs[d.parent.name] = load_run(d)
    if not runs:
        raise FileNotFoundError(f"no artifacts for run {batch_id}")
    done = {ds: r for ds, r in runs.items() if r["manifest"]["status"] == "completed" and r["metrics"]}
    macro = macro_average({ds: {m: v for m, v in r["metrics"]["methods"].items()} for ds, r in done.items()}) if done else {}
    flags = {ds: {"official_test_split": r["manifest"].get("official_test_split"), "smoke": r["manifest"].get("smoke")}
             for ds, r in done.items()}
    return {"run_id": batch_id, "datasets": {ds: r["manifest"] for ds, r in runs.items()}, "macro_average": macro,
            "macro_note": "equal weight per dataset" + ("; INCLUDES internal-split or smoke results, not official scores"
                                                        if any(not (f["official_test_split"] and not f["smoke"]) for f in flags.values()) else ""),
            "flags": flags}


def latest_batch_id(artifacts_dir: str) -> str | None:
    ds = run_dirs(artifacts_dir)
    return ds[0].name if ds else None


def text_report(artifacts_dir: str, batch_id: str) -> str:
    s = batch_summary(artifacts_dir, batch_id)
    L = [f"MORPHE BEIR benchmark  run {batch_id}", ""]
    hdr = f"{'method':16s} {'nDCG@10':>8s} {'MAP@10':>8s} {'R@5':>7s} {'R@10':>7s} {'R@100':>7s} {'MRR@10':>8s} {'P@10':>7s}"
    for ds, man in s["datasets"].items():
        if man["status"] != "completed":
            tag = man["status"].upper()
        else:
            tag = "OFFICIAL test" if man.get("official_test_split") and not man.get("smoke") else \
                ("SMOKE (not official)" if man.get("smoke") else "INTERNAL split (not official)")
        L += [f"== {ds}  [{tag}]  status={man['status']}"]
        if man["status"] != "completed":
            L += [f"   error: {man.get('error')}", ""]
            continue
        r = load_run(Path(artifacts_dir) / ds / batch_id)
        d = man["dataset"]
        L += [f"   corpus={d['counts']['corpus']} test_queries={d['test_group_queries']} test_qrels={d['test_group_qrels']} "
              f"validation_queries={man['validation']['queries']} seed={man['seed']}", "   " + hdr]
        for m, v in r["metrics"]["methods"].items():
            L.append(f"   {m:16s} {v['ndcg_at_10']:8.4f} {v['map_at_10']:8.4f} {v['recall_at_5']:7.4f} {v['recall_at_10']:7.4f} "
                     f"{v['recall_at_100']:7.4f} {v['mrr_at_10']:8.4f} {v['precision_at_10']:7.4f}")
        L.append(f"   {'method':16s} {'p50 e2e':>9s} {'p95 e2e':>9s} {'p50 retr':>9s} {'p95 retr':>9s} {'candidates':>11s} {'pool cut':>9s} {'fallback':>9s}")
        for m, v in r["latency"]["methods"].items():
            red = v.get("candidate_pool_reduction_vs_corpus")
            L.append(f"   {m:16s} {v['end_to_end']['p50_ms']:8.2f}ms {v['end_to_end']['p95_ms']:8.2f}ms {v['retrieval']['p50_ms']:8.2f}ms "
                     f"{v['retrieval']['p95_ms']:8.2f}ms {v['mean_eligible_candidates']:11.1f} {'' if red is None else f'{red * 100:8.1f}%':>9s} "
                     f"{v['fallback_rate'] * 100:8.1f}%")
        for c in r["comparisons"] or []:
            b = c["bootstrap"]["ndcg_at_10"]
            L.append(f"   {c['comparison']:22s} ΔnDCG@10 {b['mean_diff']:+.4f} 95% CI [{b['ci95_low']:+.4f}, {b['ci95_high']:+.4f}] "
                     f"(improved {b['queries_improved']}, regressed {b['queries_regressed']}, n={b['n_queries']}) "
                     f"ΔRecall@5 {c['bootstrap']['recall_at_5']['mean_diff']:+.4f}")
        a = r["adaptation"]
        L.append(f"   adaptation: {a['proposals_evaluated']} proposals evaluated, {a['accepted']} accepted, {a['rejected']} rejected, "
                 f"{a['failed']} failed, {a['rolled_back']} rolled back  ({a['initial_version']} → {a['adaptive_version']})")
        L.append("")
    if s["macro_average"]:
        L += [f"== macro-average ({s['macro_note']})", "   " + hdr]
        for m, v in s["macro_average"].items():
            L.append(f"   {m:16s} {v['ndcg_at_10']:8.4f} {v['map_at_10']:8.4f} {v['recall_at_5']:7.4f} {v['recall_at_10']:7.4f} "
                     f"{v['recall_at_100']:7.4f} {v['mrr_at_10']:8.4f} {v['precision_at_10']:7.4f}   ({v['datasets']} datasets)")
    return "\n".join(L)
