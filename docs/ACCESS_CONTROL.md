# Roles, administrator sign-in and access control

MORPHE has two experiences, and the **server** decides which one a request gets. The browser UI only mirrors that decision.

| | **User Mode** (default, no sign-in) | **Admin Mode** (after a real sign-in) |
|---|---|---|
| Navigation | Query Playground, Graph Explorer | Overview, Ingest, Query Playground, Memories, Graph Explorer, Compression, Adaptation, Metrics, Versions, Benchmarks |
| Ask questions | yes: real retrieval engine, top-k ≤ 20, sanitised results (no access counts, source ids, internal version ids) | yes: full results, debug trace, all four strategies, comparison |
| Own query history | yes: only this browser session's questions | full playground |
| Graph Explorer | read-only, sanitised (module names, hierarchy, co-access links, route of your own recent queries) | full (capacity, memories, adaptation history, ingest) |
| Ingest / edit / delete memories | no | yes |
| Compression, adaptation, versions, restore, rollback | no | yes |
| Metrics, benchmarks, evaluation, config, integrity | no | yes |
| Demo reset / seed / scenarios | no | yes (development only) |
| `GET /health` | `{status, ready}` only | full dependency detail |

## How it works

* **Administrator session**: `POST /api/v1/auth/login` checks the username and an **Argon2id** password hash. On success the server creates a revocable session record (MongoDB `admin_sessions`) and sets the `morphe_admin` cookie: *HttpOnly*, *SameSite=Strict*, *Secure* whenever `APP_ENV` is not `development`. The cookie value is an HMAC-signed token bound to the "admin" kind; a role claim sent by the client is never read.
* **Visitor session**: the first request without a valid `morphe_session` cookie gets a server-issued, signed, anonymous visitor cookie. A visitor id supplied by the client is ignored. Query events and stored results carry the originating visitor id, and `GET /me/queries[/{id}]` only returns rows that belong to the calling session (anything else is a 404).
* **CSRF**: state-changing requests authenticated by the admin cookie must send `X-CSRF-Token` (delivered by login and `GET /auth/session`, kept only in memory by the SPA), and cross-origin state-changing requests are refused by an Origin check. Missing/incorrect token → `403 csrf_failed`.
* **Status codes**: `401` = no, expired, revoked or invalid credentials (`authentication_required`, `session_expired`, `invalid_credentials`); `403` = authenticated visitor lacking privilege (`admin_required`); `429 too_many_attempts` after `LOGIN_MAX_ATTEMPTS` failures within `LOGIN_LOCKOUT_SECONDS`. Rejected requests never mutate state or start jobs: the dependency runs before the handler.
* **No registration endpoint exists. No default account exists.** Sign-in stays disabled (`503 admin_not_configured`) until a hash is configured.

### Protected endpoints

All routers except the ones below carry a router-level `Depends(require_admin)`: ingestion, memories, sources, modules, graph, compression, adaptation, metrics, evaluation, versions (including restore), benchmarks, `/admin/*`, `/integrity`, `/config`. Available without administrator rights:

| Endpoint | Behaviour |
|---|---|
| `POST /auth/login`, `GET /auth/session`, `POST /auth/logout` | sign in / current session / sign out |
| `POST /query` | role-aware: visitors get a sanitised response and `top_k ≤ USER_TOP_K_MAX` |
| `GET /me/queries`, `GET /me/queries/{id}` | the caller's own history |
| `GET /explore/graph`, `/explore/modules`, `/explore/modules/{id}` | sanitised read-only graph |
| `GET /health` | redacted unless an administrator session is present |

## Creating the administrator credentials

Nothing is checked in. Create them once on the machine that runs the API:

```bash
.venv/bin/python backend/scripts/admin_credentials.py init            # username morphe-admin (or --username NAME)
```

It generates a strong random password, writes **only** `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` (Argon2id) and a separate random `SESSION_SECRET` to the git-ignored `backend/.env` (file mode 600), and prints the password **once**. It is not stored anywhere; store it in a password manager. Then restart the API (one worker).

```bash
.venv/bin/python backend/scripts/admin_credentials.py check           # configured? (never prints secrets)
.venv/bin/python backend/scripts/admin_credentials.py rotate          # new password, same account
.venv/bin/python backend/scripts/admin_credentials.py rotate --revoke-sessions   # ...and sign every administrator out
.venv/bin/python backend/scripts/admin_credentials.py secret          # new SESSION_SECRET: every session ends on next API start
```

`init` refuses to overwrite an existing hash, so credentials are never rotated silently. **Forgot the password?** On the machine that holds `backend/.env` run `rotate --revoke-sessions` and restart; nothing else (data, versions, benchmarks) is touched. You may supply your own password with `--password-from-env VAR`.

| Setting (backend/.env) | Default | Meaning |
|---|---|---|
| `ADMIN_USERNAME` | `morphe-admin` | administrator login name |
| `ADMIN_PASSWORD_HASH` | empty (disabled) | Argon2id hash written by the script (single-quoted because it contains `$`) |
| `SESSION_SECRET` | empty (ephemeral key, development only) | ≥ 32 characters; **required** in production when a hash is set |
| `ADMIN_SESSION_TTL_MINUTES` / `USER_SESSION_TTL_DAYS` | 480 / 30 | session lifetimes |
| `SESSION_COOKIE_SECURE` | auto (true outside development) | force the Secure cookie attribute |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS` | 5 / 300 | brute-force throttle (per client address and username) |
| `USER_TOP_K_MAX` / `USER_HISTORY_LIMIT` | 20 / 20 | visitor limits |

## Using it

1. Open the dashboard. You are in **User Mode** (Query Playground, Graph Explorer, an **Admin Login** button).
2. **Admin Login** → username and password → the full dashboard opens with an **Admin Mode** indicator.
3. **Switch to User Mode** only changes the view (the administrator session is kept; a banner offers *Return to Admin Mode* without signing in again). **Sign out** revokes the session on the server and returns to User Mode.
4. When a session expires the next request fails with 401 and the app falls back to User Mode.

Scripts that call admin endpoints (`backend/scripts/demo.py`, Playwright e2e) read the password from `MORPHE_ADMIN_PASSWORD` (and `MORPHE_ADMIN_USERNAME`); it is never written to disk.

## Testing authorization

```bash
cd backend  && ../.venv/bin/python -m pytest tests/test_auth.py tests/test_authorization.py   # login/session/CSRF, every route's 401/403, spoofing, cross-session isolation
cd frontend && npm test                                                                       # mode switching, login dialog, restricted views
cd frontend && MORPHE_ADMIN_PASSWORD=... npx playwright test roles.spec.ts                    # against the running stack
curl -i http://127.0.0.1:8000/api/v1/memories                                                 # 401 without a session
```

## Limitations

* Single administrator account, no user management, password reset is a local script by design.
* Login throttling is in memory per API process (single worker is already a deployment requirement).
* Behind HTTPS set `APP_ENV=production` (Secure cookies, explicit CORS origins, `SESSION_SECRET` required) and serve the SPA and API from the same site.
* Visitor sessions are anonymous and per browser; clearing cookies discards that history.
