# Evaluation, acceptance, versioning and rollback

## The evaluation dataset
`data/evaluation_queries.json` — 59 human-authored queries (43 `dev`, 16 `heldout`) over the sample corpus, each with the expected
relevant **memory ids**. Relevance is defined by an *evidence snippet* that must occur in exactly one chunk of the named source;
`backend/scripts/build_eval_labels.py` resolves snippets to the stable ids (`mem_<source-hash>_<chunk>`), fails on ambiguity, and writes
`docs/evaluation_label_review.md` listing every label with the chunk text. The labels were generated mechanically from my evidence
snippets and I read the review file; cross-topic queries (tag `cross`, e.g. q050–q053) are the loosest labels. They are *not* ground
truth in a research sense. Queries whose labeled memories are not active (e.g. the Python-notes queries before the `mixed` scenario) are
skipped and counted; fewer than `MIN_EVAL_QUERIES` usable queries makes any evaluation **invalid**, and invalid evaluations are never
a successful validation. A malformed fixture is reported and can never replace a good dataset in MongoDB.

## Metrics (`evaluation/metrics.py`)
* **Recall@5** — per query, |relevant ∩ top-5| / |relevant|, averaged.
* **MRR** — per query 1 / rank of the first relevant result within the top 5 (0 if none), averaged.
* **p95 latency** — 95th percentile (linear interpolation) of per-query latency. Each query is warmed up once and then timed
  `EVAL_REPETITIONS` times; the per-query value is the median. Query embedding is excluded (identical for both architectures).
* **Mean candidate count**, **fallback rate**, and guided-only Recall/MRR (before the global fallback can mask routing misses).
Every report states its sample size, dataset hash, top-k, repetitions and the exact candidate-count definition.

## Acceptance policy (`evaluation/version_comparator.py`)
A candidate replaces the active architecture only if **all** checks pass (all values configurable; the UI shows them):

| Check | Default |
|---|---|
| same dataset hash and same queries for baseline and candidate | required |
| enough valid evaluation data (`MIN_EVAL_QUERIES`) | 10 |
| Recall@5 regression | ≤ 0.02 absolute |
| MRR regression | ≤ 0.02 absolute |
| p95 latency regression | ≤ +10 % **or** ≤ `LATENCY_NOISE_FLOOR_MS` (1 ms) absolute |

The noise floor exists because at this scale p95 is on the order of a millisecond, where scheduler jitter alone exceeds 10 %.
`validation_override` is accepted **only** when `ALLOW_VALIDATION_OVERRIDE=true` and not in production, and it can bypass only the
"insufficient evaluation data" check — never a regression, never an integrity check.

## Candidate evaluation is isolated and real
The candidate `ArchitectureState` is turned into an in-memory Qdrant store + graph and queried by the production `RetrievalEngine`
(seed selection, graph expansion, filtered search, fallback). Tests prove (a) production state is byte-identical afterwards,
(b) the sandbox of the live state reproduces live rankings exactly, (c) a deliberately harmful candidate (records shuffled, stale
routing profiles, fallback disabled) regresses Recall@5 by > 0.1 and is rejected, leaving the version pointer, graph, assignments
and version list unchanged. Note the global fallback *protects recall by design*, which is why guided-only metrics are also reported.

## Commit, verification and recovery
1. exclusive mutation lock; the live state must still equal the evaluated baseline (else `409 stale_plan`);
2. **checkpoint** — the current live architecture is saved as a version (skipped if it already equals the active version);
3. `converge_to(candidate)` — Qdrant payloads + profiles, MongoDB documents, live graph;
4. cross-store integrity check (Qdrant ↔ MongoDB ↔ graph, profiles ↔ content modules, token totals, no `pending` records);
5. **post-commit verification** — a *live* evaluation must reproduce the sandbox prediction (Recall, MRR, candidates, fallback rate equal to 1e-6)
   and must not regress against the baseline;
6. new version snapshot + manifest (parent = checkpoint, evaluation, validation), version pointer moved last.
Any exception in 3–6 converges back to the checkpoint **inside the same lock**. Status is then `rolled_back` if the change had been
committed, `failed` if it never became active; `recovered` records whether recovery succeeded. If recovery itself fails, `needs_recovery`
is persisted and `/health` reports `degraded`; the next startup repairs it. A change is **never reported as completed unless every step succeeded.**

*Rejection before commit* (`rejected`, `rejection_stage: pre_commit`) and *rollback after commit* (`rolled_back`) are different states in
the API and the UI. Compression is never rolled back by a structural restore.

