# MORPHE — Memory-Oriented Reconfigurable Plasticity and Hierarchical Evolution

An adaptive memory system. Text is stored as searchable memory records, organised into topic **modules**, retrieved
through a **graph-guided** search, and the *architecture itself* — which modules exist and how they are linked — is
restructured from measured usage. A restructuring is only kept if it does not make retrieval worse on a fixed labeled
dataset, and every accepted change is a durable version that can be rolled back across all stores.

```
ingest ─▶ chunk ─▶ local embeddings ─▶ modules (Qdrant) ─▶ graph (NetworkX) ─▶ metadata/events/versions (MongoDB)
query  ─▶ module-profile search ─▶ NetworkX expansion ─▶ filtered vector search ─▶ (global fallback) ─▶ results + trace
every 50 queries ─▶ detect weakness ─▶ plan split / merge / shortcut ─▶ evaluate candidate in a sandbox
                 ─▶ accept or reject by thresholds ─▶ commit + verify ─▶ new version  (or recover / roll back)
```

**No API keys are needed.** Embeddings come from the local `sentence-transformers/all-MiniLM-L6-v2` model and
summaries are deterministic extractive text selection. Nothing leaves your machine.

> **Start here:** [`SETUP.md`](SETUP.md) has step-by-step installation (macOS/Linux and Windows), virtual-environment activation, configuration, running, testing and troubleshooting.

## Project status (read this honestly)

* **Implemented and tested:** ingestion, graph-guided retrieval, split/merge/shortcut adaptation with performance-based validation, checksummed versions with rollback and restore, compression, the dashboard, a deterministic demo,
  and a reproducible **BEIR benchmark harness** built on the real MORPHE services (BM25, global dense, fixed-graph, adaptive-graph) with diagnostics for parity, routing failures and Qdrant performance.
* **What the benchmark found:** on SciFact, NFCorpus and ArguAna the graph hard-filtering path scored **lower nDCG@10 than plain global dense retrieval** (−0.094 / −0.039 / −0.010 on the test split; every paired 95 % interval excludes zero),
  and validated adaptation produced no detectable quality gain. The safety machinery (rejecting a harmful candidate, rolling back a faulty commit, restoring a version) worked on every dataset.
  MORPHE should therefore **not** currently be presented as improving retrieval quality or latency over global dense search; see [`docs/BEIR_RESULTS.md`](docs/BEIR_RESULTS.md).
* **Why (diagnostic analysis, not a fix):** the module whose centroid is closest to a query often is not the module holding the answer, and hierarchy expansion adds little ([`docs/BEIR_ROUTING_FAILURE_ANALYSIS.md`](docs/BEIR_ROUTING_FAILURE_ANALYSIS.md));
  the extra latency comes from Qdrant's local filtered-search implementation, not from the graph logic ([`docs/BEIR_PERFORMANCE_ANALYSIS.md`](docs/BEIR_PERFORMANCE_ANALYSIS.md));
  an apparent live-versus-sandbox mismatch was a defect in the parity check, with a separate tie-handling determinism gap found and fixed ([`docs/BEIR_PARITY_INVESTIGATION.md`](docs/BEIR_PARITY_INVESTIGATION.md)).
* **Alternative retrieval strategies (implemented, evaluated, no reliable benefit):** *progressive graph expansion* (search a few modules, widen only while the top results keep changing, global fallback when unsettled) and *soft graph routing* (global dense search re-ranked by a small bounded module/graph prior), selectable in the Query Playground and comparable side by side. On SciFact, NFCorpus and ArguAna neither beat global dense retrieval in quality or latency, on local or indexed Qdrant; progressive expansion recovers most of the fixed graph's loss mainly by falling back to global search, and soft routing is statistically indistinguishable from dense. Results, protocol and caveats: [`docs/BEIR_RETRIEVAL_ABLATIONS.md`](docs/BEIR_RETRIEVAL_ABLATIONS.md). **Recommendation:** do not claim a retrieval-quality or latency advantage for graph routing.
* **Not done:** combining these strategies with adapted architectures, tuning them beyond the small pre-declared grids, and testing at much larger corpus sizes.

## What is in the box

