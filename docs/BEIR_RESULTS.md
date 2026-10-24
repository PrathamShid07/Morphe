# BEIR results

Every number below was read from the saved artifacts in `backend/artifacts/beir/<dataset>/<run_id>/` (git-ignored; regenerate with the commands in
`BEIR_BENCHMARK_METHODOLOGY.md`). Nothing here is copied from a paper or estimated. Read **Chronology and caveats** before quoting any figure.

**Headline finding.** On all three datasets the graph-routed retrieval (fixed and adaptive) scores *lower* nDCG@10 than global dense retrieval, with
paired 95 % intervals that exclude zero. Routing cuts the eligible candidate pool by 77–89 %, but on this local Qdrant backend it does **not** reduce
latency: it is slower. Validated adaptation produced no measurable gain: on SciFact nothing was accepted, and on NFCorpus and ArguAna one split was
accepted whose effect on the test split is zero or slightly negative. The safety machinery (reject a harmful candidate, roll back a faulty commit,
restore a previous version) worked on every dataset.

## Runs

| Run id | Dataset(s) | Protocol | Test group | Wall time | Notes |
|---|---|---|---|---|---|
| `beir_scifact_full_01` | SciFact | v1 | official test, 300 q | — | first run; seen before v2 was designed |
| `beir_full_v2_01` | SciFact | v2 | official test, 300 q | 1,731 s | **post-hoc re-run** (see below) |
| `beir_full_v2_01` | NFCorpus | v2 | official test, 323 q | 1,361 s | clean test of v2 |
| `beir_full_v2_01` | ArguAna | v2 | **internal** 50 % partition, 703 q | 3,177 s | not the official 1,406-query score |
| two `beir_2026…` runs | SciFact | v2 | `--query-limit` smoke | — | pipeline checks; non-official, not used below |

Environment (from the manifests): macOS arm64, 10 CPU cores, 16 GB RAM, Python 3.12.14, torch 2.14.1 on `mps`, sentence-transformers 6.2.0,
BEIR 2.2.0, pytrec-eval-terrier 0.5.11, qdrant-client 1.20.0 (local in-process mode), embedding model `all-MiniLM-L6-v2` (384-d, max 256 tokens), seed 13.
All methods ran sequentially on one machine, so absolute latencies are specific to this laptop.

## Datasets and splits

| Dataset | Corpus docs | Test queries | Test qrels | Workload q | Validation q | Official test split? |
|---|---|---|---|---|---|---|
| SciFact | 5,183 | 300 | 339 | 403 | 150 | yes |
| NFCorpus | 3,633 | 323 | 12,334 | 500 | 150 | yes |
| ArguAna | 8,674 | 703 | 703 | 370 | 150 | **no** — internal 30/20/50 partition (ArguAna ships only one split) |

Workload queries use no labels; validation labels are used only inside candidate evaluation; test labels are not read until the architecture is frozen.
The initial graph is built from embeddings only (KMeans), so it does not depend on any relevance label: 52 modules/7 groups (SciFact),
36/6 (NFCorpus), 87/9 (ArguAna), about 100 documents per module.
ArguAna validation (5 qrels point at documents absent from the corpus) is recorded as a caveat; BEIR keeps those judgments and so do we.

## Retrieval quality (test group, BEIR/pytrec_eval, graded relevance kept)

★ = headline metric. The BEIR aggregate and our per-query values agree to ≤ 5×10⁻⁶ on every run (`beir_vs_per_query_max_gap`).

### SciFact (official test, 300 queries)
| Method | nDCG@10 ★ | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| BM25 (local Okapi) | 0.6634 | 0.6180 | 0.7259 | 0.7876 | 0.8826 | 0.6309 | 0.0870 |
| Global dense | 0.6451 | 0.5959 | 0.7379 | 0.7833 | 0.9250 | 0.6047 | 0.0883 |
| Fixed graph | 0.5507 | 0.5098 | 0.6214 | 0.6618 | 0.7718 | 0.5201 | 0.0750 |
| Adaptive graph (v2) | 0.5507 | 0.5098 | 0.6214 | 0.6618 | 0.7718 | 0.5201 | 0.0750 |

