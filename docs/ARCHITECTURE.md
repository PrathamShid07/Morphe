# Architecture

## Storage responsibilities (one owner per fact)

| Store | Owns | Never |
|---|---|---|
| **Qdrant** (`morphe_memories`) | active vectors + searchable text payload, `module_id`, `status`, pin/importance, `summary_of` | audit history |
| **Qdrant** (`morphe_module_profiles`) | one routing vector (centroid) per *content* module | containers without records |
| **MongoDB** | sources, memory lifecycle (access counts, status, previews), modules, graph edges, query/adaptation/compression events, evaluation queries and runs, architecture **versions + snapshots**, operation journal, system metrics, window counters | embeddings |
| **NetworkX** `MultiGraph` | the live module graph used for routing (nodes = modules, typed edges `hierarchy` / `co_access`) | the only copy of anything — rebuilt from MongoDB at startup |

Access statistics are authoritative in MongoDB (updated with one bulk write per query); Qdrant payloads are authoritative for
text, vectors and module assignment. Startup reconciliation aligns the two (`reconcile.py`).

## Components

```
api/routes ─▶ services (thin routes, typed Pydantic schemas, uniform {"error":{code,message,request_id}})
IngestionService ─┐
MemoryManager ────┤
CompressionService┼─▶ MutationCoordinator (exclusive writer lock + operation journal + idempotency)
AdaptationService ┤        │
ArchitectureLifecycle (checkpoint/restore) ┘        ├─▶ ArchitecturePersistence.converge_to(target)  ◀── single apply/rollback primitive
RetrievalService ── shared read lock ──────────────┘          │  Qdrant payloads+profiles · MongoDB docs · live graph swap
```

* **MutationCoordinator** — a writer-preferring, writer-reentrant RW lock. Queries take the shared side, so they never observe
  a half-applied restructuring. Every mutation writes an `operations` document at start (`running`) and a terminal status at
  the end, and a re-submitted `operation_id` that already completed is not re-run. Compression and adaptation use `fail_fast`
  (HTTP 409 instead of queueing behind another job).
* **ArchitectureState** — a pure, serialisable description (modules + routing profile vectors, memory→module assignments, typed
  edges) with a structural checksum. Candidate architectures, snapshots, restores and sandbox evaluations all use this one structure.
* **converge_to(target)** — *declarative and idempotent*: it diffs each store against the target and fixes only what differs,
  in a safe order (add profiles → move payloads → write MongoDB → drop stale profiles → swap graph). Re-running after a partial
  failure converges. It is used to apply a change, to recover from a failure, and to restore a version.

## Retrieval path

1. embed the query once (same model/preprocessing as memories);
2. `search_modules` on the profile collection → `seed_module_count` seeds;
3. `expand_candidates`: seeds, then `co_access` neighbours by edge weight, then hierarchy children/siblings through the parent
   container — capped at `max_candidate_modules`;
4. Qdrant search filtered by `module_id ∈ candidates AND status = active`;
5. rank (score, then id for determinism); if guided results are too few or the best score < `FALLBACK_MIN_SCORE`, run a global search;
6. record the query event (ids, modules, latency, candidate count, fallback, architecture version) and bump access counts.

**Candidate count** is defined once and used everywhere (API trace, metrics, benchmark): *the number of active records inside the
searched modules; when global fallback also ran, the whole active corpus is added.*

The graph is functionally in the path: edges and module assignments change the module-id filter. Tests assert that adding an
edge changes the candidate set and that records outside the candidate set are invisible to the guided path.

## Isolation for candidate evaluation

`Evaluator.build_sandbox(state)` creates an **in-memory Qdrant store**, loads the *same* memory vectors with the candidate's
module assignments and profile vectors, and builds a graph from the candidate's edges. The production `RetrievalEngine` runs
unchanged over it. Production is never touched, and a parity test asserts that a sandbox of the live state returns exactly the
same rankings as live retrieval.

## Failure model

Cross-store writes are not transactional, so safety comes from ordering and idempotence: stage → verify → activate/retire;
the version pointer moves last; a checkpoint of the previous architecture is saved *before* a mutation starts; any failure
converges back to that checkpoint; if recovery itself fails the need is persisted (`needs_recovery`) and retried at startup.
See `EVALUATION_AND_ROLLBACK.md`.

## Frontend

React 19 + Vite + TypeScript (strict) + Tailwind 4 + shadcn/ui. TanStack Query is the only data layer: all numbers come from
the API, loading/error/empty states are explicit, unmeasured values render as "Not enough data yet", and every successful
mutation invalidates all queries. React Flow renders the graph with a touch-friendly module list as an alternative.