| Area | What it does |
|---|---|
| Ingestion | normalise → structure-aware chunking → batched local embeddings → module assignment → staged (`pending`) write → activation; identical content is detected; failures leave nothing active |
| Retrieval | four selectable strategies: global dense; fixed graph (module-profile seeds → NetworkX expansion → module-filtered Qdrant search → ranking → global fallback); progressive expansion; soft graph routing. Every query can return a full debug trace |
| Structural adaptation | split overloaded/mixed modules (source/section/KMeans, silhouette-checked), merge truly redundant modules (overlap + conflict checks), add co-access shortcut edges (count thresholds, edge budget) |
| Validation | candidate architecture evaluated in an isolated in-memory sandbox with the **real** routing code on a labeled dataset; Recall@5, MRR, p95 latency, candidate count, fallback rate; configurable accept/reject thresholds |
| Versioning & rollback | checksummed architecture snapshots; one idempotent "converge stores to target state" routine used for apply, recovery and restore across Qdrant payloads + profile vectors, MongoDB documents, and the live graph |
| Compression | token-budget thresholds (75 % / 90 %), exact + near-duplicate removal, extractive summaries with provenance, safe write-verify-retire replacement, protected pinned/critical records, emergency cleanup |
| Dashboard | React + shadcn/ui: Overview, Query Playground (four retrieval strategies with per-strategy traces and a side-by-side comparison), Memory Explorer, Graph Explorer (deterministic hierarchy layout, search and filters, module/edge details with capacity and evidence, real query-route highlighting, module list), Compression Center, Adaptation Center, Metrics & Evaluation, Architecture Versions, Benchmarks (BEIR results and the retrieval-strategy comparison); dark/light; responsive from 320 px |

## Requirements

* Python **3.12** (the tested version), Node.js **22.13+** and npm (Vitest 5 / ESLint 10 need it)
* MongoDB 7/8 Community running on `localhost:27017` (no Docker needed — see below)
* ~1 GB disk for PyTorch + the 90 MB embedding model (downloaded on first start; needs internet **once**)

## Setup

Short version below; the complete guide (Windows, activation, troubleshooting) is [`SETUP.md`](SETUP.md).

```bash
# 1. Python backend
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt          # exact tested versions: backend/requirements.lock.txt
cp backend/.env.example backend/.env                        # every option is documented in that file

# 2. MongoDB — use your own install, or a project-local copy (no system changes):
#    download https://fastdl.mongodb.org/osx/mongodb-macos-arm64-8.0.12.tgz (pick the build for your OS from
#    mongodb.com/try/download/community), unpack it to ./.tools/mongodb, then:
scripts/dev_mongo.sh                                        # uses `mongod` from PATH or ./.tools/mongodb/bin; data in ./data/mongo

# 3. Frontend
cd frontend && npm install && cd ..
```

## Run

```bash
# terminal 1 — MongoDB (skip if it is already running as a service)
scripts/dev_mongo.sh

# terminal 2 — API. Use exactly ONE worker: the local Qdrant folder must be opened by a single process.
cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
#   http://127.0.0.1:8000/docs   OpenAPI      http://127.0.0.1:8000/health   readiness

# terminal 3 — dashboard (proxies /api and /health to :8000)
cd frontend && npm run dev            # http://localhost:5173
```

First start loads the embedding model (≈ 10 s after the initial download). If MongoDB or the model is unavailable the
API still serves `/health` with the reason and the dashboard shows it instead of failing silently.

## Demo (deterministic, reproducible)

The demo never relies on a lucky random sequence. Scenarios create the *conditions*; the adaptation engine still detects,
evaluates and decides on its own.

```bash
.venv/bin/python backend/scripts/demo.py full        # reset → seed → merge → mixed → three 50-query windows → evaluation
```
or step by step (also available as buttons in the **Adaptation** and **Compression** pages):

```bash
.venv/bin/python backend/scripts/demo.py reset                 # removes ONLY MORPHE's Qdrant collections + MongoDB database
.venv/bin/python backend/scripts/demo.py seed                  # 12 topics / 60 memories + baseline version
.venv/bin/python backend/scripts/demo.py eval                  # Recall@5, MRR, p95; global vs fixed-graph vs adaptive-graph
.venv/bin/python backend/scripts/demo.py scenario merge        # redundant PostgreSQL module   → merge proposal
.venv/bin/python backend/scripts/demo.py scenario mixed        # mixed-topic "Python Notes"    → split proposal
.venv/bin/python backend/scripts/demo.py scenario shortcut     # fixed 50-query co-access replay → shortcut proposal
.venv/bin/python backend/scripts/demo.py scenario compression  # budget ≈ 80 %  → normal compression
.venv/bin/python backend/scripts/demo.py scenario emergency    # budget ≈ 93 %  → emergency cleanup
.venv/bin/python backend/scripts/demo.py adapt                 # close the window now (what the 50th query does automatically)
backend/scripts/reset_demo.sh --offline                        # server stopped: delete ./data/qdrant and drop the database
```

Typical output of `demo.py full` on this repository's sample corpus (real model, real MongoDB/Qdrant):