### NFCorpus (official test, 323 queries)
| Method | nDCG@10 ★ | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| BM25 (local Okapi) | 0.3059 | 0.1175 | 0.1196 | 0.1486 | 0.2494 | 0.5146 | 0.2155 |
| Global dense | 0.3159 | 0.1105 | 0.1196 | 0.1550 | 0.3115 | 0.5046 | 0.2433 |
| Fixed graph | 0.2771 | 0.0900 | 0.0978 | 0.1212 | 0.2490 | 0.4802 | 0.2077 |
| Adaptive graph | 0.2752 | 0.0892 | 0.0974 | 0.1209 | 0.2517 | 0.4717 | 0.2081 |

### ArguAna (internal partition, 703 queries — **not comparable to published ArguAna test scores**)
| Method | nDCG@10 ★ | MAP@10 | Recall@5 | Recall@10 | Recall@100 | MRR@10 | P@10 |
|---|---|---|---|---|---|---|---|
| BM25 (local Okapi) | 0.4560 | 0.3697 | 0.5590 | 0.7354 | 0.9431 | 0.3731 | 0.0735 |
| Global dense | 0.5066 | 0.4205 | 0.6316 | 0.7824 | 0.9744 | 0.4245 | 0.0782 |
| Fixed graph | 0.4967 | 0.4126 | 0.6159 | 0.7667 | 0.9317 | 0.4166 | 0.0767 |
| Adaptive graph | 0.4957 | 0.4121 | 0.6145 | 0.7639 | 0.9317 | 0.4160 | 0.0764 |

Sanity check: the BM25 and global-dense baselines are in the range published for BEIR on SciFact and NFCorpus. That check is by eye against
published figures; the ArguAna rows use a different query set and are not comparable.

## Paired query-level bootstrap (1,000 resamples, seed 13, unit = query, percentile 95 % interval)

Differences are second minus first. Counts are queries improved / regressed / unchanged in nDCG@10.

| Dataset | Comparison | Δ nDCG@10 [95 % CI] | impr/regr/same | Δ Recall@5 [95 % CI] |
|---|---|---|---|---|
| SciFact | adaptive − fixed | +0.0000 [0.0000, 0.0000] | 0/0/300 | +0.0000 [0.0000, 0.0000] |
| SciFact | fixed − global | −0.0944 [−0.1270, −0.0631] | 14/42/244 | −0.1165 [−0.1542, −0.0800] |
| SciFact | adaptive − global | −0.0944 [−0.1270, −0.0631] | 14/42/244 | −0.1165 [−0.1542, −0.0800] |
| SciFact | global − BM25 | −0.0184 [−0.0558, +0.0221] | 66/73/161 | +0.0120 [−0.0349, +0.0596] |
| NFCorpus | adaptive − fixed | −0.0019 [−0.0061, +0.0020] | 9/14/300 | −0.0004 [−0.0014, +0.0003] |
| NFCorpus | fixed − global | −0.0389 [−0.0524, −0.0252] | 42/87/194 | −0.0218 [−0.0334, −0.0114] |
| NFCorpus | adaptive − global | −0.0408 [−0.0550, −0.0267] | 42/90/191 | −0.0222 [−0.0344, −0.0120] |
| NFCorpus | global − BM25 | +0.0100 [−0.0117, +0.0318] | 115/96/112 | +0.0000 [−0.0161, +0.0165] |
| ArguAna | adaptive − fixed | −0.0011 [−0.0025, −0.0001] | 0/4/699 | −0.0014 [−0.0043, +0.0000] |
| ArguAna | fixed − global | −0.0098 [−0.0185, −0.0024] | 22/17/664 | −0.0156 [−0.0256, −0.0057] |
| ArguAna | adaptive − global | −0.0109 [−0.0195, −0.0035] | 19/18/666 | −0.0171 [−0.0270, −0.0071] |
| ArguAna | global − BM25 | +0.0505 [+0.0252, +0.0778] | 280/198/225 | +0.0725 [+0.0341, +0.1138] |

Reading it:
* **Routing hurts quality.** fixed − global is negative with a CI excluding zero on all three datasets (SciFact is the largest, −0.094 nDCG@10).
* **Adaptation did not help.** adaptive − fixed is exactly zero on SciFact (no change was accepted). On NFCorpus the interval contains zero. On ArguAna the
  interval is entirely negative but tiny (−0.0011, four queries regress, none improve); we report it as a small real regression, not as noise.
* **Dense vs BM25** is significant only on ArguAna (internal partition). On SciFact and NFCorpus the intervals contain zero. This is a baseline observation, not a MORPHE claim.
* Intervals describe resampling variability of the test queries only. They do not capture variation from other seeds, splits, or graph initialisations, and nothing was run with multiple seeds.

