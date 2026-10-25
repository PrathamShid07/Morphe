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

## Authentication and roles

Details in [`ACCESS_CONTROL.md`](ACCESS_CONTROL.md) and [`USER_ACCOUNTS.md`](USER_ACCOUNTS.md). Sessions are `HttpOnly` cookies; state-changing requests also send `X-CSRF-Token` (returned by sign-in and `GET /auth/me`). `401` = no/expired/revoked/invalid session; `403` = authenticated but not allowed (`admin_required`, `user_account_required`, `account_disabled`, `csrf_failed`); objects outside the caller's scope are `404`.

| Method & path | Who | Request → response |
|---|---|---|
| `POST /auth/register` | anyone | `{full_name, email, password, confirm_password}` (unknown fields such as `role` → 422) → `201` session body; always a regular `user`. `409 email_taken`, `422 invalid_email / invalid_name / weak_password / password_mismatch`, `403 registration_disabled`, `429` |
| `POST /auth/login` | anyone | `{email, password}` → session body + `morphe_user` cookie. `401 invalid_credentials` (identical for unknown email and wrong password), `403 account_disabled`, `429 too_many_attempts` |
| `POST /auth/admin/login` | anyone | `{username, password}` → session body + `morphe_admin` cookie. `401`, `429`, `503 admin_not_configured` |
| `GET /auth/me` | anyone | `{authenticated, role: "admin"\|"user"\|null, user?, username?, expires_at, csrf_token, admin_configured, registration_enabled, account_disabled?}` |
| `POST /auth/logout` | session holder | revokes the session server-side, clears cookies; idempotent |
| `GET /health` | anyone (detail: admin) | non-administrators receive `{status, ready}` only |

The `user` object is `{user_id, full_name, email, role:"user", status, created_at, last_login_at, last_activity_at}` (never a hash).

## Personal memory (`/me/*`, signed-in user only)

The owner is always the session's user. Bodies forbid unknown fields, so an `owner_user_id`, `visibility` or `role` is a 422.

| Method & path | Purpose |
|---|---|
| `POST /me/memories` | `{text, title?, topic?, group?, tags?}` → `201 {source_id, status: completed\|duplicate, chunks_created, memory_ids, module_ids, new_module_ids, tokens_added, message}`. Stored as **private**; on failure nothing stays active (`500 save_failed`), `413/422` for size/empty, `409 memory_quota_exceeded` |
| `GET /me/memories?module_id&search&limit&offset` | the caller's own memories (metadata + `text_preview`) |
| `DELETE /me/memories/{id}` | delete one of the caller's memories (404 if absent **or not theirs**) |
| `POST /me/query` | `{query, top_k (≤ 20), mode: graph\|global\|progressive\|soft, debug_trace}` → `{query_id, results[{memory_id,text,score,module_id,module_name,source_name,section,scope:"personal"\|"shared"}], latency_ms, mode, fallback_used, memory_scope{personal_records,shared_records,empty}, trace?}`. Searches only the caller's memories + published shared knowledge, in every strategy and fallback |
| `GET /me/queries`, `GET /me/queries/{id}` | own history (another user's id → 404) |
| `GET /me/graph` | the caller's graph + published shared modules; nodes carry `scope`; read-only, no operational fields |
| `GET /me/modules`, `GET /me/modules/{id}` | module list / detail within the same scope (no memory text) |
| `GET /me/summary` | `{memories, modules, sources, tokens, shared_records}` |

## Administrator: users and activity

| Method & path | Purpose |
|---|---|
| `GET /admin/users?search&status&active_after&active_before&limit&offset` | accounts with `stats{private_memories, personal_modules, sources, queries_submitted/succeeded/failed, memory_saves, memory_save_failures, memory_deletions, failed_operations, failed_logins}` |
| `GET /admin/users/{id}` | one account + 10 most recent activity events |
| `GET /admin/users/{id}/activity?action&status&since&until` | the account's audit events |
| `GET /admin/users/{id}/queries`, `…/queries/{query_id}` | questions asked + result **metadata** (never the retrieved text) |
| `POST /admin/users/{id}/status` | `{status: active\|disabled, reason?}`; disabling revokes sessions at once (`409` if unchanged) |
| `GET /admin/users/{id}/memories?reason=` | support access to memory **content**; `reason` ≥ 8 chars; audited as `admin.user_memories_accessed` |
| `DELETE /admin/users/{id}?confirm=DELETE` | permanently delete the account and its personal data (audit trail kept) |
| `GET /admin/activity?user_id&action&status&target_type&role&since&until&search&limit&offset` | the audit log, newest first (`action` may be a family such as `query`) |
| `GET /admin/activity/actions`, `GET /admin/activity/{event_id}` | distinct actions; one event |
| `POST /sources/{source_id}/visibility` | `{visibility: shared\|restricted}` publish / withdraw shared-plane content |

Audit event: `{event_id, actor_user_id, actor_role, actor_label, subject_user_id, action, target_type, target_id, timestamp, status: success\|failure\|denied, request_id, metadata}`.

## Core

Every route in this section and all later sections is **administrator-only**. `POST /query` is the administrator playground on the shared architecture; ordinary users use `POST /me/query`. `POST /ingestion` accepts `visibility: "shared"\|"restricted"` (default `restricted` = administrators only).

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