```
window 1: split_module       completed  Accepted: no metric regressed beyond its threshold
window 2: merge_modules       completed  Accepted: no metric regressed beyond its threshold
window 3: add_shortcut_edge   completed  Accepted: no metric regressed beyond its threshold
  global          Recall@5=0.932 MRR=0.915 p95=0.67ms candidates=83.0
  fixed_graph     Recall@5=0.932 MRR=0.915 p95=1.65ms candidates=40.4
  adaptive_graph  Recall@5=0.932 MRR=0.924 p95=1.59ms candidates=38.5
```
Read this honestly: the adaptive graph kept Recall@5 and improved MRR on this dataset while searching fewer records than
global search, but at ~1 ms scale the graph path is *slower* than brute-force global search (extra module search + filter).
Candidate-count narrowing pays off as the corpus grows, not on 80 chunks. See `docs/EVALUATION_AND_ROLLBACK.md`.

## BEIR benchmark (retrieval quality, graph routing, adaptation safety)

An evaluation harness built on the real MORPHE services compares **BM25**, **global dense** (MiniLM), **fixed-graph** and **adaptive-graph** retrieval on official
BEIR datasets (SciFact, NFCorpus, ArguAna; any other BEIR id by name) with BEIR's own metrics (nDCG@10 headline, MAP@10, Recall@5/10/100, MRR@10, Precision@10),
latency/candidate-pool measurements, paired bootstrap intervals, and a leakage-free validation protocol for structural changes. **Read `docs/BEIR_RESULTS.md` first** —
the headline finding is that graph-guided routing *lost* retrieval quality against global search on all three datasets and that validated adaptation produced no detectable gain.

