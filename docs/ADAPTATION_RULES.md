# Structural adaptation rules

Structural adaptation changes **how memory is organised** (modules and links). It is separate from compression, which reduces
redundant text. Every threshold below is a setting (see `backend/.env.example`), not a constant in an algorithm.

## Signals and windows
Each completed, tracked query records: seed modules, expanded modules, returned memory ids and their modules, candidate count,
latency, fallback flag and architecture version. Counters for a window (`ADAPTATION_QUERY_BATCH`, default 50) are kept in MongoDB:
query count, fallback count, candidate sum, per-module use and **module-pair co-access** (only for *successful* queries: ≥ 1 result
and best score ≥ `FALLBACK_MIN_SCORE`). When the window fills, detection runs once (background thread, or inline in tests);
at most `ADAPTATION_MAX_OPS_PER_WINDOW` (default 1) operations are applied; recently rejected proposals are skipped for
`ADAPTATION_REJECTED_COOLDOWN_WINDOWS` windows. These are measurements, not proof of a root cause.

## Split
*Trigger* — a content module with ≥ `SPLIT_CAPACITY_THRESHOLD` records (default 1000), **or** ≥ `SPLIT_MIXED_MIN_RECORDS` (16) records
whose best grouping has silhouette ≥ `SPLIT_MIXED_MIN_SILHOUETTE` (0.10).
*Plan* — candidate groupings of the existing embeddings: by source, by section, KMeans k = 2…`SPLIT_MAX_GROUPS`. A grouping is valid only if
it has 2–4 groups, every group ≥ `SPLIT_MIN_GROUP_SIZE` (4) and cosine **silhouette ≥ `SPLIT_MIN_SILHOUETTE`** (0.08); metadata
groupings win ties within 0.03. Clustering output is never trusted without the silhouette check; incoherent splits yield no proposal.
*Result* — child modules (named from source / section / TF-IDF keywords) with recomputed centroid profiles; the original becomes a
routing-only **container** (hierarchy parent, no profile); shortcut edges that touched it move to the child nearest the neighbour's
profile. Memory ids never change.

## Merge
Candidate pairs among content modules that are not hierarchically related. A merge is **eligible only if**
profile cosine ≥ `MERGE_PROFILE_SIMILARITY` (0.85) **and** ≥ `MERGE_OVERLAP_MIN` (30 %) of the smaller module's records have a near-duplicate
(cos ≥ `MERGE_PAIR_SIMILARITY`) in the other **and** the overlapping pairs have ≤ `MERGE_MAX_CONFLICTS` (0) numeric-fact conflicts
(each side states a number — port, percentage, duration, version — the other lacks). Shared keywords/sources and existing co-access
are recorded as supporting evidence. Similarity alone never approves a merge. The larger (then older) module survives; all records
move (nothing is dropped, duplicates are left for compression); edges are re-pointed without self-loops or duplicates.

## Shortcut edge
A `co_access` edge is proposed for a pair seen together ≥ `COACCESS_MIN_COUNT` (10) times in a window of ≥ `COACCESS_MIN_WINDOW_QUERIES` (20),
unless an equivalent edge exists, `MAX_EDGES_TOTAL` (60) or `MAX_COACCESS_PER_NODE` (4) would be exceeded. The edge stores
`co_access_count`, `weight = count / window_queries`, creation time and the supporting evidence. Proposals are ranked by co-access count.

## What happens to a proposal
`plan (pure)` → integrity validation of the candidate → evaluation of baseline and candidate on the labeled dataset → policy decision
→ (rejected: stop, nothing changed) → exclusive commit with checkpoint, convergence, integrity check, post-commit verification,
new version → (any failure: recovery to the checkpoint). Details and thresholds: `EVALUATION_AND_ROLLBACK.md`.
Each event records the triggering signal, evidence, thresholds, affected modules/records, baseline metrics and the outcome in plain text.
