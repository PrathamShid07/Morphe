# BEIR live-versus-sandbox parity investigation

Phase 2 of the post-benchmark work. Question: the v2 benchmark reported that, for the first 40 test queries, the live production engine and the
evaluation sandbox returned the same top-10 for 40/40 queries on SciFact but only 37/40 on NFCorpus and 37/40 on ArguAna. Is the benchmark
measuring something other than what production serves?

**Answer: no. The two engines are identical on every query of every dataset. The 3/40 "mismatches" were produced by the parity check itself
(it applied two different tie-break rules to exact score ties between duplicate documents). The investigation also found a genuine, separate
determinism gap in the vector store (insertion-order-dependent results when a tie group straddles the search limit), which is now fixed.**

Artifacts: `backend/artifacts/beir_phase2/parity_04/<dataset>/parity.json` (machine readable, one row per test query; `parity_01…03` are earlier
development iterations of the tool, kept on the author's disk but not committed). Code: `backend/app/evaluation/parity.py`, `run_parity.py`. Tests: `backend/tests/test_parity.py`.
Reproduce: `cd backend && ../.venv/bin/python -m app.evaluation.run_parity --datasets scifact,nfcorpus,arguana --run-id <new id>`.

## Method

`run_parity` rebuilds, for each dataset, the same real MORPHE context the benchmark builds (cached corpus embeddings, deterministic KMeans
initial graph, ingestion through the production persistence layer into a live in-memory Qdrant + Mongo), exports the live architecture state,
builds the sandbox from that state exactly as the benchmark does, and runs **both** engines on **identical query vectors** for **every test query**
(300 / 323 / 703, not a sample of 40). Only query text is used; no relevance judgments are read. For each query it records the first pipeline stage at which the
outputs diverge — `query_vector → seed_modules → candidate_modules → guided_space → guided_ranking → fallback_decision → final_ranking` — and a classified cause.
It also (a) re-runs the **original check's logic** to see whether it reproduces the reported numbers, and (b) compares live global retrieval with an exact float64 numpy ranking.

Scope limit: this run uses the **initial** architecture (no adaptation), so it demonstrates the mechanism and the engine equivalence, but it does not re-examine the
specific adapted architecture of NFCorpus (`arch_0003`) or ArguAna (`arch_0003`), whose in-memory stores no longer exist. The original check's mismatch counts reproduce
exactly on the initial architecture, and the adapted architecture differs from it by one module split, so I consider the explanation established; a
future benchmark run includes the corrected check on its own adapted architecture (see "What changed in the benchmark code").

## Results

| | SciFact | NFCorpus | ArguAna |
|---|---|---|---|
| Queries compared (live vs sandbox, full test group) | 300 | 323 | 703 |
| **Engine-vs-engine divergence at any stage** | **0** | **0** | **0** |
| Original check's logic, first 40 sorted query ids | 0 mismatches | **3** | **3** |
| Original check's logic, all queries | 0 | 13 | 29 |
| Documents sharing a bit-identical vector (groups / documents) | 0 / 0 | 40 / 80 | 49 / 98 |
| Live global top-10 equals the exact numpy ranking | 300/300 | 323/323 | 703/703 |
| Queries with an exact score tie across rank 10 / rank 100 (exact oracle) | 0 / 0 | 6 / 1 | 9 / 6 |
| Queries whose top-100 membership changes with tie-safe search | 0 | 2 | 1 |

The "original check's logic" row reproduces the previously reported 40/40, 37/40, 37/40 exactly (SciFact has no duplicate vectors, so no mismatches; NFCorpus and ArguAna 3 each).

### Root cause of the reported 3/40

All 13 (NFCorpus) and 29 (ArguAna) mismatching queries have **identical top-10 score vectors** in the two systems: the lists differ only in how documents with exactly equal
cosine scores are ordered. Those documents are duplicates (same title and text under different ids, e.g. ArguAna `test-culture-…-con02b` vs `test-science-…-con02b`) so their
embeddings are bit-identical. The check ordered:

* the **live** ranking by `(-score, memory_id)` where `memory_id = mem_beir_<dataset>_<sha1(doc_id)[:14]>` (the engine's rule), and
* the **sandbox** ranking by `(-score, BEIR document id)` (it re-sorted a `doc → score` dict).

A SHA-1 hash order and a lexicographic id order disagree about half the time on a tie, so the two orderings of the same set differ. In 5 of the 42 cases an exact tie also straddled the rank-10 cut
and the top-10 *membership* differed. This is a defect of the benchmark's parity check, not of retrieval. Reproduction confirms it: comparing the engines directly (same ordering rule, memory ids) gives 0
divergences, and a unit test (`test_live_and_sandbox_engines_agree_on_duplicate_documents_but_legacy_check_does_not`) shows the legacy comparison misreporting exact ties on a synthetic corpus with duplicates.

### Does it affect the reported retrieval metrics? No for ordering; ≤ 2 queries per dataset for the cut at depth 100

The benchmark evaluates with `pytrec_eval`, which **ignores the engine's order for tied scores and sorts ties by document id (descending)**. I verified this directly
(`{'a':0.5,'b':0.5}` and `{'b':0.5,'a':0.5}` give the same nDCG@1; the larger id wins). So within the evaluated depth, tied documents are ordered by the evaluator
identically for every method, not by either engine. The only way the engine's tie-breaking can reach a metric is *which* tied documents are inside the returned 100-document list when a tie
group straddles rank 100 (1 query in NFCorpus and 6 in ArguAna have such a tie). With the new tie-safe search the top-100 membership changed for 2 of 323 (NFCorpus) and 1 of 703 (ArguAna) test queries relative
to the old plain search on the live store. Those can only move Recall@100 for those queries; I did not re-evaluate the saved baselines, which remain immutable.

## Checklist of what was compared

| Item | Result | Evidence |
|---|---|---|
| Query text and preprocessing | Same text; both engines receive the same query vector, so preprocessing cannot differ between them | by construction; `same_query_vectors_for_both_engines` |
| Embedding model, dimension, preprocessing | `all-MiniLM-L6-v2`, 384-d, `beir_title_text_v1`, same cached corpus matrix feeds both stores | `embedding` block in `parity.json` |
| Query vector generation | Generated once (batch 1) and shared; run-to-run determinism of the model on `mps` was **not** tested | — |
| Corpus ids ↔ memory ids | Bijective, verified; every live payload `beir_doc_id` matches the map | `assignments.beir_doc_id_mismatches = 0` (all datasets) |
| Active records and module assignments | Live Qdrant payload `module_id` equals the architecture state for every memory; all points active | `assignments.module_id_mismatches = 0`, `non_active_points = 0`, counts equal |
| Module-profile vectors | Live profiles vs the state vectors used by the sandbox agree to ≤ 1.3×10⁻⁸ after normalisation | `profile_vectors.max_abs_diff_after_normalisation` |
| NetworkX graph | Live and sandbox graphs identical (nodes with record counts, typed edges with weight and direction) | `graph.identical = true` |
| Graph traversal order | Same function, same inputs → same candidate module list at every query | `candidate_modules` stage never diverges |
| Qdrant configuration | Local in-process mode, cosine distance, size 384, no payload indexes (see below) | `qdrant_config_live` |
| Similarity definition and direction | Cosine similarity, higher is better; the global top-10 equals an exact float64 cosine ranking on every query | oracle row above |
| Deduplication, final ranking, fallback decision | Identical (`rank_hits`; fallback flag equal for all queries) | stage comparison |
| Tie-breaking | **The finding.** Engine rule: `(-score, memory_id)`; evaluator rule: `(-score, doc id desc)`; legacy check used a third mixture of both | above |
| Floating point | No score disagreement for the same document at any rank (`score_disagreement` count 0) | `cause_counts` empty |
| Concurrent updates | None in the benchmark (single-threaded, mutations are serialised by the coordinator); not stress-tested | — |
| Approximate search | **None.** Qdrant local mode is brute-force exact; no ANN index is used. Ties are the only non-determinism source | oracle row |

## The genuine determinism gap that was found and fixed

A vector store returns an arbitrary subset of a tie group when the group straddles the `limit`. Qdrant local mode picks the subset by internal point order, i.e.
**insertion order**. Demonstration (12 identical vectors, limit 5, four insertion orders): the plain search returned four different result sets,
e.g. `d05,d08,d10,d02,d01` vs `d11,d02,d10,d09,d08`. The live store (ingested in sorted-id batches) and the sandbox (inserted in module order) have different insertion orders, so
the two engines *could* diverge on a boundary tie even though none did on these queries.

Fix (`QdrantStore._tie_safe`, setting `tie_safe_search`, default on): fetch `limit+1`; if the extra hit's score is strictly lower than the `limit`-th score, the boundary group is complete; otherwise
widen (doubling the margin, capped at 20,000) and retry; then order by `(-score, id)` and cut at `limit`. The same helper protects module-profile search. In the no-tie case the cost is one extra candidate.
With the fix the same experiment returns one result set for all insertion orders (`d00…d04`). `tie_safe_search=False` restores the legacy behaviour for reproducing the baseline artifacts.

## What changed in the benchmark code (baselines untouched)

* `benchmark/runner.py::_live_parity` now compares the two engines directly, in the engine's own order, stage by stage, and records divergent queries with stage and cause. The `checked`/`identical_top10`
  keys remain, so reports and the dashboard keep working. Only new runs use it; v1/v2 artifacts are unchanged and read-only, and a frozen copy of the pre-change source is in `backend/_baseline_src_v2/`.
* Explicit rules, now documented in one place: engine order = `(-score, memory_id)`; evaluator order = pytrec_eval (`score` desc, document id desc). The two are never compared against each other.

## Open items

* Run-to-run determinism of query embeddings on `mps` (the benchmark shares one vector between engines, so it cannot cause live/sandbox divergence, but it could move scores between separate runs).
* The adapted NFCorpus/ArguAna architectures were not re-examined (see Scope limit); the next full benchmark run records the corrected check on its own adapted state.
