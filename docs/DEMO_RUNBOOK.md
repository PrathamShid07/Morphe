# MORPHE live demo runbook

About 12 minutes. Every number below was observed on the real stack on 2026-10-25 and is reproducible from the starting state; nothing in the demo is scripted or mocked. If a number on screen differs slightly (latencies always do), say so rather than hiding it.

**The honest storyline to tell:** MORPHE organises memory into a versioned, rollback-safe module graph. We built three ways to use that graph for retrieval and measured them against plain global dense search on real BEIR benchmarks. The graph makes the system *inspectable and safe to change*; the measurements say it does **not** (yet) make retrieval better or faster, and we show exactly why. Do not claim otherwise.

## 0. Before the demo (10 minutes ahead)

Everything runs locally; no internet is needed once the embedding model is cached and the saved benchmark results exist.

```bash
# terminal 1 — MongoDB (skip if it already runs)
scripts/dev_mongo.sh
# terminal 2 — API (exactly one worker)
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# terminal 3 — dashboard
cd frontend && npm run dev            # http://localhost:5173
```

Checks (all must pass; otherwise see the table at the end):

```bash
curl -s http://127.0.0.1:8000/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('ready:', d['ready'], d['checks']['qdrant'])"
curl -s http://127.0.0.1:8000/api/v1/benchmarks/strategies/runs | python3 -c "import json,sys; print([r['run_id'] for r in json.load(sys.stdin)['items']])"   # expect ['strat_01']
python3 scripts/verify_baseline_hashes.py                      # frozen baselines untouched
```

Create the administrator account once (`.venv/bin/python backend/scripts/admin_credentials.py init`, restart the API) and export `MORPHE_ADMIN_PASSWORD` in the terminal that runs `demo.py`; the demo script signs in with it. In the browser press **Admin Login** (the demo begins in Admin Mode; end it by switching to **User Mode** to show what an ordinary visitor sees).

