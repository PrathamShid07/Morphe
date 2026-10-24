# BEIR benchmark plan

Written before the experiments were implemented. Deviations are recorded in `BEIR_RESULTS.md`.

## Audit of what MORPHE already had
| Capability | State before this work |
|---|---|
| Embedding | `sentence-transformers/all-MiniLM-L6-v2`, 384-d, cosine, max 256 wordpieces |
| Retrieval | `RetrievalEngine`: module-profile seeds → NetworkX expansion → module-filtered Qdrant search → ranking → global fallback; `mode="global"` exists |
| Evaluator | isolated in-memory sandbox of an `ArchitectureState` queried by the real engine; **Recall@5, MRR@5 (top-5 only), p95 retrieval latency, candidate count, fallback rate**, binary labels only |
| Acceptance | Recall@5 / MRR@5 / p95 regression guards, sufficient-data check; **no nDCG, no requirement that a change be beneficial** |
| Adaptation / versions / rollback | split · merge · shortcut, checkpoint → converge → integrity → post-commit verification → version, recovery and restore (all tested) |
| Ingestion | chunking pipeline; chunk ids are *not* corpus document ids |

Gaps this work fills: graded nDCG@10, MRR@10, MAP/Precision/Recall at 10/100, per-stage latency, original-document-id round trip, a
benefit requirement for accepting a change, a BEIR adapter, BM25, splits without leakage, bootstrap statistics, artifacts, API, dashboard.

## Datasets (official BEIR files, `GenericDataLoader`, cached in `data/beir/`, git-ignored)
| Dataset | Corpus | Official qrels splits found locally | Role |
|---|---|---|---|
| SciFact | 5,183 | train 809 q / test 300 q | first end-to-end run |
| NFCorpus | 3,633 | train 2,590 / dev 324 / test 323 (graded 1,2 in dev/test) | generalisation, graded relevance |
| ArguAna | 8,674 | test 1,406 only | argument retrieval; **internal** split required |
Any other BEIR id can be requested with `--datasets` if its folder is cached or `--download` is passed. A missing dataset is an explicit error.

## Split protocol (deterministic, seeded; never touches final-test labels before freezing)
* **access-workload group** — queries only (no qrels used) replayed through the live retrieval path to create access/co-access events;
* **validation group** — qrels used *only* to evaluate candidate architectures in the real adaptation engine;
* **test group** — official test queries; evaluated once, after the architecture is frozen.
SciFact: train split partitioned 50/50 (seed 13) into workload/validation; test = official test.
NFCorpus: workload = seeded sample (≤ 500) of train; validation = official dev; test = official test.
ArguAna: no train/dev exists → seeded query partition 30/20/50 of the single official split into workload/validation/test; the report labels this
**internal evaluation split, not the official full-test score**.
`--query-limit` truncates the test group (and caps workload/validation proportionally) and marks the run `smoke` / non-official.

## Methods (all dense methods share the same cached embeddings, preprocessing and the same `RetrievalEngine`)
* **A. BM25** — local Okapi BM25 (k1=1.2, b=0.75, lowercase alphanumeric tokens, no stemming/stop-words) over `title + text`; not a published Elasticsearch run.
* **B. Global dense** — `RetrievalEngine.retrieve(mode="global")` over the whole active corpus.
* **C. Fixed graph** — real graph-guided path on the initial deterministic graph (seeds 3, ≤ 8 candidate modules, fallback at best score < 0.30 or too few results).
* **D. Adaptive graph** — same engine on the architecture produced by the real adaptation engine (split/merge/shortcut proposals evaluated on the validation group, committed and versioned only if accepted).

## Initial graph (independent of every relevance label)
Corpus embeddings → KMeans (seed 13, `k = round(N / 100)`, min 8) → modules; module profile = centroid; hierarchy = KMeans of module centroids into
`round(sqrt(k))` group containers (parent→child edges); no initial co-access edges. Same graph for C and D.

## Metrics
BEIR `EvaluateRetrieval.evaluate` (pytrec_eval) at k ∈ {1,3,5,10,100} for **nDCG@10 (headline, graded qrels kept)**, MAP@10, Recall@5/10/100, Precision@10,
plus BEIR `evaluate_custom(metric="mrr")` for MRR@10. Per-query values are computed with pytrec_eval using identical measures and cross-checked
against BEIR's aggregate (they must agree to rounding). System metrics: p50/p95/mean end-to-end latency, query-embedding latency, seed / expansion /
Qdrant / fallback latency, fallback rate, eligible candidate documents and reduction vs global, graph nodes/edges, build time, RSS, storage.
"Eligible candidate documents" = active documents inside the searched modules (+ whole corpus if fallback ran); it is **not** a count of vectors the engine scanned.

