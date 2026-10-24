# Why graph routing loses relevant documents (BEIR validation groups)

Phase 3 of the post-benchmark work. This is **diagnostic analysis of the fixed-graph baseline**, not a new benchmark result and not a retrieval-method change. Nothing was tuned.

* Data: the **validation** group of each dataset only (SciFact 403 queries, NFCorpus 323 = official dev, ArguAna 258 = internal partition). The test group is untouched.
  Qrels are used only to explain results; they are never used at retrieval time and no relevance label is derived from access patterns.
* Architecture: the initial deterministic KMeans graph (52 / 36 / 87 modules, about 100 documents each), engine settings exactly as in the v2 benchmark
  (3 seed modules, ≤ 8 candidate modules, fallback when the best guided score < 0.30 or fewer than 10 results). The adapted architectures differ from it by at most one split (v2 results)
  and no longer exist in memory, so they were not diagnosed separately.
* Artifacts: `backend/artifacts/beir_phase2/diag_03/<dataset>/routing_diagnostics.json` (aggregate, taxonomy, examples and one row per query with every relevant document). Code:
  `backend/app/evaluation/routing_diagnostics.py`; tests: `backend/tests/test_routing_diagnostics.py`. Reproduce: `cd backend && ../.venv/bin/python -m app.evaluation.routing_diagnostics --run-id <new id>`.