## Latency and candidate pool (test group, ms)

Median of 3 repetitions per query after one warm-up; query embedding timed separately; end-to-end = embedding + retrieval. BM25 is in-process Python and is shown
for scale only. Hardware: see Environment.

### SciFact
| Method | e2e p50 | e2e p95 | retrieval p50 / p95 / mean | embed p50 | eligible docs (mean) | pool cut vs corpus | fallback rate |
|---|---|---|---|---|---|---|---|
| BM25 | 0.58 | 0.60 | 0.58 / 0.60 / 0.57 | — | 5,183 | 0 % | — |
| Global dense | 22.80 | 26.44 | 18.98 / 22.03 / 19.55 | 3.57 | 5,183 | 0 % | 0 % |
| Fixed graph | 39.71 | 43.37 | 36.02 / 38.47 / 36.64 | 3.57 | 885.9 | 82.9 % | 1.3 % |
| Adaptive graph | 40.11 | 43.41 | 36.30 / 39.65 / 36.97 | 3.57 | 885.9 | 82.9 % | 1.3 % |

### NFCorpus
| Method | e2e p50 | e2e p95 | retrieval p50 / p95 / mean | embed p50 | eligible docs (mean) | pool cut vs corpus | fallback rate |
|---|---|---|---|---|---|---|---|
| BM25 | 0.34 | 0.36 | 0.34 / 0.36 / 0.32 | — | 3,633 | 0 % | — |
| Global dense | 17.05 | 21.63 | 13.49 / 15.77 / 13.94 | 3.50 | 3,633 | 0 % | 0 % |
| Fixed graph | 29.09 | 44.20 | 25.52 / 39.25 / 27.66 | 3.50 | 837.5 | 77.0 % | 15.2 % |
| Adaptive graph | 28.93 | 43.61 | 25.39 / 38.83 / 27.36 | 3.50 | 769.0 | 78.8 % | 15.5 % |

### ArguAna
| Method | e2e p50 | e2e p95 | retrieval p50 / p95 / mean | embed p50 | eligible docs (mean) | pool cut vs corpus | fallback rate |
|---|---|---|---|---|---|---|---|
| BM25 | 0.72 | 0.77 | 0.72 / 0.77 / 0.72 | — | 8,674 | 0 % | — |
| Global dense | 39.00 | 56.19 | 32.10 / 45.68 / 34.18 | 6.75 | 8,674 | 0 % | 0 % |
| Fixed graph | 67.99 | 82.42 | 61.08 / 73.57 / 63.19 | 6.75 | 984.5 | 88.7 % | 0 % |
| Adaptive graph | 67.67 | 81.30 | 60.63 / 72.35 / 62.92 | 6.75 | 971.2 | 88.8 % | 0 % |

Where the time goes (mean ms): on SciFact fixed graph spends 0.20 seeding, 0.04 expansion, 36.13 in the Qdrant search and 0.25 in fallback. So the routing logic
itself is cheap; the cost is the module-filtered vector search.

**Candidate pool is not work done.** "Eligible docs" is the number of active documents inside the searched modules (plus the whole corpus when fallback ran for a query
— `mean_candidates_incl_fallback` in `latency.json` is 955 / 1,389 / 985 for the fixed graph). It is not a count of vectors the backend scanned. Local-mode Qdrant applies
a payload filter over its stored points rather than using a pre-partitioned index, so a smaller eligible pool does not translate into a faster search here. Whether
a server-mode Qdrant with a payload index, or per-module collections, would turn the pool reduction into a speed-up was **not tested**.

Other system numbers: process RSS 781 / 762 / 849 MB (SciFact / NFCorpus / ArguAna); embedding cache 7.6 / 5.3 / 12.7 MB; initial-graph build 1.05 / 0.59 / 2.28 s;
ingestion 0.93 / 1.07 / 1.59 s; corpus embedding 17.7 s on NFCorpus (SciFact and ArguAna were served from the embedding cache, hence 0.0 s). Graph size, initial → adaptive:
SciFact 59 nodes / 52 edges → unchanged; NFCorpus 42/36 → 46/40; ArguAna 96/87 → 98/89.

## Adaptation evidence (validation group only)

| Dataset | Protocol | Proposals evaluated | Accepted | Rejected | Failed | Rolled back | Version before → after |
|---|---|---|---|---|---|---|---|
| SciFact | v1 | 13 | 6 | 7 | 0 | 0 | — |
| SciFact | v2 | 30 | 0 | 30 | 0 | 0 | `arch_0002` → `arch_0002` |
| NFCorpus | v2 | 25 | 1 | 24 | 0 | 0 | `arch_0002` → `arch_0003` |
| ArguAna | v2 | 27 | 1 | 26 | 0 | 0 | `arch_0002` → `arch_0003` |