## Monitoring regression
After each window (and on `POST /adaptation/monitor`) the active version is compared with its parent — both aligned to the
*current* memories and evaluated on the same dataset. If the policy rejects it, the system restores the parent through the normal restore path
and marks the original event `rolled_back`. For this monitor the latency tolerance is 3× the commit threshold, to absorb sandbox jitter.
Live p95 from production traffic is shown on the dashboard but is not used to trigger rollbacks.

## Versions
Manifest (lightweight) + snapshot (checksummed full state, stored separately): modules with routing profile vectors, assignments,
typed edges, counts, parent version, reason, operation id, evaluation and validation. Versions are created for: the empty baseline, a baseline after seeding,
checkpoints before any mutation, accepted structural operations, and restores — **not** for every ingestion (a checkpoint captures any
drift first). `restorable` is recomputed from the checksum on every read; a corrupt snapshot cannot be restored (tested).

## Restore
`GET /versions/{id}/restore-preview` shows the target, the version being replaced, structural differences and reconciliation counts.
`POST /versions/{id}/restore`: verify snapshot → checkpoint current → reconcile → `converge_to` → integrity check → new version
(`restored_from`) → mark the adaptation events of undone versions `rolled_back`. Failure re-applies the checkpoint. Idempotent for a repeated `operation_id`; audit events are never deleted.

**Reconciliation rule**: only memories that are *active now* are placed. Memories compressed, superseded or deleted since the snapshot are **not revived**;
memories created after the snapshot stay in their current module if it exists in the target, otherwise go to the nearest module by profile similarity
(`mod_unassigned` if none); profiles of changed modules are recomputed; empty leaf modules are dropped.

## Startup reconciliation (`reconcile.py`)
Interrupted operations are marked failed; an unfinished structural change is rolled back to its checkpoint; `pending` records are removed;
Qdrant is treated as the source of truth for active vectors/modules and MongoDB metadata is aligned; half-finished summary replacements are
completed (roll-forward); module counts/profiles are recomputed; dangling edges dropped; the graph is rebuilt from MongoDB; a baseline version is created if none exists.

## BEIR profile (nDCG@10, benefit requirement, drift guard)

The acceptance policy has two profiles. `legacy` (default, used by the sample-corpus demo) guards Recall@5 and MRR@5. `beir` (used by the BEIR benchmark,
`acceptance_profile=beir`) guards **nDCG@10**, **Recall@5** and **MRR@10** (each ≤ 0.02 absolute regression, graded judgments kept for nDCG) plus p95 latency (≤ +10 % or ≤ 1 ms), and additionally supports:

* **`require_benefit`** — a change that merely avoids harm is rejected: it must improve nDCG@10 or Recall@5 by ≥ `min_quality_gain` (0.005), cut the mean eligible-candidate pool by ≥ `min_candidate_reduction_pct` (5 %), or improve p95 by ≥ `min_latency_gain_pct` (5 %, above the noise floor). There is no neutral-change policy.
* **`benefit_requires_ci`** (protocol v2) — a quality gain only counts as a benefit if the paired query-level bootstrap 95 % interval of the improvement on the validation queries is above zero. With ≤ 150 validation queries a single query moves Recall@5 by ≥ 0.0067, so an unqualified +0.005 threshold is noise.
* **`cumulative_guard`** (protocol v2) — in addition to the comparison with the immediate parent, the candidate must stay within the same guardrails relative to the **initial** architecture, so a chain of individually acceptable steps cannot drift into a regression.
* **`eval_interleaved`** (protocol v2) — baseline and candidate are timed alternately per query (rotating order, 3 repetitions, median), so p95 comparisons of near-identical architectures are paired instead of two noisy independent passes.

`Evaluator.evaluate_states` implements the interleaving; `AcceptancePolicy.decide(reference=…)` implements the cumulative guard. The monitoring rollback (`monitor_regression`) compares the active version with its parent with 3× latency tolerance and without the benefit requirement (a neutral change is not a regression).

Benchmark-only seams: `AdaptationService.process(plan_transform=…, fault="post_commit")` inject a harmful candidate or a post-commit failure so that rejection, rollback and restoration can be demonstrated on a real corpus. They are not exposed by any API route.
All BEIR-specific settings default to the previous behaviour (`legacy`, no benefit requirement, no CI, no cumulative guard, sequential timing), so the sample-corpus demo is unchanged.