Start from a clean, deterministic demo state (this deletes **only** MORPHE's own demo collections and database, nothing else):

```bash
.venv/bin/python backend/scripts/demo.py reset
.venv/bin/python backend/scripts/demo.py seed
.venv/bin/python backend/scripts/demo.py status
# expect: architecture arch_0002 | memories 60 | modules 12 | edges 12
```

Warm the model once so the first live query is fast: open **Query Playground** and run any query. Use the light theme for projectors (toggle bottom-left). Keep the browser at ≥ 1280 px wide; the app is also responsive down to 320 px if you show a phone.

## 1. Overview (30 s)

Open **Overview**. Point at: 60 active memories, 12 content modules, 12 graph edges, architecture version `arch_0002`. "Memories live in modules; modules are linked in a graph; every structural change is a checksummed version."

## 2. Graph Explorer (1.5 min)

Open **Graph Explorer**.
* The graph is laid out deterministically from the real hierarchy: five groups (Devops, Web Frameworks, Data Stores, Frontend, Machine Learning) with their modules.
* Type `django` in **Search modules** → the matches are highlighted and the counter says how many match; clear it.
* Click **Django Orm**: the camera zooms to its neighbourhood and the details panel opens. Point at *Capacity* (5 / 1,000 records: the split detector's threshold, not a storage limit), parent, retrieval activity, incoming/outgoing relationships, related memories, architecture version.
* Toggle **Hierarchy** / **Shortcuts**, change **All sizes**, switch to **Module list** (the full alternative view with relationships and a “Show in graph” button) and back.
* There are no co-access shortcut edges yet in the seeded state; say so. (Run `demo.py scenario shortcut` + `demo.py adapt` later if you want to show one with its recorded evidence.)

## 3. Global dense vs fixed graph on one query (2 min)

Open **Query Playground**, type: `how do I deploy a python web app with docker and nginx`.

1. Retrieval **Global dense** → Run. Note the top 5 (two Docker memories, one **Django Routing** memory “Including other URLconfs”, one Nginx, one Kubernetes) and the trace: all 60 memories examined.
2. Retrieval **Fixed graph** → Run. The trace shows seeds Docker, Nginx, Kubernetes, then hierarchy expansion adds Git: **20 of 60 records examined, graph-guided only**. The Django Routing memory is no longer in the results.

## 4. What did fixed routing exclude? (1 min)

Still on this query, open the tab **Compare all four** → *The text typed above* → **Compare four strategies**. Open the **Fixed graph** tab: it lists the 4 modules it searched; the Django Routing memory (global rank 3) is in a module it never searched.
Be precise: this query has no relevance labels, so we can say the *results differ*, not which is “right”. The corpus is small and clean: across all 53 labeled demo queries every strategy scores the same (mean nDCG@10 0.905), so there is no labeled failure to show live. The labeled evidence is on the Benchmarks page (step 8). Click **Show route on graph** on the Fixed graph tab to see the four searched modules highlighted.

## 5. Progressive expansion (1.5 min)

Back in **Single run** choose **Progressive expansion** → Run. The stage cards show:
* Stage 1: Docker, Nginx, Kubernetes (15 records).
* Stage 2: Django Routing, Fastapi, Django Orm → the top 5 **changed**, so it continues.
* Stage 3: Postgresql, Git, Scikit Learn → top 5 **unchanged** → stop. **45 of 60 records, no global fallback**, and the Django Routing memory is back in the results.
Explain the stopping rule: query-time only (did the top results stop changing?), never relevance labels; budget exhaustion with an unsettled top-k triggers a global fallback. Click **Show route on graph** (or use **Graph Explorer → Trace a query**): seeds are purple with “stage 1”, expanded modules blue with “stage 2/3”, modules holding returned memories carry a “returned” badge, everything else fades.

## 6. Soft graph routing (1 min)

Choose **Soft graph routing** → Run. The table shows, for every result, the dense rank (“was 2”), the module rank, graph distance, prior, a tiny **score shift bar** (bounded by ±λ = 0.02) and the final rank. Say: “it searches everything and lets the graph nudge the order slightly — the prior is not a relevance label.” Here the order barely changes, which is the point of the result in step 8.

## 7. Side by side (1 min)

**Compare all four** → results table. Today's numbers on this query (records examined / latency): global 60 / ~0.4 ms, fixed graph 20 / ~0.9 ms, progressive 45 / ~2.2 ms, soft 60 / ~1.2 ms. “On 60 memories a brute-force scan costs less than the routing; the candidate reduction does not buy speed.”
Other queries with a visible fixed-graph difference: `caching strategy for a web app`, `scaling a database and a cache together`, `redis as a django cache backend`, `state management and caching in the frontend`.

## 8. Benchmarks → Retrieval strategies (2.5 min)

Open **Benchmarks → Retrieval strategies** (run `strat_01`; real SciFact / NFCorpus / ArguAna results, nothing recomputed).
* **Status badge**: SciFact test is *exploratory* (labels seen under the first protocol); NFCorpus *confirmatory*; ArguAna an *internal partition*. Switch dataset and between Validation and Test.
* **Quality** (SciFact, test): global dense nDCG@10 0.6451, fixed graph 0.5507, progressive 0.6324, soft 0.6429 (BM25 0.6634 shown as context).
* **Paired comparison**: fixed − global −0.0944 [−0.127, −0.063] (reliable loss); progressive − global −0.0127 [−0.026, −0.003] (reliable but small loss); soft − global −0.0021 [−0.005, +0.001] (not distinguishable from zero).
* **Efficiency**: progressive falls back to global search for 41 % of SciFact queries, so it examines 3,375 of 5,183 records on average; fixed graph examines 886. The **indexed-server table** shows global ≈ 1.0 ms, fixed ≈ 1.8, soft ≈ 1.8, progressive ≈ 5.1 ms: no strategy is faster than global.
* **Routing failure analysis**: the fixed graph left 66 relevant documents in modules it never searched (64 queries); progressive 15; soft 0 by construction.
* The **“What these numbers say”** box is generated from the saved results.

## 9. Why a strategy succeeds or fails (1 min)

In the **Per-query inspector** choose *Progressive expansion vs Global dense* → *Degraded only* → open the worst query: the table shows each strategy's nDCG@10, records examined, how many relevant documents sat in unsearched modules, and stages/stop reason. Say: progressive expansion recovers quality largely because the queries that fall back are identical to global; the ones that don't still lose. Details: `docs/BEIR_RETRIEVAL_ABLATIONS.md`.
Optionally expand **Frozen configuration and how it was chosen**: the configuration was chosen on the validation group only, frozen with a hash, and the test group was evaluated once afterwards; no progressive configuration was non-inferior to global dense.

## 10. Versions and rollback (1.5 min)

Create a validated structural change with the real adaptation engine, then roll it back:

```bash
.venv/bin/python backend/scripts/demo.py scenario merge     # ingests a redundant PostgreSQL notes module
.venv/bin/python backend/scripts/demo.py adapt              # detect -> evaluate in a sandbox -> accept: "merge_modules completed"
```
Open **Versions**: `arch_0004` is active (the merge), `arch_0003` is the state before it, `arch_0002` the seeded baseline, each checksummed. Open `arch_0003` → **Restore this version…** → read the expected changes (+1 module “PostgreSQL Tuning Notes”, 5 memories reassigned) → tick the acknowledgement → **Restore**. A restore is itself a new version (`arch_0005`, restored from `arch_0003`); integrity checks pass, and the Graph Explorer now shows 18 nodes. Run a query to show retrieval works on the restored architecture. Mention that a harmful candidate is rejected before commit and a fault after commit rolls back automatically (the benchmark's safety evidence, `docs/EVALUATION_AND_ROLLBACK.md`).

**Reset to the starting state afterwards** (only disposable demo data): `demo.py reset` then `demo.py seed`.

## What not to say

* Not “graph routing improves retrieval” or “is faster”: the evidence says neither.
* Not that the BEIR numbers are official leaderboard scores (ArguAna is an internal split; SciFact is exploratory).
* Not that the live demo corpus proves anything about quality: it is 60 clean memories where every strategy ties.

## If something goes wrong

| Symptom | What to do |
|---|---|
| Dashboard says backend unreachable | start the API (terminal 2); the proxy targets port 8000 |
| `/health` 503 `mongodb_unavailable` | start MongoDB (`scripts/dev_mongo.sh`) |
| `qdrant_unavailable … already accessed by another instance` | another process holds `data/qdrant`: stop the other API/worker (exactly one) |
| First query takes ~10 s | the embedding model is loading; run one query before the audience arrives |
| Benchmarks → Retrieval strategies is empty | `backend/artifacts/beir_strategies/strat_01/` is missing; the saved results are not committed to Git by default, so restore them or re-run `run_strategies` (about 3 h) |
| Indexed-server table says “not measured” | restart the API (it was started before `server_latency.json` existed) |
| A demo query behaves differently | reset and seed again (`demo.py reset`, `demo.py seed`); never edit results to match the script |