Headline test-split nDCG@10 (ArguAna is an **internal** split of the official queries, not comparable to published scores; SciFact's v2 numbers are a post-hoc re-run, see the chronology in the docs):

| | SciFact | NFCorpus | ArguAna (internal) |
|---|---|---|---|
| BM25 (local Okapi) | 0.663 | 0.306 | 0.456 |
| Global dense | 0.645 | 0.316 | 0.507 |
| Fixed graph | 0.551 | 0.277 | 0.497 |
| Adaptive graph | 0.551 | 0.275 | 0.496 |

The graph paths shrink the eligible candidate pool by 77–89 % but are *slower* than global search on the local Qdrant backend; with an indexed Qdrant server every search takes about 1 ms and graph retrieval is still marginally slower. Only split proposals were
ever generated on BEIR data, so merge and shortcut adaptation are untested there. These are single-seed results with one embedding model.

```bash
cd backend      # needs MongoDB running (scripts/dev_mongo.sh); first run downloads each dataset (~3-4 MB zip) and embeds it (15-40 s on Apple MPS)
../.venv/bin/python -m app.evaluation.run_beir --datasets scifact --methods global_dense,fixed_graph,adaptive_graph --query-limit 20   # smoke, marked non-official
../.venv/bin/python -m app.evaluation.run_beir --datasets scifact,nfcorpus,arguana --methods bm25,global_dense,fixed_graph,adaptive_graph --split test --download
../.venv/bin/python -m app.evaluation.report_beir --latest                       # tables, comparisons, macro-average

# follow-up diagnostics (analyses written to backend/artifacts/beir_phase2/; use a fresh --run-id every time)
../.venv/bin/python -m app.evaluation.run_parity --datasets scifact,nfcorpus,arguana --run-id my_parity_01      # live vs sandbox engine, per query
../.venv/bin/python -m app.evaluation.routing_diagnostics --datasets scifact,nfcorpus,arguana --run-id my_diag_01   # why routing loses documents (validation group only)
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id my_strat_01 --phase tune      # progressive expansion + soft routing: tune on validation, freeze,
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id my_strat_01 --phase evaluate  # ...then evaluate (refuses without an intact frozen config)
python3 ../scripts/verify_baseline_hashes.py                                    # check the frozen baselines in the repository are untouched
```
Results are saved under `backend/artifacts/beir/<dataset>/<run_id>/` (manifest, metrics, per-query metrics, latency, comparisons, adaptation events, safety probes, TREC run files) and shown on the dashboard page **Benchmarks**
(start runs, watch job status, compare methods, inspect adaptation evidence, export JSON/CSV). Downloaded datasets and cached embeddings (`data/beir/`) and *new* run output (`backend/artifacts/`) are git-ignored; the **frozen baseline results**
(v1/v2 runs and the final phase-2 diagnostic runs, about 60 MB) are committed so the evidence can be verified, together with `docs/baselines/beir_v1_v2_freeze.json` (SHA-256 of every baseline file and source) and a source snapshot in `backend/_baseline_src_v2/`. `--query-limit` makes a run a clearly-marked smoke run; ArguAna has no train/dev qrels so its numbers come from an
internal split. Methodology: `docs/BEIR_BENCHMARK_METHODOLOGY.md`; plan and protocol-v2 chronology: `docs/BEIR_BENCHMARK_PLAN.md`; results: `docs/BEIR_RESULTS.md`.

## Tests

```bash
cd backend  && ../.venv/bin/python -m pytest                    # 186 backend tests (real algorithms; fake embedder, mongomock, in-memory Qdrant)
cd frontend && npm test && npm run lint && npm run typecheck    # 70 component/interaction tests, ESLint, strict TypeScript
cd frontend && npm run e2e                                      # 60 Playwright tests against the RUNNING real stack
                                                                # (needs MongoDB + API on :8000; resets and re-seeds the demo data)
```
Demo helpers: `python backend/scripts/demo.py strategies` runs all four strategies over every labeled query on the live architecture and lists the instructive ones; the complete live walkthrough is in [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md).
Optionally verify the evaluation labels still match the sample files: `.venv/bin/python backend/scripts/build_eval_labels.py --check`.

## Repository layout

```
backend/app/        FastAPI app (api/ ingestion/ retrieval/ graph/ adaptation/ evaluation/ versioning/ compression/
                    memory/ storage/ monitoring/), coordinator.py (shared mutation coordinator), reconcile.py (startup repair)
backend/tests/      pytest suites          backend/scripts/   demo CLI, label builder
backend/app/benchmark/   BEIR harness (datasets, splits, BM25, methods, runner, report, API services)
backend/app/retrieval/   engine, graph selector, ranker and strategies.py (progressive expansion, soft graph routing)
backend/app/evaluation/  evaluator, acceptance policy, BEIR CLIs, parity / routing-diagnostic / Qdrant-performance tools
backend/_baseline_src_v2/  read-only snapshot of the source that produced the frozen v1/v2 results (do not edit)
backend/artifacts/beir*/   frozen baseline results (committed); new runs are written here and ignored
backend/artifacts/beir_strategies/<run>/   retrieval-strategy comparison (frozen config, validation sweeps, validation + test results)
frontend/src/       pages/ components/(ui = shadcn, app = MORPHE) api/ hooks/ layouts/ test/      frontend/e2e/  Playwright
data/samples/       12 core + merge/mixed/compression documents and manifest.json
data/evaluation_queries.json   59 labeled queries (+ evaluation_spec.json source, demo_replay_queries.json)
docs/               ARCHITECTURE · API · ADAPTATION_RULES · EVALUATION_AND_ROLLBACK · IMPLEMENTATION_DECISIONS
                    BEIR_BENCHMARK_PLAN · BEIR_BENCHMARK_METHODOLOGY · BEIR_RESULTS · BEIR_PARITY_INVESTIGATION
                    BEIR_ROUTING_FAILURE_ANALYSIS · BEIR_PERFORMANCE_ANALYSIS · BEIR_RETRIEVAL_ABLATIONS · DEMO_RUNBOOK · baselines/ (freeze manifest)
scripts/            dev_mongo.sh (project-local MongoDB) · verify_baseline_hashes.py
SETUP.md            installation, activation, configuration, running, testing, troubleshooting
```

## Common problems

| Symptom | Cause / fix |
|---|---|
| Dashboard banner "Backend unreachable" | start the API (`uvicorn …` above); the Vite proxy targets `http://127.0.0.1:8000` (`VITE_PROXY_TARGET` overrides) |
| `/health` 503 `mongodb_unavailable` | start MongoDB (`scripts/dev_mongo.sh`) or set `MONGODB_URI` |
| `qdrant_unavailable … already accessed by another instance` | a second process holds `data/qdrant`; run one API worker only, and use the demo scripts through the API (they do) |
| `embedding_model_unavailable` | first start needs internet to download the model once; MORPHE never silently substitutes another model |
| `dimension_mismatch` | `EMBEDDING_DIMENSION` ≠ model/collection size; reset the data or change the collection name |
| Evaluation says "Not enough data yet" | seed the corpus (`demo.py seed`) so the labeled memories exist; at least `MIN_EVAL_QUERIES` must be usable |

## Known limitations

* Local Qdrant is a single-process, brute-force store; for multi-process serving switch `QDRANT_MODE=server`.
* The token budget is **active-text tokens** — it is not RAM or disk. Process RSS and Qdrant disk size are shown separately and
  superseded records stay on disk (only active text counts toward the budget).
* Acceptance protects *measured* quality on a small, human-authored benchmark (59 queries). It is evidence, not proof, of
  improvement on your data; replace `data/evaluation_queries.json` with queries for your own corpus.
* Summaries are extractive (sentence selection), so they are less fluent than a generative model; a `SummaryProvider`
  interface exists for adding one.
* No authentication, multi-tenancy or distributed workers (out of scope for this MVP).