## Acceptance policy for candidate architectures (validation group only)
Guardrails: nDCG@10 ≤ 0.02 abs regression, Recall@5 ≤ 0.02, MRR@10 ≤ 0.02, p95 ≤ +10 % (with the noise floor). **Plus a benefit requirement**: nDCG@10 or
Recall@5 gain ≥ 0.005, or mean candidate pool reduction ≥ 5 %, or p95 improvement ≥ 5 % (and above noise). No benefit ⇒ rejected (no neutral-change policy).
Missing or insufficient data is never a pass. Thresholds are fixed before the test split is evaluated.

## Statistics
Paired query-level bootstrap (1,000 resamples, seed 13, unit = query, percentile 95 % interval) of adaptive − fixed for nDCG@10 and Recall@5 (also each vs global dense).
Macro-average across datasets gives each dataset equal weight; relevance judgments are never pooled across datasets.

## Timing
Models and indexes are built before timing; one warm-up retrieval per query, then the median of 3 repetitions per query; query embedding is timed separately
(batch size 1, warm); cold start (model load, corpus embedding, graph build) is reported separately. Single machine, sequential methods.

## Safety evidence (Q5)
On the real benchmark corpus after the test evaluation: (1) a deliberately harmful candidate (records shuffled, routing profiles left stale) must be **rejected pre-commit**;
(2) a valid candidate with an injected post-commit fault must end **rolled_back** with the pre-change state restored exactly (checksum and version pointer).

---

## Addendum — protocol v2 (written 2026-10-25, before NFCorpus/ArguAna were evaluated)

**Chronology (read this before interpreting any number).** Protocol **v1** (everything above) was run once on the full official SciFact test
split (`beir_scifact_full_01`). I looked at that result — and at the *validation-group* adaptation events behind it — before changing anything. The events
showed four defects in the validation protocol itself, independent of the test scores:

1. a quality "benefit" of +0.005 Recall@5 on 200 validation queries is **one query**; it is noise, not evidence;
2. p95 latency was timed once per query, baseline and candidate unpaired and sequential, and swung ±20 % between near-identical architectures
   (e.g. 36.1 → 42.4 ms for a candidate that changes almost nothing), producing both spurious rejections and spurious "latency benefits";
3. each candidate was guarded only against its immediate parent, so six individually small steps can add up to a regression no single step is allowed to cause;
4. the detector only ever surfaced split proposals (no module pair reached the co-access threshold), so the evaluated set was not representative.

Protocol **v2** changes only the *validation/acceptance mechanics* (not the retrieval method, not the dataset, not the splits, not the guardrail thresholds):

| Change | v1 | v2 |
|---|---|---|
| Candidate latency measurement | baseline then candidate, 1 repetition | **interleaved** (rotating order per query), median of **3** repetitions, same queries |
| Quality benefit | any gain ≥ 0.005 | gain ≥ 0.005 **and** paired-bootstrap 95 % lower bound > 0 on the validation queries |
| Drift | guard vs immediate parent only | **plus** guard vs the *initial* architecture (same 0.02 limits) |
| Candidate-pool benefit, latency benefit, guardrails | unchanged | unchanged |
| Budget (runtime) | 6 windows, ≤ 9 proposals/window, 200 validation queries | ≤ 5 windows, ≤ 6 proposals/window (round-robin across operation types), 150 validation queries |

`--protocol v1` still reproduces the first protocol. **Consequence for the evidence:** SciFact's test split has now been seen under v1, so SciFact numbers
produced under v2 are a *post-hoc re-run* and are reported as such; they are not a clean held-out evaluation of v2. NFCorpus and ArguAna were never evaluated before v2 was fixed
(this addendum precedes their runs), so they are the clean tests of v2. No threshold was loosened to obtain an acceptance, and the default routing
(3 seeds, 8 candidate modules) is unchanged and was chosen before any evaluation.

A validation-only **routing sweep** (seeds × candidate cap) is also recorded for context. It is informational: it does not select a configuration for the primary comparison.