* **Every evaluated proposal was a `split_module`.** The detector never surfaced a merge or shortcut proposal: no module pair reached the co-access threshold in any run, and
  `co_access_edges` stayed 0. So these runs do **not** exercise merge or shortcut adaptation on BEIR data. Those operations are covered only by the repository's own unit/integration tests.
* Rejected, failed and rolled-back are separate statuses. Here "rejected" means a candidate failed validation before any commit; nothing in the real runs failed or needed rollback.
* Under v1, six splits were accepted on SciFact. The test effect was adaptive − fixed = −0.0029 nDCG@10 [−0.0179, +0.0110]: indistinguishable from zero, so the accepted changes were noise-level, which is what
  motivated v2 (below). Under v1 the adaptive p95 was 65.7 ms vs 55.3 ms for fixed — an unpaired, single-repetition measurement that v2 replaced.
* The single accepted split on each of NFCorpus and ArguAna passed every guardrail and the benefit requirement on validation, but on thin margins (exact checks in each run's `adaptation.json`):
  * NFCorpus — split of `M000` (153 records): validation nDCG@10 −0.0068, Recall@5 −0.0015, MRR@10 −0.0078 (all within the 0.02 allowance); accepted because the mean eligible pool fell 1,233 → 1,166 (−5.5 %, threshold 5 %).
  * ArguAna — split of `M007` (163 records): validation quality unchanged (+0.0000 on all three); accepted because p95 fell 71.4 → 67.6 ms (−5.3 %, threshold 5 %). A 0.3-point margin on a latency measurement is within what I would
    expect from timing noise, so I would not call this a real latency benefit.
  On the test group these splits changed nDCG@10 by −0.0019 (CI includes 0) and −0.0011 (CI excludes 0, negative). So a validation-time "benefit" did not carry over as a quality benefit; in both cases the
  acceptance rested on a marginal efficiency criterion, and the policy's quality guard did not catch a small loss.
* Live-vs-sandbox parity: the production retrieval path returned an identical top-10 to the evaluation sandbox for 40/40 sampled queries on SciFact and 37/40 on both NFCorpus and ArguAna. I did not investigate
  the three mismatches per dataset, so I cannot say whether they are tie-ordering or a real divergence. That is an open item.

## Safety evidence (real benchmark corpus, after test evaluation)

| Dataset | Harmful candidate (nDCG@10 baseline → candidate) | Outcome | Post-commit fault | Restore round-trip |
|---|---|---|---|---|
| SciFact | 0.5882 → 0.1412 | rejected at `pre_commit`, live state unchanged | `rolled_back`, exact state restored, integrity passed | completed; matches pre-change snapshot |
| NFCorpus | 0.2481 → 0.1565 | rejected at `pre_commit`, live state unchanged | `rolled_back`, exact state restored, integrity passed | completed; matches pre-change snapshot |
| ArguAna | 0.3571 → 0.0384 | rejected at `pre_commit`, live state unchanged | `rolled_back`, exact state restored, integrity passed | completed; matches pre-change snapshot |

These probes inject a deliberately bad candidate (records shuffled, routing profiles left stale) and a fault after commit. They show the guards and persistent rollback operate on real benchmark data;
they are not evidence that the detector proposes good changes. The "harmful" candidate baselines are validation-group nDCG@10 for the architecture at that moment, not test scores.

## Routing-breadth sweep (validation group; informational only)

nDCG@10 (mean eligible docs, p95 ms). The sweep did not choose the configuration used above (3 seeds, ≤ 8 modules was fixed beforehand).

| Config | SciFact | NFCorpus | ArguAna |
|---|---|---|---|
| Global dense (no routing) | 0.6605 (5,183; 16.7) | 0.2831 (3,633; 14.7) | 0.3649 (8,674; 28.1) |
| seeds 3, cap 8 | 0.5882 (904; 32.6) | 0.2549 (1,233; 38.2) | 0.3571 (965; 54.2) |
| seeds 5, cap 12 | 0.6170 (1,266; 34.1) | 0.2591 (1,630; 39.6) | 0.3635 (1,382; 56.6) |
| seeds 8, cap 20 | 0.6410 (2,045; 44.2) | 0.2787 (2,383; 41.2) | 0.3579 (2,169; 63.6) |
| seeds 12, cap 32 | 0.6474 (2,978; 44.0) | 0.2820 (3,195; 43.3) | 0.3607 (3,341; 75.4) |

Quality approaches global dense only when the pool grows to roughly 55–90 % of the corpus, and latency stays above global dense in every row. Single seed, validation only.

## Chronology and caveats

1. **SciFact v2 numbers are a post-hoc re-run.** Protocol v1 was run once on the official SciFact test split. I inspected that result and the validation events behind it, found four defects
   in the validation mechanics (noise-level benefits, unpaired latency, drift across steps, only split proposals), and wrote protocol v2 (`BEIR_BENCHMARK_PLAN.md` addendum) *before* running NFCorpus and ArguAna.
   Retrieval method, splits and guardrail thresholds were not changed. SciFact test labels had already been seen under v1, so SciFact/v2 is not a clean held-out evaluation. NFCorpus and ArguAna are the clean ones.
   The v1 → v2 change moved SciFact from "6 noisy splits accepted" to "none accepted"; the routing result itself (−0.094) did not change.
2. **ArguAna is an internal split.** Its 703 test queries are a seeded half of the 1,406 official queries. Do not compare with published ArguAna scores.
3. **Small validation groups.** 150 validation queries per dataset make each accept/reject decision statistically weak; the CI-gated benefit rule is a response to that, not a cure.
4. **Latency is a property of local-mode Qdrant on one laptop**, with `mps` embeddings, sequential runs and no isolation from other processes. It says nothing about server deployments, other hardware or larger corpora.
5. **Single seed, single embedding model.** Nothing was repeated across seeds; the intervals above are query resampling only.
6. **BM25 is a local Okapi implementation** (no stemming, no stop-words), not a published Elasticsearch/Pyserini run.
7. **Only split proposals were exercised** (see Adaptation evidence).
8. **Smoke runs** (`--query-limit`) are non-official and are excluded from every table here.
9. **BEIR packaging:** BEIR 2.2.0 imports `requests` without declaring it; `requests` was added to `backend/requirements.txt`.
10. The initial KMeans graph is a plain content clustering; it was not tuned. A better initial partition (or a retrieval policy that routes by learned affinity) might behave differently. That was not explored.

## What this does and does not show

It shows a working, reproducible, leakage-controlled BEIR harness bound to the real MORPHE engine, with honest negative results: the current default graph routing loses retrieval quality against flat dense
search, adaptation under a strict acceptance policy mostly declines to change anything, and the safety/rollback mechanisms hold. It does **not** show that MORPHE improves retrieval, reduces latency, or that
merge/shortcut adaptation helps on these corpora.

---

## Addendum — Phase 2 follow-up (added 2026-10-25; the tables above are unchanged and remain the immutable v1/v2 results)

Three statements above are qualified by later investigation. The baseline artifacts were not modified (they are read-only and hashed in `docs/baselines/beir_v1_v2_freeze.json`).

1. **Live-vs-sandbox parity ("37/40", open item above).** Resolved in `BEIR_PARITY_INVESTIGATION.md`: the two engines are identical on every query of all three datasets (0 divergences over 300 / 323 / 703 test queries).
   The reported mismatches came from the parity check itself, which broke exact score ties between duplicate documents with two different rules. The retrieval metrics were not affected by that check. A separate real determinism gap (insertion-order-dependent tie selection at the search limit) was found and fixed for future runs.
2. **Latency.** The finding that graph routing was slower is explained in `BEIR_PERFORMANCE_ANALYSIS.md`: in Qdrant's local mode a filtered search costs more than an unfiltered one regardless of the eligible-pool size, and the redundant `status` filter also made the *global* baseline about 10× slower than an unfiltered search.
   The latency tables above describe what was run and are correct for that configuration, but they should not be read as a statement about routing in general. With an indexed server deployment every search takes about 1 ms and graph retrieval is still slightly slower than global.
3. **Why quality drops.** `BEIR_ROUTING_FAILURE_ANALYSIS.md` (validation group, diagnostic): relevant documents are excluded because the module closest to the query is often not the module that holds the answer, and hierarchy expansion adds little beyond the seed modules. No retrieval strategy has been changed or evaluated yet, and no new test-split results exist.

Diagnostic runs (`diag_03`, `perf_01`, `parity_04`) are analyses, not benchmark runs, and use the validation group or query text only.
