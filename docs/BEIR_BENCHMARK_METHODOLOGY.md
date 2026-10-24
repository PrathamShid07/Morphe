# BEIR benchmark methodology

How the benchmark in `backend/app/benchmark/` works, what each number means, and what it cannot tell you. The plan that preceded the
implementation (and the protocol-v2 addendum with its chronology) is `BEIR_BENCHMARK_PLAN.md`; findings are in `BEIR_RESULTS.md`.

## 1. What is compared and how it reuses MORPHE

| Method | Implementation | Shares with the others |
|---|---|---|
| **BM25** | `benchmark/bm25.py` — local Okapi BM25 | corpus ids, queries, qrels, cutoffs |
| **Global dense** | MORPHE `RetrievalEngine.retrieve(mode="global")` | embeddings, preprocessing, score semantics (cosine), engine code |
| **Fixed graph** | `RetrievalEngine.retrieve(mode="graph")` on the initial architecture | everything above |
| **Adaptive graph** | the same engine on the architecture produced by MORPHE's real adaptation engine | everything above |

There is no second retrieval pipeline. The benchmark ingests the corpus into an isolated MORPHE context (its own in-memory Qdrant store, its own MongoDB
database `morphe_beir_<dataset>_<run>`, its own graph) using the production persistence layer (`ArchitecturePersistence.converge_to`) and version store.
Adaptation uses `AdaptationService.run_window`/`process`, `Evaluator`, `AcceptancePolicy`, `ArchitectureLifecycle` unchanged except for the *additive* extensions listed in §9.

## 2. Datasets (official BEIR files)

Loaded with `beir.datasets.data_loader.GenericDataLoader` from `data/beir/<dataset>/` (git-ignored). A dataset that is not cached raises
`DatasetNotAvailable` with an explicit message; `--download` fetches the official archive with `beir.util.download_and_unzip` (public BEIR host). Nothing is ever substituted.

| Dataset | Corpus | Official qrels files | Notes |
|---|---|---|---|
| SciFact | 5,183 | train (809 q / 919 rows), test (300 q / 339 rows) | binary judgments; no dev split |
| NFCorpus | 3,633 | train (2,590 q), dev (324), test (323 q / 12,334 rows) | **graded** judgments (1, 2) in dev/test |
| ArguAna | 8,674 | test (1,406 q / 1,406 rows) | single split; 5 judgments point at documents absent from the official corpus |

