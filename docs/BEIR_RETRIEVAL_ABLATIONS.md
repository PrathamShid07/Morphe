# Retrieval strategies: progressive expansion and soft graph routing (BEIR ablations)

Run `strat_01` (protocol `strategies-v1`), 2026-10-25. Artifacts: `backend/artifacts/beir_strategies/strat_01/` (frozen config, validation sweeps, per-dataset validation and test results with per-query rows, server latency).
Code: `backend/app/retrieval/strategies.py` (the strategies), `backend/app/evaluation/run_strategies.py` (the experiment), `run_strategy_latency.py` (indexed-server latency).
The frozen v1/v2 results and `BEIR_RESULTS.md` are unchanged; this is a new run with a new protocol label and new artifacts.

**Bottom line: neither strategy gives a reliable benefit over global dense retrieval on these workloads.** Both are far better than the fixed hard-filter graph, but only because they stop excluding relevant documents:
progressive expansion gets there mostly by falling back to a full global search, and soft routing is a full global search with a negligible re-ranking. Neither is faster than global search, in the local store or on an indexed Qdrant server.
The recorded evidence does not justify hard module-membership constraints for these corpora.

## 1. What each strategy does (precisely)

| | Searches | Uses graph/module information to | Candidate reduction? |
|---|---|---|---|
| **Global dense** | every active memory | nothing | none |
| **Fixed graph** (original) | only the modules selected by profile similarity + hierarchy expansion (3 seeds, ≤ 8 modules); global fallback only when the best score < 0.30 or fewer than 10 results | **exclude** every other module | yes (77–89 %) |
| **Progressive expansion** | stage 1: top-3 modules by profile similarity; then adds modules a few at a time and searches **only the new ones**, merging by memory id | decide **how far to widen** the search | yes, unless it falls back |
| **Soft graph routing** | the global dense top-100 (full search), then re-ranks | **shift scores** by a bounded prior; excludes nothing | none (full search) |

*Progressive expansion — stopping and fallback (query-time signals only, no labels).* After each stage the top-10 membership is compared with the previous stage. It stops when the top-10 is unchanged for `patience` consecutive expansion steps (`stable`), when the budget (`max_modules`, `max_steps`) is reached, or when every module was searched.
It falls back to a full global search when the result is weak (no/too few results or best score < 0.30, the existing rule) or when the budget ended while the top-10 was still changing (the evidence is unsettled). The next modules come from profile rank (`profile`), graph neighbours first (`graph`), or profile rank plus a small bonus for modules linked to an already searched one (`blend`).

*Soft graph routing — scoring rule.* `final = dense + λ · (prior − mean prior over the candidate pool)`, with `prior = (1 − g) · P + g · G`. `P` is the min–max normalised query-to-module-profile similarity over all modules; `G = 1/(1 + d)` where `d` is the graph distance from the top-3 profile matches over hierarchy edges and co-access edges with ≥ 10 observations (0 beyond depth 2).
Dense scores are cosine similarities, the prior is in [0, 1] and centred on the pool mean (a candidate with no module information is shifted by exactly 0), the maximum shift is ±λ, ties break on memory id, and **λ = 0 reproduces the dense ranking exactly** (tested). The prior is a ranking nudge, not a relevance label.
No co-access edges existed in these architectures, so the graph component here is hierarchy distance only: the benchmark says nothing about co-access evidence.

## 2. Protocol (fixed in code before any strategy was evaluated)

* **Data and splits:** the same corpora, cached embeddings, query groups and metric code as the frozen benchmark. Every strategy ran through the real `RetrievalEngine` on the **initial deterministic architecture** (no adaptation), so the retrieval strategy is the only variable.
* **Tuning on validation only:** a fixed grid of 9 progressive configurations (policy × patience × step) and 13 soft configurations (λ × graph share) was run on the validation groups (403 / 323 / 258 queries). Test labels were not read in this phase (tested).
* **Pre-declared selection rules:** progressive — among configurations within 0.01 nDCG@10 of global dense on *every* dataset, choose the one examining the fewest records, else the highest macro nDCG@10; soft — the highest macro validation nDCG@10 among λ > 0, with "reliable" requiring a positive gain on every dataset and an interval excluding 0 on at least one.
* **Freeze:** the chosen configuration was written to `frozen_config.json` (SHA-256 `005fa1fb0b9ce4c2…`, recorded in the manifest) on 2026-10-25T01:42:48.071Z; the runner refuses to evaluate the test group without an intact frozen file. The test group was then evaluated once.
* **Status of each test result:** NFCorpus — confirmatory (its test split was not used for tuning; it was evaluated under benchmark v2, which did not tune these strategies). SciFact — **exploratory** (its test labels were seen under benchmark protocol v1). ArguAna — an **internal partition** of the official queries, not an official test score.
* **Control check:** the two controls reproduce the frozen v2 numbers exactly on SciFact (global 0.6451, fixed 0.5507) and NFCorpus (0.3159, 0.2771). ArguAna differs slightly (global 0.5088 here vs 0.5066 in v2) because this run excludes the 3 test queries whose judged documents are absent from the corpus; v2 scored them as 0.
* **Not done:** adaptation was not combined with these strategies; one seed; one embedding model; latency on one laptop.