* Run history: `diag_01` and `diag_03` are valid and agree on every count (only `diag_03`, the final run, is committed with the repository; `diag_01` stayed on the author's disk). A third run, `diag_02`, was **invalid and deleted**: I launched two processes with the same run id and they shared one MongoDB
  database, so one process's cleanup removed data the other was using. The tool's own consistency check flagged 197 NFCorpus queries, which is how I noticed; the tool now marks such runs invalid and exits non-zero, and each context uses a unique database name.
  Latency numbers below come from `diag_03` (run alone; `diag_01` agrees within 5 %).

## How to read the analysis

The graph path scores a document exactly like global search (same cosine, same vectors) but only over the documents in the selected modules. So a relevant document that global search puts in
its top-10 **and** that lies in a selected module is always in the graph top-10. This was verified on every query: relevant documents lost versus global top-10 although their module was searched = **0** on all three datasets.
Consequence: every loss against global search is an exclusion by the routing step, and graph routing can also *win* a query when excluding distractors lifts a relevant document into the top-10.

## Headline result per dataset (validation, mean over queries)

| | SciFact | NFCorpus | ArguAna |
|---|---|---|---|
| nDCG@10 global → graph (Δ) | 0.6445 → 0.5747 (−0.0697) | 0.3017 → 0.2672 (−0.0344) | 0.5188 → 0.5057 (−0.0131) |
| Recall@100 global → graph (Δ) | 0.9376 → 0.7945 (−0.1431) | 0.3026 → 0.2502 (−0.0524) | 0.9767 → 0.9264 (−0.0504) |
| Queries graph worse / equal / better | 42 / 344 / 17 | 78 / 198 / 47 | 6 / 240 / 12 |

These validation deltas have the same sign and similar size as the test-split deltas in `BEIR_RESULTS.md` (−0.094, −0.039, −0.010), so the validation group is a fair proxy for the behaviour being explained.

## Failure taxonomy

Primary category for each query where graph nDCG@10 is below global (exclusive categories):

| Category | Definition | SciFact | NFCorpus | ArguAna |
|---|---|---|---|---|
| **Routing miss** | every lost relevant document is in a module the graph could not reach from any seed | 28 (67 %) | 53 (68 %) | 5 (83 %) |
| **Expansion miss** | every lost relevant document is in a module the graph could reach from a seed, but the candidate cap / tier order dropped it | 14 (33 %) | 18 (23 %) | 1 (17 %) |
| Mixed | both kinds among the lost documents | 0 | 7 (9 %) | 0 |
| Unattributed | lost with no excluded relevant document | 0 | 0 | 0 |
| **Total graph-worse queries** | | 42 (10.4 % of 403) | 78 (24.1 % of 323) | 6 (2.3 % of 258) |

Non-exclusive flags (queries):

| Flag | SciFact | NFCorpus | ArguAna | Meaning |
|---|---|---|---|---|
| Ranking miss | 61 | 265 | 47 | a relevant document was searched (its module was selected) yet is in the top-10 of neither system: an embedding-model limit, not a routing effect |
| Fallback miss | 42 | 78 | 6 | graph worse and the global fallback did not run. See the caveat below: this is true of every graph-worse query by construction |
| System overhead | 386 | 273 | 246 | graph slower than global with no nDCG gain |
| Mapping / consistency error | **0** | **0** | **0** | judged document absent from the store, or lost although eligible |

**Fallback caveat.** If the fallback runs, the final list is the global list, so the query cannot be graph-worse. Hence "fallback miss = 100 % of graph-worse queries" is a tautology and not a separate finding. The informative number is how often the fallback fires at all:
0.5 % of SciFact queries (2), 11.8 % of NFCorpus (38), 0 of ArguAna. In NFCorpus it raised nDCG@10 in only 4 of its 38 firings, changed nothing in 27 and lowered it in the other 7 (mean gain 0.017; a lower value occurs when the global list contains distractors the guided list had excluded). The trigger (best guided score < 0.30 or fewer than 10 results) is
nearly independent of whether routing excluded a relevant document: a routing miss still returns ten confident-looking results from the wrong modules.

## Why relevant documents are excluded

Relevant documents, by where the router put them (document-level, summed over validation queries):

| | SciFact | NFCorpus | ArguAna |
|---|---|---|---|
| Relevant documents in the store | 459 | 11,378 | 258 |
| in a **seed** module | 351 (76.5 %) | 3,304 (29.0 %) | 242 (93.8 %) |
| in an **expanded** module | 32 (7.0 %) | 1,631 (14.3 %) | 2 (0.8 %) |
| **excluded** | 76 (16.6 %) | 6,443 (56.6 %) | 14 (5.4 %) |
| Relevant documents lost vs global top-10 / gained | 44 / 5 | 151 / 60 | 6 / 2 |
| Queries with all / some / none of their relevant documents covered | 330 / 4 / 69 | 27 / 252 / 44 | 244 / 0 / 14 |

1. **The module whose centroid is closest to the query is often not the module that holds the answer.** For excluded relevant documents the module's profile-similarity rank has median 12 (of 52 modules) in SciFact,
   16 (of 36) in NFCorpus and 9.5 (of 87) in ArguAna; 58 % / 70 % / 50 % are ranked beyond 10 (or 4–10 for the rest). A module profile is the mean of about 100 abstracts or arguments; a short query (a claim, a single word such as "galactose") is not close to that blurry average
   even when it is very close to one document inside it. SciFact seeds often have low similarity (e.g. query 1016: seed scores 0.36 / 0.32 / 0.32).
2. **Hierarchy expansion adds little.** At equal module budget, graph expansion reaches fewer relevant documents than simply taking the next modules by profile similarity (diagnostic estimate, next section):
   SciFact: 3 seeds reach 76.5 %; 8 modules chosen by the graph reach 83.4 %; the top-5 by profile alone reach 82.1 %, the top-8 88.0 %. Only 35 % / 27 % / 21 % of excluded relevant documents sit in a module the graph can reach from the seeds at all.
   The expanded modules (hierarchy children and siblings of the seeds) are clusters of modules that KMeans grouped together, not modules that tend to hold the same answers.
3. **Multi-topic queries defeat hard filtering.** NFCorpus queries have on average 35 relevant documents spread over about 9 distinct modules (SciFact and ArguAna: 1.0–1.02), so covering all of them needs most of the corpus: only 27 of 323 queries have all relevant documents covered.
4. **No co-access evidence is used.** The initial graph has hierarchy edges only (adaptation did not create shortcut edges in any benchmark run), so expansion is purely structural.
5. **Routing can win.** 17 / 47 / 12 queries improved because modules containing distractors were excluded (e.g. SciFact 685, "Lamins are associated with nuclear membrane structure maintenance": relevant document moved from rank 3 to 1).
   These wins are real but smaller than the losses (+5 versus −44 relevant top-10 documents in SciFact).

### Diagnostic estimate: how many modules would have to be searched?

**This is an estimate from module-profile ranks, not a measured retrieval result.** It asks: if the top-n modules by profile similarity were searched (no hierarchy expansion, no fallback), what share of relevant documents would be reachable, and what share of the corpus would that be? (`coverage_vs_probe_depth` in the artifact.)

| n modules | SciFact: relevant reachable / corpus | NFCorpus | ArguAna |
|---|---|---|---|
| 1 | 55.1 % / 2.2 % | 15.4 % / 3.0 % | 82.6 % / 1.4 % |
| 3 (the benchmark's seed count) | 76.5 % / 6.5 % | 29.0 % / 9.1 % | 93.8 % / 4.3 % |
| 5 | 82.1 % / 10.8 % | 37.2 % / 15.1 % | 95.0 % / 7.2 % |
| 8 (the benchmark's candidate cap) | 88.0 % / 17.3 % | 47.5 % / 24.2 % | 97.3 % / 11.5 % |
| 12 | 90.8 % / 26.0 % | 57.5 % / 36.3 % | 98.4 % / 17.3 % |
| 24 | 98.3 % / 52.0 % | 83.5 % / 72.5 % | 99.2 % / 34.6 % |

So on SciFact, covering 98 % of relevant documents with centroid routing needs about half the corpus; on NFCorpus 84 % coverage needs about 72 %. These curves bound what *any* hard filter over these clusters can achieve with this query-to-centroid routing signal. They are an upper bound on coverage, not on nDCG, and a different clustering, a different routing signal or multi-vector profiles could behave differently; none was tested.

## Latency: where does the overhead come from?

Mean per-query stage times (ms, median of 3 repetitions per query, validation queries, local Qdrant, one machine):

| Stage | SciFact global | SciFact graph | NFCorpus global | NFCorpus graph | ArguAna global | ArguAna graph |
|---|---|---|---|---|---|---|
| Seed selection (profile search) | 0 | 0.17 | 0 | 0.16 | 0 | 0.21 |
| Graph expansion | 0 | 0.04 | 0 | 0.03 | 0 | 0.05 |
| Vector search | 16.89 | 32.34 | 12.33 | 23.33 | 31.54 | 60.90 |
| Ranking / dedupe | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 |
| Fallback search | 0 | 0.08 | 0 | 1.50 | 0 | 0 |
| **Total** | **16.91** | **32.66** | **12.35** | **25.05** | **31.57** | **61.20** |

The graph logic itself (seed selection, expansion, ranking) costs about 0.2 ms. All of the extra time is in the module-filtered vector-search call, which is roughly twice the unfiltered one **even though it is allowed to consider only about 10–23 % of the documents**.
That is a property of the Qdrant deployment, not of the graph; it is analysed in `BEIR_PERFORMANCE_ANALYSIS.md`.

## Representative examples (validation queries; full list per category in the artifact)

* **Routing miss, SciFact 1016** — "Rapamycin increases the concentration of triacylglycerols in fruit flies." The relevant paper (*Mechanisms of Life Span Extension by Rapamycin in the Fruit Fly…*) is global rank 1 but lives in module `M035 · aging human stem`, the 7th most similar module and not reachable from the seeds. nDCG@10 1.0 → 0.0.
* **Expansion miss, SciFact 1188** — "The ability of activator E2Fs in preventing cell death is limited to terminally differentiated cells." The relevant paper (*E2F1-3 switch from activators…*) is global rank 1; its module `M014 · stem cells reprogramming` is profile rank 12 and *is* reachable through the hierarchy, but the 8-module cap dropped it. nDCG@10 1.0 → 0.0.
* **Mixed miss, NFCorpus PLAIN-428** — "In making water alkaline, how much baking soda is too much?" One relevant paper sits in a reachable-but-dropped module (profile rank 29), another in an unreachable module (rank 4). nDCG@10 1.0 → 0.0.
* **Routing miss in a weakly-linked topic, NFCorpus PLAIN-2559** — "Treating Hepatitis C with Chlorella": the judged-relevant documents (yeast β-glucan, leukocytes after exercise) are indirectly related and sit in module `M032 · exercise muscle damage` (rank 28). NFCorpus relevance is often indirect, which centroid similarity cannot see. Neither system ranks these two in the top-10; global nDCG@10 is 0.68 thanks to other relevant documents, and the graph path lost all of them (nDCG@10 0.0).
* **Routing win, SciFact 685** — see above (rank 3 → 1).
* **System overhead, SciFact 0** — "0-dimensional biomaterials lack inductive properties.": identical ranking and nDCG@10 (0.431), 2× the search time.

Module names are auto-generated top keywords and are often poor topic labels (e.g. `M014 · human smoke food`); do not read semantic structure into them.

## Limits of this analysis

* Validation groups only, fixed-graph architecture only, one embedding model, one clustering (KMeans, seed 13), 3 seeds and a cap of 8; the probe curve is an estimate from profile ranks.
* "Reachable from seeds" is computed with the real expansion function with an unlimited cap, i.e. over the hierarchy and any co-access edges (none existed).
* The taxonomy attributes the loss to the step that excluded the document; it does not say a better policy would have found it, only that this policy did not.
* Mapping or consistency errors: 0 queries in the valid runs. One earlier run was invalid because of my own process collision (above), which the consistency check detected.
* Latency numbers come from local in-process Qdrant on one laptop and are specific to that deployment.