Validation (`datasets.validate_beir`): non-empty corpus and queries, unique document ids (line count vs ids), documents with neither title nor text, qrels query ids present in
the query file, integer non-negative grades, every judged query has ≥ 1 relevant document. Judgments for documents missing from the corpus are a **recorded caveat, not an error** — BEIR's own
evaluation keeps them (they lower every method's recall equally) — and they appear in the manifest's `caveats` and on the dashboard. Licence notes in the manifest come from BEIR's dataset table; verify before redistributing.

Offline guarantee: the application and all automated tests need no network. Tests use a synthetic dataset written in the official on-disk format (`tests/beir_fixture.py`).

## 3. Corpus → MORPHE

* Text = `title + " " + text` (BEIR/SBERT convention); missing title or text is handled. `all-MiniLM-L6-v2` truncates at 256 wordpieces — long abstracts (notably ArguAna, NFCorpus) are truncated *identically for every dense method*.
* One BEIR document = one MORPHE memory (no chunking), so retrieval results are document ids. `memory_id = mem_beir_<dataset>_<sha1(doc_id)[:14]>`; `DocMap` is verified collision-free, and `verify_mapping`
  checks that every Qdrant payload carries exactly the original id in `beir_doc_id` (plus `dataset_id`, `title`, `kind="document"`, `status`, `module_id`, `source_id`).
* Embeddings are computed once in batches and cached in `data/beir/embeddings/<dataset>/<key>/corpus.npy`; the key hashes (model, dimension, preprocessing id, corpus SHA-256), so any change invalidates it. Dimension and finiteness are checked.
* Query embeddings for the test group are produced one query at a time (timed) and reused by all dense methods.

## 4. Query groups and leakage prevention

Three disjoint groups, built deterministically from a seed (13): **workload** (queries only — never their judgments — replayed through the *live* retrieval path to create access/co-access events),
**validation** (judgments used only to evaluate candidate architectures), **test** (frozen; evaluated once, after the architecture is frozen).

| Dataset | workload | validation | test | official? |
|---|---|---|---|---|
| SciFact | 50 % of official **train** queries | other 50 % of train | official **test** (300) | yes |
| NFCorpus | seeded ≤ 500 of official train | official **dev** | official **test** (323) | yes |
| ArguAna | 30 % of the single split | 20 % | remaining 50 % | **no — internal split** |

Safeguards: groups are disjoint by id *and* by exact query text; a train/dev query whose text equals a test query is dropped (ArguAna contains many duplicated texts), and a workload query equal to a validation query is dropped.
`--query-limit N` caps the test and validation groups and marks the run `smoke`/non-official. The initial graph and the routing configuration never use any query or judgment; final test judgments are never read before the architecture is frozen
(`group_qrels(…, "workload")` raises by construction). Everything is recorded in the manifest (`split_protocol`).

## 5. Initial graph

`corpus.build_initial_architecture`: KMeans over document embeddings (`k = max(8, round(N/100))`, `random_state=13`, `n_init=10`); clusters smaller than 10 documents are folded into the nearest cluster; module profile = L2-normalised centroid;
`round(√k)` group *containers* from KMeans over module centroids provide the hierarchy (parent→child edges); no co-access edges at initialisation. Same graph for fixed and adaptive.

## 6. Metrics

**Standard BEIR metrics** (BEIR `EvaluateRetrieval.evaluate`, i.e. pytrec_eval, cutoffs 1/3/5/10/100; MRR via `evaluate_custom(metric="mrr")`):
**nDCG@10 (headline)**, MAP@10, Recall@5/10/100, MRR@10, Precision@10. Graded judgments are passed unchanged (linear gain). BEIR defaults apply: a document whose id equals the query id is ignored; BEIR rounds to 5 decimals.
Per-query values come from pytrec_eval with the same measures (needed for bootstrap/inspection); every run asserts that the mean of per-query values equals BEIR's aggregate to 1e-4. A run that omits any judged query raises
(pytrec_eval would otherwise silently skip it and inflate scores).

**MORPHE-specific measurements** (not BEIR metrics): eligible candidate documents, candidate-pool reduction, fallback rate, per-stage latencies, graph size, version ids.
*Eligible candidates* = active documents inside the searched modules (plus the whole corpus when the global fallback also ran). It is **not** the number of vectors the engine examined, and not the number returned.
During candidate validation the evaluator also reports Recall@5 / MRR@5 / MRR@10 / nDCG@10 with MORPHE's own `ranking_metrics`, which are unit-tested against pytrec_eval.

## 7. Latency and resources

`time.perf_counter()`. Models, indexes and sandbox stores are built before timing. Query embedding is timed separately (batch size 1, after a warm-up) and **end-to-end = embedding + retrieval** so routing overhead is never hidden.
Each (method, query) retrieval is warmed up once, then repeated 3 times; the per-query value is the median (BM25: minimum). Reported: p50/p95/mean of retrieval and end-to-end, embedding p50/p95, and mean seed-selection / expansion / Qdrant-search / fallback stage times.
Methods run sequentially on one machine; **cold-start costs** (model load, corpus embedding, graph build, ingestion, adaptation, evaluation) are reported separately under `latency.json → system.timings_s`. Process RSS is sampled; the benchmark Qdrant store is in-memory
(so there is no meaningful on-disk size — the embedding cache size is reported instead).
The dense methods are timed in *sandbox* stores (identical in-memory Qdrant copies of each architecture) so that global/fixed/adaptive are measured by exactly the same code; the run asserts that the adaptive sandbox's top-10 equals the live production engine for 40 queries.
Latencies are single-machine, local-mode Qdrant (brute force, in-process) numbers; they say nothing about a networked server with payload indexes.

## 8. Statistics

Per-dataset scores first. Paired query-level percentile bootstrap (1,000 resamples, seed 13, unit = query, both systems share each resample) of *treatment − baseline* for nDCG@10 and Recall@5, for adaptive − fixed, fixed − global, adaptive − global and global − BM25.
An interval that contains 0 means this sample cannot distinguish the systems. No significance is claimed from a higher aggregate alone. Macro-averages weight each dataset equally and are labelled when any member is an internal split or a smoke run. Judgments are never pooled across datasets.

## 9. Candidate validation (additive extensions to MORPHE)

Every structural candidate is planned (pure function), evaluated against the current architecture in isolated sandboxes on the **validation group**, and committed only if the acceptance policy passes
(then: checkpoint → converge → integrity check → post-commit verification → new version; any failure converges back to the checkpoint). Statuses stay distinct: *rejected* (never committed), *failed* (mutation failed), *rolled_back* (committed, then restored).

Guardrails (any breach rejects): nDCG@10 ≤ 0.02, Recall@5 ≤ 0.02, MRR@10 ≤ 0.02 absolute regression; p95 ≤ +10 % (or ≤ 1 ms). **Benefit requirement:** nDCG@10 or Recall@5 gain ≥ 0.005, or mean eligible-candidate reduction ≥ 5 %, or p95 improvement ≥ 5 %
(above the noise floor); otherwise the change is rejected as having no meaningful benefit. Missing/insufficient data is never a pass. **Protocol v2** adds: interleaved paired latency with 3 repetitions, a quality gain only counts if its paired-bootstrap 95 % lower bound > 0, and a cumulative guard against the initial architecture.
All of this is configuration (`acceptance_profile`, `require_benefit`, `benefit_requires_ci`, `cumulative_guard`, `eval_interleaved`, thresholds) and is displayed with every run.

## 10. Safety evidence (Q5), produced on the benchmark corpus after the test evaluation

1. a deliberately harmful candidate (documents shuffled across modules, routing profiles stale) is injected through `AdaptationService.process(plan_transform=…)` and must be **rejected before commit** with the state, graph and version pointer unchanged;
2. a valid candidate with an injected post-commit fault (`fault="post_commit"`) must end `rolled_back` with the persistent state restored exactly (checksum) and integrity passing;
3. a real committed change is restored away (`ArchitectureLifecycle.restore`) and restored again; both states must match their snapshots.
The probes relax only candidate *availability* (co-access thresholds, benefit requirement, drift guard); validation, acceptance, commit and recovery are the production code. `plan_transform` and `fault` are internal seams not reachable from the API.

## 11. Artifacts

`backend/artifacts/beir/<dataset>/<run_id>/` (git-ignored): `manifest.json` (config, environment, versions, protocol flags, thresholds, caveats, timing methodology, split provenance), `metrics.json`, `per_query_metrics.json`,
`latency.json`, `comparisons.json` (bootstrap + most improved/regressed queries), `adaptation.json` (all proposals with baseline/candidate validation metrics and decisions), `safety.json`, `sweep.json`, `logs.jsonl`, `run_<method>.trec` (BEIR `save_runfile`, original document and query ids).
MongoDB holds only job metadata (`benchmark_runs`); the dashboard reads artifacts through the API.

## 12. Runbook

```bash
# prerequisites: backend venv with `pip install -r backend/requirements.txt` (adds beir, requests); MongoDB running (scripts/dev_mongo.sh)
cd backend
python -m app.evaluation.run_beir --datasets scifact --methods global_dense,fixed_graph,adaptive_graph --split test --query-limit 20    # smoke (non-official)
python -m app.evaluation.run_beir --datasets scifact,nfcorpus,arguana --methods bm25,global_dense,fixed_graph,adaptive_graph --split test --download
python -m app.evaluation.report_beir --latest            # table, comparisons, macro-average (add --json for the raw summary)
```
Flags: `--protocol v1|v2`, `--query-limit N`, `--seed`, `--max-windows`, `--force-embeddings`, `--no-sweep`, `--no-safety-probe`, `--keep-db`, `--mongo-backend mongomock` and `--embedding-backend hashing` (offline smoke tests only; the hashing embedder is a fake).
Through the API/dashboard: `POST /api/v1/benchmarks/run`, page **Benchmarks**. Progress is logged to the console and `logs.jsonl`. Embeddings are cached after the first run; an interrupted run leaves its `manifest.json` as `running` (CLI) or the job as
`failed: interrupted by backend restart` (API) — just rerun, cached embeddings are reused.
Resources observed on a 10-core / 16 GB Apple-silicon laptop: datasets 8–11 MB each, embedding cache ≈ 7–13 MB, process RSS ≈ 0.8–1.0 GB, corpus embedding 15–40 s on MPS, tens of minutes per dataset for the adaptation phase.

## 13. Limitations

* Latency is measured in-process on one machine with a brute-force local vector store; a graph filter can easily cost more than it saves at this corpus size.
* The initial graph is unsupervised KMeans on MiniLM embeddings; other module constructions could route differently.
* Validation groups are small (≤ 150 queries during candidate evaluation), so only large effects can be detected during acceptance.
* BM25 here is a local re-implementation (no stemming/stop-words), not an Elasticsearch/Lucene run.
* ArguAna's score is an internal split and its documents are heavily truncated by the 256-wordpiece limit.
* Baselines beyond BM25 and MiniLM (stronger embedders, rerankers, hybrid) are out of scope.