## 3. Frozen configuration and how it was chosen

* **Progressive expansion:** policy `profile`, step 4 modules, patience 2, 3 seed modules, budget 12 modules / 4 steps, fallback on an unsettled budget.
  **No configuration was non-inferior to global dense on every validation dataset** (best case −0.006 / −0.007 / −0.0125 nDCG@10 on ArguAna / NFCorpus / SciFact), so the rule fell back to the highest macro nDCG@10. The cheapest configurations lose more (e.g. patience 1, step 2: −0.009 / −0.022 / −0.049, examining 21 % of the corpus per query) and the best-quality one examines 56 % of it, because it falls back to global search for 9 % / 53 % / 35 % of validation queries (ArguAna / NFCorpus / SciFact).
* **Soft graph routing:** weight λ = 0.01, graph share 0.0 (profile similarity only), pool 100. **λ = 0 (plain dense retrieval) had the best validation score**; nearly every λ > 0 lowered nDCG@10 (the only gains were +0.0001 and +0.0004 on ArguAna; macro 0.4876 at λ = 0.01 falling to 0.4672 at λ = 0.1 with graph share 1.0). The smallest-λ choice is a "least harmful" pick, and the gain over weight 0 is **not reliable** (it is negative on two datasets).
  The full grids are in `tuning.json` and on the dashboard (Benchmarks → Retrieval strategies → Frozen configuration).

## 4. Results

Per-dataset results first; the macro-average at the end gives each dataset equal weight and mixes an exploratory split, a confirmatory split and an internal partition, so read it as a summary, not as a headline.

### scifact — exploratory (test labels seen under protocol v1)

**Test group, 300 queries — quality**

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.6451 | 0.5959 | 0.7379 | 0.7833 | 0.9250 | 0.6047 | 0.0883 |
| Fixed graph | 0.5507 | 0.5098 | 0.6214 | 0.6618 | 0.7718 | 0.5201 | 0.0750 |
| Progressive expansion | 0.6324 | 0.5855 | 0.7213 | 0.7633 | 0.8977 | 0.5943 | 0.0863 |
| Soft graph routing | 0.6429 | 0.5955 | 0.7379 | 0.7750 | 0.9250 | 0.6041 | 0.0873 |

**Efficiency** (local in-process Qdrant; median of 3 repetitions per query, strategies interleaved)

| Strategy | e2e p50 (ms) | e2e p95 (ms) | retrieval p50 (ms) | records examined | pool cut | incl. fallback | fallback rate |
|---|---|---|---|---|---|---|---|
| Global dense | 19.8 | 20.3 | 16.7 | 5183 | 0.0% | 5183 | 0.0% |
| Fixed graph | 35.5 | 36.1 | 32.3 | 886 | 82.9% | 955 | 1.3% |
| Progressive expansion | 122.8 | 139.9 | 119.5 | 1250 | 75.9% | 3375 | 41.0% |
| Soft graph routing | 20.3 | 20.7 | 17.1 | 5183 | 0.0% | 5183 | 0.0% |

**Paired comparisons** (1,000 resamples, seed 13; bold = interval excludes 0)

| Comparison (second − first) | Δ nDCG@10 [95% CI] | Δ Recall@5 [95% CI] | queries improved / worse / same |
|---|---|---|---|
| Fixed graph − Global dense | **-0.0944 [-0.1270, -0.0631]** | **-0.1165 [-0.1542, -0.0800]** | 14 / 42 / 244 |
| Progressive expansion − Global dense | **-0.0127 [-0.0262, -0.0032]** | **-0.0167 [-0.0333, -0.0033]** | 2 / 6 / 292 |
| Soft graph routing − Global dense | -0.0021 [-0.0053, +0.0005] | +0.0000 [+0.0000, +0.0000] | 4 / 3 / 293 |
| Progressive expansion − Fixed graph | **+0.0817 [+0.0530, +0.1122]** | **+0.0998 [+0.0657, +0.1340]** | 36 / 14 / 250 |
| Soft graph routing − Fixed graph | **+0.0922 [+0.0613, +0.1253]** | **+0.1165 [+0.0800, +0.1542]** | 40 / 12 / 248 |

