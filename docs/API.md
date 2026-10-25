# REST API

Base path `/api/v1` (configurable with `API_PREFIX`); interactive OpenAPI at `/docs`, schema at `/openapi.json`; readiness at `GET /health`.
All bodies are JSON and validated by Pydantic. A single body limit applies (`MAX_REQUEST_BYTES`, default 2 MB → `413 request_too_large`).

**Errors** always have one shape and never contain stack traces:
```json
{"error": {"code": "memory_not_found", "message": "memory mem_x not found", "request_id": "req_ab12…", "details": null}}
```
`X-Request-ID` is accepted/returned on every response and appears in the server's structured logs.

| Status | Meaning | Example codes |
|---|---|---|
| 200 | read / write completed (long operations are synchronous; nothing is "pending" behind a 200) | |
| 404 | unknown id | `memory_not_found`, `module_not_found`, `version_not_found`, `event_not_found` |
| 409 | conflicting state | `mutation_in_progress`, `protected_memory`, `stale_plan`, `adaptation_running` |
| 413 | too large | `text_too_large`, `request_too_large` |
| 422 | invalid input | `invalid_request` (field list in `details`), `empty_text`, `empty_query`, `unsupported_file`, `override_disabled` |
| 500 | integrity/restore failure (state is recovered and reported truthfully) | `snapshot_invalid`, `restore_failed`, `integrity_error`, `internal_error` |
| 503 | dependency not ready | `mongodb_unavailable`, `qdrant_unavailable`, `embedding_model_unavailable` |

## Core

| Method & path | Purpose |
|---|---|
| `GET /health` | backend, MongoDB, Qdrant, embedding model readiness; active architecture version; startup reconciliation summary; `needs_recovery` |
| `POST /ingestion` | `{source_name, text, metadata?{topic,parent_topic,module_name}, tags?, importance?, pinned?, operation_id?}` → `{source_id, status: completed\|duplicate, chunks_created, module_ids, new_module_ids, tokens_added, active_tokens, usage_percent, compression?}`; identical content returns `duplicate`; `operation_id` is an idempotency key |
| `POST /ingestion/file` | multipart UTF-8 text file (`file`, optional `topic`, `parent_topic`) |
| `POST /query` | `{query, top_k (1–50, default 5), debug_trace?, mode: graph\|global\|progressive\|soft}` → `{query_id, architecture_version, results[{memory_id,text,score,module_id,module_name,source_name,section}], latency_ms, trace?}`; `trace` = `{strategy, seed_modules, candidate_modules[{origin: seed\|co_access\|sibling\|hierarchy, via}], searched_modules[{module_id, stage, origin}], result_modules, candidate_count, guided_candidate_count, fallback_used, fallback_reason, embedding_ms, timings_ms{seed,expand,search,rank,fallback}, …}`; `progressive` adds `stages[]` (modules, candidates, top-k change, decision per stage) and `progressive{stop_reason,…}`; `soft` adds `soft{weight, graph_share, formula, results[{dense_score, dense_rank, prior, score_shift, final_rank, change}]}`. See `BEIR_RETRIEVAL_ABLATIONS.md` for what each mode does. |
| `GET /memories` | `module_id, source_id, status=active\|superseded\|deleted\|all, pinned, search, limit≤100, offset` → page of metadata (no embeddings, text as `text_preview`) |
| `GET /memories/{id}` | full text, lifecycle, `summary_of` with source previews, `superseded_by` |
| `PATCH /memories/{id}` | `{pinned?, importance?}` |
| `DELETE /memories/{id}?force=` | hard-deletes the vector; pinned/critical need `force=true` (else 409) |
| `GET /sources` · `GET /modules` · `GET /modules/{id}` | sources; module list with counts/usage; module detail (parent, neighbours, memories, adaptation history) |
| `GET /graph` | `{nodes, edges}` (+ server layout, `architecture_version`, counts, `capacity_threshold`); nodes carry `recordCount`, `isContainer`, `utilization` (records ÷ split capacity threshold, `null` for groups); edges carry `relationType` (`hierarchy`\|`co_access`), `weight`, `coAccessCount`, `evidence`, `operationId`. `GET /modules` and `/modules/{id}` add `capacity_threshold` and `utilization` (the detail also the `architecture_version`) |

