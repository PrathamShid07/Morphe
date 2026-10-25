# Roles, sign-in and access control

MORPHE has a visitor, an ordinary **user** and an **administrator**. The **server** decides which one a request is; the browser UI only mirrors that decision. User accounts, personal memory and the audit trail are described in [`USER_ACCOUNTS.md`](USER_ACCOUNTS.md); this page is the access-control reference.

| | Visitor | User | Administrator |
|---|---|---|---|
| Sign-in page (sign in / create account / administrator) | yes | – | – |
| Query Playground, Graph Explorer | no | yes: own private memory + published shared knowledge, read-only graph, top-k ≤ 20 | the full playground and graph |
| Save / delete own memories | no | yes (`/me/memories`) | – (no personal memory) |
| Own query history | no | yes (`/me/queries`) | – |
| Ingest, edit or delete shared memories; publish a source | no | no | yes |
| Overview, Memories, Compression, Adaptation, Metrics, Versions (restore / rollback), Benchmarks | no | no | yes |
| **Users** and **Activity** pages | no | no | yes |
| Demo reset / seed / scenarios | no | no | yes (development only) |
| `GET /health` | `{status, ready}` | `{status, ready}` | full dependency detail |

## How it works

* **Administrator** – `POST /auth/admin/login` checks the configured username and an **Argon2id** hash. The server stores a revocable session (`admin_sessions`) and sets the `morphe_admin` cookie: *HttpOnly*, *SameSite=Strict*, *Secure* whenever `APP_ENV` is not `development`. The cookie value is an HMAC-signed token bound to the "admin" kind.
* **User** – `POST /auth/register` / `POST /auth/login` (email + password, per-account Argon2id hash in MongoDB `users`). A revocable server-side session (`user_sessions`) and the `morphe_user` cookie (*HttpOnly*, *SameSite=Lax*, *Secure* outside development).
* **Never trusted from the client**: a role, a `user_id`, or an owner in a body, header, cookie or query string is ignored or rejected (`extra="forbid"` models → 422). The only source of identity is a signed token whose server-side session record is live.
* **CSRF** – state-changing requests authenticated by either cookie must send `X-CSRF-Token` (returned by sign-in and `GET /auth/me`, kept in memory only); cross-origin state-changing requests are also refused by an Origin check. Missing/incorrect → `403 csrf_failed`.
* **Status codes** – `401` = no, expired, revoked or invalid credentials (`authentication_required`, `session_expired`, `invalid_credentials`); `403` = authenticated but not allowed (`admin_required`, `user_account_required` for an administrator calling `/me/*`, `account_disabled`, `csrf_failed`); `409 email_taken`; `422` validation; `429 too_many_attempts` after repeated failures. Rejected requests never mutate state or start jobs: the dependency runs before the handler. Objects outside the caller's scope answer exactly like absent ones (`404`).
* **One identity per browser** – signing in as one role clears the other role's cookie.

### Protected endpoints

All routers except those below carry a router-level `Depends(require_admin)` (ingestion, memories, sources, modules, graph, compression, adaptation, metrics, evaluation, versions including restore, benchmarks, `/admin/*`, `/query`, `/integrity`, `/config`). The test `test_authorization.py` reads the OpenAPI document and fails if any route is neither public, user-scoped nor administrator-only.

| Endpoint | Who |
|---|---|
| `POST /auth/register`, `POST /auth/login`, `POST /auth/admin/login`, `GET /auth/me`, `POST /auth/logout` | anyone (logout needs the CSRF token when a session exists) |
| `GET /health` | anyone (detail: administrator) |
| `POST/GET /me/memories`, `DELETE /me/memories/{id}`, `POST /me/query`, `GET /me/queries[/{id}]`, `GET /me/graph`, `GET /me/modules[/{id}]`, `GET /me/summary` | a signed-in user (the owner is the session's user) |
| everything else, including `/admin/users…`, `/admin/activity…` | an administrator |

## Creating the administrator credentials

Nothing is checked in and there is no default account. Create them once on the machine that runs the API:

```bash
.venv/bin/python backend/scripts/admin_credentials.py init            # username morphe-admin (or --username NAME)
```

It generates a strong random password, writes **only** `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` (Argon2id) and a separate random `SESSION_SECRET` to the git-ignored `backend/.env` (mode 600), and prints the password **once**. Restart the API (one worker).

```bash
.venv/bin/python backend/scripts/admin_credentials.py check           # configured? (never prints secrets)
.venv/bin/python backend/scripts/admin_credentials.py rotate          # new password, same account
.venv/bin/python backend/scripts/admin_credentials.py rotate --revoke-sessions   # ...and sign every administrator out
.venv/bin/python backend/scripts/admin_credentials.py secret          # new SESSION_SECRET: every session (user and administrator) ends on next API start
```

`init` refuses to overwrite an existing hash. **Forgot the password?** `rotate --revoke-sessions` and restart; no data is touched.

| Setting (backend/.env) | Default | Meaning |
|---|---|---|
| `ADMIN_USERNAME` | `morphe-admin` | administrator login name |
| `ADMIN_PASSWORD_HASH` | empty (disabled) | Argon2id hash written by the script (single-quoted: it contains `$`) |
| `SESSION_SECRET` | empty (ephemeral key, development only) | ≥ 32 characters; **required** in production when a hash is set. It signs both administrator and user sessions |
| `ADMIN_SESSION_TTL_MINUTES` / `USER_SESSION_TTL_DAYS` | 480 / 30 | session lifetimes |
| `SESSION_COOKIE_SECURE` | auto (true outside development) | force the Secure cookie attribute |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS` | 5 / 300 | brute-force throttle (administrator and user sign-in) |

## Using it

1. Open the dashboard: the sign-in page offers **Sign in**, **Create account** and **Administrator**.
2. A user lands on the Query Playground (and can open Graph Explorer); an administrator lands on the dashboard. **Log out** / **Sign out** revokes the session on the server.
3. When a session expires or an account is disabled, the next request fails with 401/403 and the app returns to the sign-in page.

Scripts that call administrator endpoints (`backend/scripts/demo.py`, Playwright e2e) read the password from `MORPHE_ADMIN_PASSWORD` (and `MORPHE_ADMIN_USERNAME`); it is never written to disk.

## Testing authorization

```bash
cd backend  && ../.venv/bin/python -m pytest tests/test_auth.py tests/test_authorization.py tests/test_accounts.py tests/test_isolation.py
cd frontend && npm test
cd frontend && MORPHE_ADMIN_PASSWORD=... npx playwright test roles.spec.ts
curl -i http://127.0.0.1:8000/api/v1/memories                         # 401 without a session
```

## Limitations

* One administrator account, configured out of band; administrators have no personal memory. No self-service password reset or email verification.
* Login throttling is in memory per API process (a single worker is already required for the local Qdrant folder).
* Behind HTTPS set `APP_ENV=production` (Secure cookies, explicit CORS origins, `SESSION_SECRET` required) and serve the SPA and API from the same site.