*Validation group (403 queries, development numbers: the configuration was tuned on it, so they are optimistic)*

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.6445 | 0.5963 | 0.7287 | 0.7752 | 0.9375 | 0.6070 | 0.0883 |
| Fixed graph | 0.5747 | 0.5341 | 0.6443 | 0.6834 | 0.7945 | 0.5434 | 0.0787 |
| Progressive expansion | 0.6320 | 0.5846 | 0.7163 | 0.7603 | 0.9127 | 0.5953 | 0.0868 |
| Soft graph routing | 0.6427 | 0.5942 | 0.7287 | 0.7740 | 0.9375 | 0.6047 | 0.0883 |

### nfcorpus — confirmatory

**Test group, 323 queries — quality**

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.3159 | 0.1105 | 0.1196 | 0.1550 | 0.3115 | 0.5046 | 0.2433 |
| Fixed graph | 0.2771 | 0.0900 | 0.0978 | 0.1212 | 0.2490 | 0.4802 | 0.2077 |
| Progressive expansion | 0.3143 | 0.1092 | 0.1187 | 0.1534 | 0.3025 | 0.5073 | 0.2406 |
| Soft graph routing | 0.3151 | 0.1102 | 0.1161 | 0.1545 | 0.3115 | 0.5048 | 0.2424 |

**Efficiency** (local in-process Qdrant; median of 3 repetitions per query, strategies interleaved)

| Strategy | e2e p50 (ms) | e2e p95 (ms) | retrieval p50 (ms) | records examined | pool cut | incl. fallback | fallback rate |
|---|---|---|---|---|---|---|---|
| Global dense | 15.4 | 16.0 | 11.9 | 3633 | 0.0% | 3633 | 0.0% |
| Fixed graph | 26.7 | 39.0 | 23.2 | 837 | 76.9% | 1389 | 15.2% |
| Progressive expansion | 99.2 | 104.0 | 95.8 | 1230 | 66.1% | 3075 | 50.8% |
| Soft graph routing | 15.8 | 16.4 | 12.3 | 3633 | 0.0% | 3633 | 0.0% |

**Paired comparisons** (1,000 resamples, seed 13; bold = interval excludes 0)

| Comparison (second − first) | Δ nDCG@10 [95% CI] | Δ Recall@5 [95% CI] | queries improved / worse / same |
|---|---|---|---|
| Fixed graph − Global dense | **-0.0389 [-0.0524, -0.0252]** | **-0.0218 [-0.0334, -0.0114]** | 42 / 87 / 194 |
| Progressive expansion − Global dense | -0.0016 [-0.0041, +0.0010] | -0.0009 [-0.0027, +0.0005] | 15 / 15 / 293 |
| Soft graph routing − Global dense | -0.0008 [-0.0022, +0.0004] | -0.0035 [-0.0103, +0.0000] | 15 / 32 / 276 |
| Progressive expansion − Fixed graph | **+0.0373 [+0.0239, +0.0507]** | **+0.0209 [+0.0110, +0.0325]** | 81 / 36 / 206 |
| Soft graph routing − Fixed graph | **+0.0380 [+0.0245, +0.0516]** | **+0.0183 [+0.0056, +0.0305]** | 86 / 47 / 190 |

*Validation group (323 queries, development numbers: the configuration was tuned on it, so they are optimistic)*

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.3017 | 0.0939 | 0.1003 | 0.1360 | 0.3026 | 0.5043 | 0.2402 |
| Fixed graph | 0.2672 | 0.0800 | 0.0800 | 0.1090 | 0.2502 | 0.4684 | 0.2121 |
| Progressive expansion | 0.2950 | 0.0888 | 0.0920 | 0.1275 | 0.2860 | 0.5041 | 0.2365 |
| Soft graph routing | 0.3014 | 0.0936 | 0.0996 | 0.1351 | 0.3026 | 0.5011 | 0.2406 |

### arguana — internal partition (not an official test score)

**Test group, 700 queries — quality**

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.5088 | 0.4223 | 0.6343 | 0.7857 | 0.9786 | 0.4263 | 0.0786 |
| Fixed graph | 0.4989 | 0.4144 | 0.6186 | 0.7700 | 0.9357 | 0.4183 | 0.0770 |
| Progressive expansion | 0.5081 | 0.4220 | 0.6329 | 0.7843 | 0.9786 | 0.4259 | 0.0784 |
| Soft graph routing | 0.5093 | 0.4230 | 0.6371 | 0.7857 | 0.9786 | 0.4270 | 0.0786 |