## Compression
`GET /compression/status` (budget, usage %, thresholds, state `idle\|queued\|running\|completed\|failed`, band, last run) ·
`GET /compression/events?scope=job\|operation&status=` ·
`POST /compression/run {mode: auto\|normal\|emergency, dry_run}` (synchronous; `dry_run` records a `simulated` event and changes nothing; 409 if another mutation runs) ·
`PUT /compression/budget {budget_tokens\|null}` (dev/demo only → 403 in production).

## Structural adaptation
`GET /adaptation/status` (window counters, thresholds, last window) ·
`GET /adaptation/candidates` (explainable proposals + `not_proposed` with blockers) ·
`GET /adaptation/events?status=proposed|completed|rejected|failed|rolled_back&operation=` (+ `status_counts`) · `GET /adaptation/events/{id}` ·
`POST /adaptation/evaluate {operation, module_ids}` — plan + evaluate, **no commit** (status `proposed` or `rejected`) ·
`POST /adaptation/apply {operation, module_ids, validation_override?, operation_id?}` — evaluate and commit only if accepted ·
`POST /adaptation/events/{id}/apply` — re-plan/re-evaluate a proposed event and commit if accepted ·
`POST /adaptation/run-window` (what the 50th query triggers; 409 if one is running) · `POST /adaptation/monitor` (regression check with auto-rollback).
`operation` ∈ `split_module` (1 module id), `merge_modules` (2), `add_shortcut_edge` (2).
Event fields: `status` (`running|proposed|completed|rejected|failed|rolled_back`), `reason`, `evidence`, `thresholds`, `evaluation{baseline,candidate,decision{checks[]}}`, `rejection_stage` (`planning|integrity|pre_commit`), `integrity_check`, `post_commit_verification`, `before_version`, `new_version`, `rolled_back_to`, `recovered`, `timeline[]`, `duration_ms`, `error`.

## Metrics & evaluation
`GET /metrics/summary` (counts, tokens, p50/p95 latency **or null + reason**, candidate count, fallback rate, RSS, Qdrant disk, latest live quality) ·
`GET /metrics/history?window=1h|24h|7d` · `GET /evaluation/dataset` · `POST /evaluation/dataset/load` ·
`POST /evaluation/run {mode: graph\|global\|progressive\|soft, version_id?, splits?}` · `GET /evaluation/runs[/{id}]` ·
`POST /evaluation/compare-modes` (global vs fixed graph vs adaptive graph) · `POST /evaluation/compare-strategies {query | query_id, top_k, modes?}` (one query through global dense, fixed graph, progressive expansion and soft routing on the live architecture; nothing is tracked; a labeled `query_id` adds quality metrics and the relevant memories each strategy's routing excluded) · `POST /evaluation/compare-versions {from_version,to_version,evaluate}`.

## Versions
`GET /versions` · `GET /versions/active` · `GET /versions/{id}` (modules, edges, evaluation, creating event) · `GET /versions/{id}/evaluation` ·
`GET /versions/compare?from_version=&to_version=&evaluate=` · `GET /versions/{id}/restore-preview` (what would change, what is replaced) ·
`POST /versions/{id}/restore {operation_id?, reason?, evaluate?}` · `POST /versions/checkpoint`.
`restorable` is computed by verifying the snapshot checksum on every read.

## Operations & demo
`GET /integrity` (cross-store check) · `GET /config` (non-secret settings for the UI) · `GET /admin/demo/scenarios` · `POST /admin/demo/seed` ·
`POST /admin/demo/scenario/{merge|mixed|shortcut|compression|emergency}` · `POST /admin/reset {confirm:"RESET"}`.
Admin routes return 403 when `APP_ENV=production` or `ALLOW_ADMIN_ENDPOINTS=false`.


### Retrieval-strategy benchmark artifacts (read-only)

`GET /benchmarks/strategies/runs` · `GET /benchmarks/strategies/runs/{id}` (manifest, frozen configuration, validation-only selection evidence, optional `server_latency`) ·
`GET /benchmarks/strategies/runs/{id}/{dataset}/{validation|test}` (metrics, latency, paired comparisons, routing diagnostics) ·
`GET …/{validation|test}/rows?sort=progressive_vs_global_dense&order=worst|best&only=all|degraded|improved|excluded&search=&limit=&offset=` (per-query inspector) ·
`GET /benchmarks/strategies/runs/{id}/export?format=json|csv`. They serve what `python -m app.evaluation.run_strategies` saved under `backend/artifacts/beir_strategies/<run_id>/`; nothing is recomputed.
