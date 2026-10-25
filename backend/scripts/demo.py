#!/usr/bin/env python
"""Drive the deterministic MORPHE demo through the HTTP API (the server owns the local Qdrant lock, so scripts must
not open it directly).

    python backend/scripts/demo.py status
    python backend/scripts/demo.py reset            # wipes ONLY MORPHE's Qdrant collections + MongoDB database
    python backend/scripts/demo.py seed             # core corpus + baseline architecture version
    python backend/scripts/demo.py scenario merge   # merge | mixed | shortcut | compression | emergency
    python backend/scripts/demo.py replay           # deterministic 50-query co-access replay
    python backend/scripts/demo.py adapt            # close the window now: detect -> evaluate -> apply (1 op)
    python backend/scripts/demo.py eval             # Recall@5 / MRR / p95 + global vs fixed vs adaptive comparison
    python backend/scripts/demo.py strategies       # global vs fixed vs progressive vs soft on every labeled query; lists the instructive ones
    python backend/scripts/demo.py strategies <id>  # the same comparison for one labeled query id (e.g. the one to show live)
    python backend/scripts/demo.py full             # reset + seed + merge + mixed + replay + adapt x3 + eval
    python backend/scripts/demo.py reset --offline  # server stopped: delete data/qdrant and drop the MongoDB database

The API is administrator-only, so every command except `reset --offline` first signs in as the administrator. Provide the credentials with
MORPHE_ADMIN_USERNAME (default morphe-admin) and MORPHE_ADMIN_PASSWORD; if the password variable is unset you are prompted for it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("MORPHE_API", "http://127.0.0.1:8000")
P = f"{BASE}/api/v1"
c = httpx.Client(timeout=300)


def sign_in() -> None:
    """Authenticate the script as the administrator (cookie session + CSRF header, exactly like the dashboard)."""
    import getpass
    user = os.environ.get("MORPHE_ADMIN_USERNAME", "morphe-admin")
    password = os.environ.get("MORPHE_ADMIN_PASSWORD") or getpass.getpass(f"MORPHE administrator password for {user}: ")
    r = c.post(f"{P}/auth/admin/login", json={"username": user, "password": password})
    if r.status_code != 200:
        print(f"! administrator sign-in failed ({r.status_code}): {r.json().get('error', {}).get('message', r.text[:200])}", file=sys.stderr)
        raise SystemExit(1)
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]


def call(method: str, path: str, **kw):
    r = c.request(method, f"{P}{path}", **kw)
    if r.status_code >= 400:
        print(f"! {method} {path} -> {r.status_code}: {r.text[:600]}", file=sys.stderr)
        raise SystemExit(1)
    return r.json()


def show(title: str, obj) -> None:
    print(f"\n== {title}")
    print(json.dumps(obj, indent=2, default=str)[:2500])


def status() -> None:
    m = call("GET", "/metrics/summary")
    a = call("GET", "/adaptation/status")
    cp = call("GET", "/compression/status")
    print(f"architecture {m['architecture_version']} | memories {m['active_memory_count']} | modules {m['module_count']} | "
          f"edges {m['edge_count']} | tokens {m['active_tokens']}/{m['budget_tokens']} ({m['usage_percent']}%)")
    print(f"adaptation window {a['queries_in_window']}/{a['queries_per_window']} | compression {cp['state']} ({cp['band']})")
    q = m["retrieval_quality"]
    print("quality:", m["retrieval_quality_note"] if not q else
          f"Recall@5={q['metrics']['recall_at_5']:.3f} MRR={q['metrics']['mrr']:.3f} n={q['sample_size']}")


def reset(offline: bool) -> None:
    if offline:
        print("Removing: ./data/qdrant (local vector store) and the MongoDB database 'morphe'. Nothing else is touched.")
        shutil.rmtree(ROOT / "data" / "qdrant", ignore_errors=True)
        from pymongo import MongoClient
        MongoClient(os.environ.get("MONGODB_URI", "mongodb://localhost:27017"), serverSelectionTimeoutMS=3000).drop_database(
            os.environ.get("MONGODB_DATABASE", "morphe"))
        print("done")
        return
    print("Removing MORPHE's Qdrant collections and MongoDB database (nothing else is touched).")
    show("reset", call("POST", "/admin/reset", json={"confirm": "RESET"}))


def evaluate() -> None:
    rep = call("POST", "/evaluation/run", json={"mode": "graph"})
    m = rep["metrics"]
    print(f"\n== evaluation (live) valid={rep['valid']} n={m['sample_size']} Recall@5={m['recall_at_5']:.3f} MRR={m['mrr']:.3f} "
          f"p95={m['p95_latency_ms']:.2f}ms candidates={m['mean_candidate_count']:.1f} fallback={m['fallback_rate']:.2f}")
    for mode in ("progressive", "soft"):  # the two experimental strategies on the same live architecture and labels
        r = call("POST", "/evaluation/run", json={"mode": mode})["metrics"]
        print(f"  {mode:11s} Recall@5={r['recall_at_5']:.3f} MRR={r['mrr']:.3f} nDCG@10={r['ndcg_at_10']:.3f} p95={r['p95_latency_ms']:.2f}ms candidates={r['mean_candidate_count']:.1f} fallback={r['fallback_rate']:.2f}")
    cmp = call("POST", "/evaluation/compare-modes")
    print("\n== global vs fixed graph vs adaptive graph (same dataset, same memories)")
    for k, v in cmp["modes"].items():
        mm = v["metrics"]
        print(f"  {k:15s} Recall@5={mm['recall_at_5']:.3f} MRR={mm['mrr']:.3f} p95={mm['p95_latency_ms']:.2f}ms "
              f"candidates={mm['mean_candidate_count']:.1f}")


STRATS = ("global", "graph", "progressive", "soft")


def strategies(query_id: str | None) -> None:
    """Run the four retrieval strategies on the labeled evaluation queries of the LIVE architecture (nothing is tracked or changed)."""
    ds = call("GET", "/evaluation/dataset")
    qs = [q for q in ds.get("sample", []) if not query_id or q["query_id"] == query_id]
    if not qs:
        print(f"! no labeled query {query_id!r}; known ids start with: {[q['query_id'] for q in ds.get('sample', [])[:5]]}", file=sys.stderr)
        raise SystemExit(1)
    rows, tot = [], {k: {"ndcg": 0.0, "excl": 0, "n": 0, "cand": 0.0, "fb": 0, "ms": 0.0} for k in STRATS}
    for q in qs:
        r = call("POST", "/evaluation/compare-strategies", json={"query_id": q["query_id"], "top_k": 5})
        if not r["labeled"]:
            continue
        row = {"id": q["query_id"], "q": q["query"]}
        for k in STRATS:
            e = r["strategies"][k]
            row[k] = (e["metrics"]["ndcg_at_10"], len(e["relevant_excluded_by_routing"]), e["candidate_count"], e["fallback_used"])
            t = tot[k]
            t["ndcg"] += e["metrics"]["ndcg_at_10"]; t["excl"] += len(e["relevant_excluded_by_routing"]); t["n"] += 1
            t["cand"] += e["candidate_count"]; t["fb"] += int(e["fallback_used"]); t["ms"] += e["latency_ms"]
        rows.append(row)
    if query_id:
        row = rows[0]
        print(f"\n{row['id']}: {row['q']}")
        for k in STRATS:
            n, ex, cand, fb = row[k]
            print(f"  {k:12s} nDCG@10={n:.3f} relevant-excluded-by-routing={ex} records-examined={cand} fallback={fb}")
        return
    print(f"\n== {len(rows)} labeled queries, top_k=5, live architecture")
    for k in STRATS:
        t = tot[k]
        print(f"  {k:12s} mean nDCG@10={t['ndcg'] / t['n']:.3f}  relevant docs excluded by routing={t['excl']:3d}  mean records examined={t['cand'] / t['n']:.1f}  "
              f"fallback={t['fb']}/{t['n']}  mean latency={t['ms'] / t['n']:.2f} ms")
    inst = sorted((r for r in rows if r["graph"][1] > 0 or abs(r["progressive"][0] - r["global"][0]) > 1e-9 or abs(r["soft"][0] - r["global"][0]) > 1e-9),
                  key=lambda r: (-(r["graph"][1]), r["id"]))[:8]
    print("\n== instructive queries (fixed graph excluded a relevant module, or a strategy differs from global dense)")
    for r in inst:
        print(f"  {r['id']:28s} " + "  ".join(f"{k}={r[k][0]:.2f}" for k in STRATS) + f"  excluded(graph)={r['graph'][1]}  | {r['q'][:70]}")
    if not inst:
        print("  none: on this small, clean demo corpus every strategy returns the same relevant memories. Use the BEIR benchmark page for the evidence.")


def wait_idle(timeout: float = 180) -> None:
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not call("GET", "/adaptation/status")["running"]:
            return
        time.sleep(1)
    print("! adaptation still running after timeout", file=sys.stderr)


def report_window() -> None:
    res = call("GET", "/adaptation/status")["last_window_run"]
    if not res:
        print("  (no window has been processed yet)")
        return
    print(f"  window {res['window_id']}: {res['queries']} queries, {res['proposals_detected']} proposals, applied {res['applied']}")
    for e in res["evaluated"]:
        ev = call("GET", f"/adaptation/events/{e['event_id']}")
        print(f"    {ev['operation']:20s} {ev['status']:11s} {ev.get('decision_reason') or ev.get('error')}")
    mon = res.get("monitoring") or {}
    print(f"    post-change monitoring: {mon.get('status')}")


def adapt() -> None:
    wait_idle()
    res = call("POST", "/adaptation/run-window")
    print(f"window: {res['queries']} queries, {res['proposals_detected']} proposals, applied {res['applied']}")
    for e in res["evaluated"]:
        ev = call("GET", f"/adaptation/events/{e['event_id']}")
        print(f"  {ev['operation']:20s} {ev['status']:11s} {ev.get('decision_reason') or ev.get('error')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "reset", "seed", "scenario", "replay", "adapt", "eval", "strategies", "full"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if not (a.cmd == "reset" and a.offline):
        sign_in()
    if a.cmd == "status":
        status()
    elif a.cmd == "reset":
        reset(a.offline)
    elif a.cmd == "seed":
        show("seed", call("POST", "/admin/demo/seed"))
    elif a.cmd == "scenario":
        if not a.name:
            show("scenarios", call("GET", "/admin/demo/scenarios"))
        else:
            show(f"scenario {a.name}", call("POST", f"/admin/demo/scenario/{a.name}"))
    elif a.cmd == "replay":
        show("replay", call("POST", "/admin/demo/scenario/shortcut"))
    elif a.cmd == "adapt":
        adapt()
    elif a.cmd == "eval":
        evaluate()
    elif a.cmd == "strategies":
        strategies(a.name)
    elif a.cmd == "full":
        reset(False)
        call("POST", "/admin/demo/seed")
        status()
        evaluate()
        call("POST", "/admin/demo/scenario/merge")
        call("POST", "/admin/demo/scenario/mixed")
        print("\n== three 50-query windows; each window applies at most ONE validated operation")
        for i in range(3):
            call("POST", "/admin/demo/scenario/shortcut")
            wait_idle()
            print(f"window {i + 1}:")
            report_window()
        status()
        evaluate()


if __name__ == "__main__":
    main()