**Efficiency** (local in-process Qdrant; median of 3 repetitions per query, strategies interleaved)

| Strategy | e2e p50 (ms) | e2e p95 (ms) | retrieval p50 (ms) | records examined | pool cut | incl. fallback | fallback rate |
|---|---|---|---|---|---|---|---|
| Global dense | 36.3 | 38.1 | 29.7 | 8674 | 0.0% | 8674 | 0.0% |
| Fixed graph | 64.3 | 67.4 | 57.7 | 984 | 88.7% | 984 | 0.0% |
| Progressive expansion | 171.0 | 247.4 | 164.7 | 1215 | 86.0% | 1885 | 7.7% |
| Soft graph routing | 36.9 | 38.7 | 30.3 | 8674 | 0.0% | 8674 | 0.0% |

**Paired comparisons** (1,000 resamples, seed 13; bold = interval excludes 0)

| Comparison (second − first) | Δ nDCG@10 [95% CI] | Δ Recall@5 [95% CI] | queries improved / worse / same |
|---|---|---|---|
| Fixed graph − Global dense | **-0.0099 [-0.0188, -0.0017]** | **-0.0157 [-0.0271, -0.0043]** | 22 / 17 / 661 |
| Progressive expansion − Global dense | -0.0006 [-0.0021, +0.0003] | -0.0014 [-0.0043, +0.0000] | 2 / 1 / 697 |
| Soft graph routing − Global dense | +0.0005 [-0.0011, +0.0023] | +0.0029 [+0.0000, +0.0071] | 8 / 3 / 689 |
| Progressive expansion − Fixed graph | **+0.0093 [+0.0014, +0.0181]** | **+0.0143 [+0.0029, +0.0257]** | 16 / 20 / 664 |
| Soft graph routing − Fixed graph | **+0.0104 [+0.0027, +0.0191]** | **+0.0186 [+0.0086, +0.0300]** | 22 / 20 / 658 |

*Validation group (258 queries, development numbers: the configuration was tuned on it, so they are optimistic)*

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.5188 | 0.4247 | 0.6512 | 0.8217 | 0.9767 | 0.4263 | 0.0822 |
| Fixed graph | 0.5057 | 0.4147 | 0.6318 | 0.7984 | 0.9264 | 0.4161 | 0.0798 |
| Progressive expansion | 0.5126 | 0.4191 | 0.6434 | 0.8139 | 0.9651 | 0.4207 | 0.0814 |
| Soft graph routing | 0.5188 | 0.4248 | 0.6473 | 0.8217 | 0.9767 | 0.4267 | 0.0822 |

### Macro-average over the three test groups (equal weight per dataset; includes the exploratory and internal splits)

| Strategy | nDCG@10 | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| Global dense | 0.4899 | 0.3762 | 0.4973 | 0.5747 | 0.7383 | 0.5119 | 0.1367 |
| Fixed graph | 0.4422 | 0.3381 | 0.4459 | 0.5177 | 0.6522 | 0.4729 | 0.1199 |
| Progressive expansion | 0.4850 | 0.3722 | 0.4910 | 0.5670 | 0.7262 | 0.5092 | 0.1351 |
| Soft graph routing | 0.4891 | 0.3762 | 0.4971 | 0.5717 | 0.7383 | 0.5120 | 0.1361 |

### Why the numbers look the way they do

**Progressive expansion recovers quality largely by falling back to global search.** Queries that fall back are identical to global dense by construction, and the queries that do *not* fall back still lose quality, though much less than under the fixed graph:

| Dataset (test) | Progressive queries that fell back to global | Mean Δ nDCG@10 vs global on queries that did NOT fall back | Fixed graph on the same queries |
|---|---|---|---|
| scifact | 123 of 300 (41%) | -0.0215 (n = 177) | -0.0500 |
| nfcorpus | 164 of 323 (51%) | -0.0033 (n = 159) | -0.0269 |
| arguana | 54 of 700 (8%) | -0.0006 (n = 646) | -0.0073 |

On SciFact progressive expansion falls back for 41 % of queries, so it examines 3,375 of 5,183 records per query on average (35 % fewer) and still loses 0.0127 nDCG@10 (interval excludes 0). On NFCorpus it falls back for 51 % and examines 85 % of the corpus. ArguAna is the favourable case: 92 % of queries stop on a stable top-10 after about 1,200 records (86 % of the corpus not examined) and the nDCG@10 difference is −0.0006 (interval includes 0) — but see latency.
Routing exclusions explain the remaining loss: 15 relevant documents on SciFact sat in modules progressive expansion never searched (66 under the fixed graph), 3,050 on NFCorpus (6,714), 3 on ArguAna (39); the stability signal cannot see a relevant document that no searched module can rank into the top-10.

