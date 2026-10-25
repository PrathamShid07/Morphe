# User accounts and personal memory

MORPHE has three kinds of caller. The **server** decides which one a request is; the browser only mirrors that.

| | Visitor (no session) | User | Administrator |
|---|---|---|---|
| Sees | the sign-in / create-account / administrator-sign-in page only | **Query Playground** and **Graph Explorer** | the full dashboard + **Users** + **Activity** |
| Memory | none | their **own private** memory + published **shared** knowledge | the shared architecture (and, only via an audited support action, a user's memory content) |
| Can save memories | no | yes, explicitly ("Save to My Memory") | ingest into the shared knowledge base |
| Graph | no | their own modules and the published shared modules, read-only | the full architecture graph |
| API | `/auth/*`, `/health` (redacted) | `/auth/*`, `/me/*` | everything else |

## Architecture: why personal memory has its own plane

The shared memory is governed by whole-architecture machinery: versioning, restore/rollback, compression, structural adaptation, the integrity validator and the benchmarks each read or rewrite *every* record in the collections. If private data lived in the same collections, any of them could read, merge, summarise or roll back a user's memories.

So personal memory is a separate **plane** in the same Qdrant instance and MongoDB database:

| | Shared plane (existing) | Personal plane (new) |
|---|---|---|
| Qdrant | `morphe_memories`, `morphe_module_profiles` | `morphe_user_memories`, `morphe_user_module_profiles` |
| MongoDB | `sources`, `memory_metadata`, `modules`, `graph_edges` | `user_sources`, `user_memories`, `user_modules`, `user_edges` |
| Governed by | versions, rollback, compression, adaptation, benchmarks | nothing global: only the owner's own operations |
| Visibility field | `shared` (published) or `restricted` (administrators only) | `private`, always with `owner_user_id` |

An administrator restoring a version, running compression or resetting the demo **cannot touch** personal data. (`/admin/reset` clears the shared plane only.)

### Ownership model

* Every personal record carries `owner_user_id` (the stable `usr_…` id, never the email), `visibility: "private"`, `status`, `created_at`, `updated_at`, plus `module_id` and `source_id`. The same applies to sources, modules, edges and module-profile points.
* Every id minted in the personal plane embeds an owner token (`umem_<owner>_<hash>_<n>`, `umod_<owner>_<topic>`, `usrc_<owner>_<hash>`), so two users saving identical text, or using the same topic name, can never collide on an id or a Qdrant point.
* **The owner always comes from the verified session** (`require_user`). Request bodies forbid unknown fields (`owner_user_id`, `visibility`, `role` → 422); no route takes a user id in its path.
* MongoDB: every unique index includes the owner (`(owner_user_id, memory_id)`, `(owner_user_id, module_id)`, …) and there are `(owner, status, module)` and `(owner, created_at)` indexes for scoped reads.
* Qdrant: payload indexes are created for `owner_user_id`, `status`, `module_id`, `source_id`, `visibility` (they matter on a Qdrant server; local mode has none).

### The adapters make an unscoped query impossible

* `UserVectorStore` (Qdrant) – every method takes the owner as its first argument and **always** adds `owner_user_id == owner` to the filter. There is no method without it, and no `module_ids=None` shortcut: “all modules” means “all of this owner's modules”. Writing a point whose payload names another owner raises. Looking up another owner's ids returns nothing (same as “does not exist”).
* `UserDataStore` (MongoDB) – every read and write requires the owner and builds its query with it.
* `ScopedStore` / `build_scope` (`retrieval/scoped.py`) – the retrieval engine (`RetrievalEngine`) only ever uses `search_memories`, `search_modules`, `count_memories` and a graph. For each user query the service hands it a `ScopedStore` and the user's scope graph instead of the global ones, so **all four strategies — global dense, fixed graph, progressive expansion, soft routing — and their global fallbacks are scoped without any change to the algorithms**. “Search everything” (the fallback) is translated to “the owner's modules plus the published shared modules”.
* There is no administrator unrestricted path for user queries: `POST /query` (admin playground) uses the shared plane and never reads the personal plane; users cannot call it.
* Defence in depth: the user query service drops any hit whose payload owner is not the caller (or not explicitly shared) before it leaves the function.

### Private vs shared

| | Private (personal) | Shared |
|---|---|---|
| Created by | the user, with **Save to My Memory** (never automatic) | an administrator, with **Ingest content → Share with all users** (or `visibility: "shared"`), or by publishing an existing source |
| Visible to | its owner only | every signed-in user (labelled “Shared” in results and graph) |
| Default | **private** | an administrator's ingest is **restricted** (administrators only) unless they explicitly share it |
| Withdraw | the owner deletes it | `POST /sources/{id}/visibility {"visibility":"restricted"}` |

Fail-closed rules: a shared-plane record with **no** `visibility` label is not visible to users; a module is visible to users only if it holds at least one shared record; if a module also holds unpublished records its keywords and description are hidden from users; a compression summary is only as public as its least public source.

### Personal graph

Each user has their own modules, hierarchy (optional `group` when saving), co-access shortcuts (created after `USER_COACCESS_MIN_COUNT` of the user's own queries retrieved both modules), module-profile vectors and routing traces. A new account starts empty: it sees only the published shared modules, and Graph Explorer explains that the graph fills in as it saves information. Nothing is copied between accounts. The user's Graph Explorer is read-only: there is no route to split, merge or rewire.

Not implemented for personal graphs: automatic structural adaptation (split/merge) and per-user architecture version history. Those operate on the shared architecture only.

## Accounts and sessions

* **Register** `POST /auth/register {full_name, email, password, confirm_password}` always creates a regular `user`. The model forbids extra fields, so a client-sent `role` is a 422 and creates nothing. There is no way to create an administrator through the API; the administrator is configured out of band (see `ACCESS_CONTROL.md`).
* Emails are normalised (NFKC, trimmed, lower-cased) and uniqueness is enforced by a unique index, so concurrent sign-ups cannot create duplicates. Password policy: at least `PASSWORD_MIN_LENGTH` (10) characters, letters plus at least one digit or symbol, not an obvious password or the email/name; both the browser and the server check.
* Passwords are hashed with **Argon2id**; the hash never leaves the server. Login answers identically for “unknown email” and “wrong password”, does the same hashing work in both cases, and locks (429) after `LOGIN_MAX_ATTEMPTS` failures per address+email for `LOGIN_LOCKOUT_SECONDS`. Sign-ups are limited to `REGISTER_MAX_PER_HOUR` per address.
* A session is an HMAC-signed token **plus** a server-side record (`user_sessions`) in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` outside development). Logout, disabling an account and expiry (`USER_SESSION_TTL_DAYS`) all end it server-side; a replayed cookie is refused. State-changing requests also need the per-session CSRF token (`X-CSRF-Token`, kept in memory only by the SPA; nothing is stored in `localStorage`). Signing in as one role replaces the other role's cookie in that browser.
* **Disabled accounts** (administrator action) are refused at once: sessions are revoked, login returns 403 `account_disabled`, and the account is also re-checked on every request even if a session somehow survived.
* **Registration can be switched off** with `REGISTRATION_ENABLED=false`. Email verification and password-reset e-mail are not implemented (no mail provider is assumed); an administrator can disable an account, and a lost user password currently has no self-service reset.

## Audit trail

Every meaningful action is stored durably in MongoDB `audit_events` (indexes on actor/subject, action, status, target, time, with a TTL index when `AUDIT_RETENTION_DAYS > 0`; `0` keeps events forever).

```json
{"event_id": "evt_…", "actor_user_id": "usr_…", "actor_role": "user", "actor_label": "alice@example.com", "subject_user_id": "usr_…",
 "action": "query.completed", "target_type": "query", "target_id": "qry_…", "timestamp": "…Z", "status": "success",
 "request_id": "req_…", "metadata": {"strategy": "progressive", "result_count": 5, "latency_ms": 12.5, "query": "…first 300 chars"}}
```

`status` is `success`, `failure` or `denied`, written from what actually happened (an exception is recorded as `failure`; nothing is inferred from a button click).

| Actions | |
|---|---|
| auth | `auth.registered`, `auth.register` (failures), `auth.login` (success / failure / denied for disabled), `auth.logout`, `admin.login`, `admin.logout` |
| user | `query.submitted`, `query.completed`, `memory.created`, `memory.deleted`, `graph.viewed` |
| administrator | `user.disabled`, `user.reactivated`, `user.deleted`, `admin.user_memories_accessed`, `source.visibility_changed`, `version.restored`, `version.checkpoint`, `adaptation.applied/evaluated/run`, `compression.run`, `ingestion.created`, `benchmark.started`, `admin.demo_*`, and `admin.<verb>` for any other administrator write |

Never logged: passwords, hashes, tokens, CSRF values, the signing secret, embeddings, or the text of saved memories. Metadata is scrubbed (secret-looking keys dropped, strings capped at 300 chars, lists at 50 items). A question is kept as a truncated preview, as disclosed in the privacy notice. A failure to write an audit event never breaks the request it observes (it is logged loudly instead).

**Privacy notice** (shown on the sign-up form, in the account menu and under Query Playground): saved memories are private by default; questions search only the user's memory plus shared knowledge; administrators can see operational activity and the questions a user asks; shared knowledge is labelled; and MORPHE does **not** claim to encrypt memories at rest (it does not).

## What administrators can and cannot see

* **Users page** – name, email, user id, created, status, last sign-in, last activity, private memories, personal modules, sources, queries submitted/succeeded/failed, successful saves, save failures, failed operations, failed logins; search by name/email/id, filter by status and last-active date; a detail panel with recent activity and questions asked; disable/reactivate with confirmation and a recorded reason. Counters come from the durable audit log and the personal stores, not from memory.
* **Activity page** – the audit log with filters (user, action, outcome, actor, date range, free text) and a detail view of each event's metadata.
* Memory **content** is never on these pages. `GET /admin/users/{id}/memories?reason=…` opens a user's saved memories for support/moderation; it requires a stated reason (≥ 8 characters) and writes `admin.user_memories_accessed` to the audit log.
* `DELETE /admin/users/{id}?confirm=DELETE` permanently deletes an account and its personal data (audit trail kept; used by the e2e teardown). There is no UI button for it.

## Migration of pre-account data

Before accounts, nothing recorded who could see a memory. The rule is now **fail-closed**: a shared-plane record is visible to users only with `visibility == "shared"`.

`backend/scripts/migrate_accounts.py` (and the API at startup, `AUTO_MIGRATE=true`) labels every record that has no label:

* records from the repository's own sample corpus (`data/samples/manifest.json`) → `shared` (the explicitly shared demonstration knowledge; also their summaries, if all sources are samples);
* everything else (ad-hoc ingests, unknown provenance) → `restricted` (administrators only). It never silently becomes visible to every user; an administrator can publish it later.

```bash
.venv/bin/python backend/scripts/migrate_accounts.py plan      # read-only: counts of records that would be shared / restricted
.venv/bin/python backend/scripts/migrate_accounts.py apply     # idempotent; writes data/backups/accounts_v1_…json first (ids only)
.venv/bin/python backend/scripts/migrate_accounts.py status    # past runs
.venv/bin/python backend/scripts/migrate_accounts.py rollback data/backups/<file>.json   # remove exactly the labels that run added
```

Properties: **idempotent** (only unlabelled records are touched; a second run changes nothing), **resumable** (Qdrant is written before MongoDB, so an interrupted run is simply redone for the remaining records), **narrow** (no memory, module, version, edge or benchmark artifact is moved, rewritten or deleted; memory ids, source ids and the BEIR document-id mappings are unchanged; the personal plane is not touched), **reversible** (rollback removes only the labels listed in the backup). Stop the API first when it uses the local Qdrant folder (one process at a time). The `user_query_results` rows written before accounts (`session_id`, anonymous) are left in place but unreachable: no account owns them.

Records created after this change are labelled at creation (`ingestion.ingest(..., visibility=…)`), so the migration is a one-time upgrade step.

## Configuration

| Setting (backend/.env) | Default | |
|---|---|---|
| `REGISTRATION_ENABLED` | `true` | allow public sign-up (always creates a regular user) |
| `PASSWORD_MIN_LENGTH` | `10` (min 8) | |
| `REGISTER_MAX_PER_HOUR` | `20` | sign-ups per client address |
| `USER_SESSION_TTL_DAYS` | `30` | user session lifetime |
| `USER_MEMORY_MAX_CHARS` / `USER_MEMORY_MAX_RECORDS` | `20000` / `5000` | per save / per user guard |
| `USER_TOP_K_MAX` / `USER_HISTORY_LIMIT` | `20` / `20` | per query / stored results per user |
| `USER_COACCESS_MIN_COUNT` | `3` | queries before a personal co-access shortcut appears |
| `QDRANT_USER_MEMORY_COLLECTION` / `QDRANT_USER_MODULE_COLLECTION` | `morphe_user_memories` / `morphe_user_module_profiles` | |
| `AUDIT_RETENTION_DAYS` / `AUDIT_QUERY_TEXT_CHARS` | `365` / `300` | |
| `AUTO_MIGRATE` / `MIGRATION_BACKUP_DIR` | `true` / `data/backups` | |

## Testing

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_accounts.py tests/test_isolation.py tests/test_admin_users.py tests/test_migration.py tests/test_authorization.py tests/test_auth.py
cd frontend && npm test
cd frontend && MORPHE_ADMIN_PASSWORD='…' npx playwright test roles.spec.ts      # the real stack: two users, isolation, admin pages
```

`test_isolation.py` drives the real retrieval engine, Qdrant payload filters, MongoDB ownership queries and routes with two users: own retrieval, cross-user retrieval under every strategy, forced fallbacks (graph, progressive budget, soft), graph isolation, id guessing (memory/module/query), owner/role spoofing in bodies/headers/params, shared-vs-private policy, empty accounts, failed saves leaving nothing active, and rejected requests changing no state.

## Limitations

* No email verification and no self-service password reset (no mail provider is assumed).
* Personal graphs have no automatic structural adaptation or per-user version history.
* A single administrator account configured through `.env`; administrators have no personal memory space.
* Login throttling is in memory per API process (a single worker is already required for the local Qdrant folder).
* Shared-plane modules that mix published and unpublished records are visible with their published record count only; compression can fold published and unpublished records into a summary that is then restricted.
* Personal memories are not encrypted at rest by MORPHE.