**Soft routing barely changes anything.** It reorders deep ranks heavily (on average 34 promoted and 29 demoted results among the top 100 on SciFact) but the top-10 is almost always the same: nDCG@10 differs from global dense by −0.0021 / −0.0008 / +0.0005, every interval includes 0, and it is a full global search, so it examines the whole corpus. It is the safest strategy and offers no measured benefit.

**Neither strategy is faster than global search.**

*Local in-process Qdrant (above):* global ≈ 20 / 15 / 36 ms end-to-end; soft ≈ the same; fixed graph ≈ 36 / 27 / 64; progressive ≈ 123 / 99 / 171, because every stage is a separate filtered search and local-mode filtering costs time proportional to the whole corpus (see `BEIR_PERFORMANCE_ANALYSIS.md`).

*Indexed Qdrant server (same engine and frozen config, 100 validation queries per dataset, retrieval only):*

| Strategy | scifact p50 / p95 (ms) | nfcorpus p50 / p95 (ms) | arguana p50 / p95 (ms) |
|---|---|---|---|
| Global dense | 0.97 / 1.01 | 0.94 / 1.82 | 1.06 / 1.10 |
| Fixed graph | 1.82 / 1.89 | 1.79 / 2.86 | 1.82 / 1.89 |
| Progressive expansion | 5.07 / 6.18 | 5.69 / 6.63 | 4.28 / 6.34 |
| Soft graph routing | 1.75 / 1.82 | 1.67 / 2.58 | 2.00 / 2.06 |

Global dense is the fastest in every dataset (about 1 ms). Fixed graph and soft routing take about 1.8 ms (two server calls instead of one); progressive expansion takes 4–6 ms (one call per stage plus fallback). The candidate-pool reduction does not buy speed at these corpus sizes; the top-10 returned on the server is identical to local Qdrant for every strategy (100/100 queries).
Scaling to far larger corpora was not tested and cannot be inferred from these numbers.

## 5. Conclusions and recommendation

1. **Hard module filtering is not justified by this evidence.** The fixed graph loses a reliable 0.010–0.094 nDCG@10 on all three datasets and gains nothing measurable in return.
2. **Progressive expansion is the only strategy that keeps a real candidate reduction, but at a cost:** a reliable loss on SciFact, indistinguishable on NFCorpus and ArguAna, 4–6× the latency of global search on an indexed server, and a fallback rate that makes the average reduction small. It could be revisited for much larger corpora or a store whose cost scales with the searched set; neither was tested.
3. **Soft routing is harmless and useless here:** with the best validation setting its effect is statistically indistinguishable from zero.
4. **Recommendation:** do not claim a retrieval-quality or latency advantage for graph-based routing. Keep global dense retrieval as the default, keep the graph for what it demonstrably does well (an inspectable, versioned, rollback-safe memory architecture), and treat routing as an experimental option whose benefit would have to be re-established on a much larger corpus and a different routing signal.

## 6. Limitations

* One embedding model (MiniLM), one clustering (KMeans, seed 13), one seed; intervals reflect query sampling noise only, and latency has no intervals.
* Three small corpora (3.6–8.7 k documents), where a brute-force scan costs about 1 ms; a benefit at 10⁶ documents is neither shown nor excluded.
* Grids were small and fixed in advance (including the progressive budget, which was not tuned); a different budget or a learned routing signal could behave differently.
* The strategies were evaluated on the initial architecture only; their interaction with adapted architectures was not measured.
* No co-access edges existed, so the graph prior uses hierarchy only.
* SciFact is exploratory and ArguAna an internal partition, as marked above.

## 7. Reproduce

```bash
cd backend      # MongoDB running; BEIR datasets cached in data/beir
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id <new id> --phase tune        # validation only; writes frozen_config.json
../.venv/bin/python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id <same id> --phase evaluate   # validation + test, refuses without an intact frozen config
# optional, with a Qdrant server on 127.0.0.1:6343 (see SETUP.md / BEIR_PERFORMANCE_ANALYSIS.md):
../.venv/bin/python -m app.evaluation.run_strategy_latency --run-id <same id>
```
The run takes about 3 hours on an Apple-silicon laptop (tuning, then the timed evaluation). Never reuse a run id; never run two jobs with the same id (the runner refuses an existing id for the tune phase).
