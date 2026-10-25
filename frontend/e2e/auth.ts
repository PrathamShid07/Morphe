import { expect, request as pwRequest, type APIRequestContext } from "@playwright/test"
import { readFileSync } from "node:fs"

/** Real credentials come from the environment — they are never stored in the repository.
 *  Create them with `backend/scripts/admin_credentials.py init` (see SETUP.md) and export MORPHE_ADMIN_PASSWORD. */
export const API = "http://127.0.0.1:8000/api/v1"
export const WEB = "http://127.0.0.1:5173"
export const ADMIN_USERNAME = process.env.MORPHE_ADMIN_USERNAME ?? "morphe-admin"
export const ADMIN_STATE = "e2e/.auth/admin.json"
export const USER_A_STATE = "e2e/.auth/user-a.json"
export const USER_B_STATE = "e2e/.auth/user-b.json"
export const USERS_FILE = "e2e/.auth/users.json"
export const NO_SESSION = { cookies: [], origins: [] }
/** Throw-away password for the e2e accounts; the accounts are deleted again by the global teardown. */
export const E2E_PASSWORD = "E2e-Throwaway-Pass-42"

export interface E2eUser { id: string; email: string; name: string }
export const e2eUsers = (): { a: E2eUser; b: E2eUser } => JSON.parse(readFileSync(USERS_FILE, "utf-8"))

export function adminPassword(): string {
  const p = process.env.MORPHE_ADMIN_PASSWORD
  if (!p) throw new Error("MORPHE_ADMIN_PASSWORD is not set: export the administrator password to run the e2e tests (see SETUP.md)")
  return p
}

/** An API client signed in as the administrator (cookie jar + CSRF header), for set-up and for asserting backend state. */
export async function adminApi(): Promise<APIRequestContext> {
  const anon = await pwRequest.newContext({ baseURL: API + "/" })
  const r = await anon.post("auth/admin/login", { data: { username: ADMIN_USERNAME, password: adminPassword() } })
  expect(r.ok(), `admin login failed with HTTP ${r.status()}`).toBeTruthy()
  const { csrf_token } = await r.json()
  const state = await anon.storageState()
  await anon.dispose()
  return pwRequest.newContext({ baseURL: API + "/", storageState: state, extraHTTPHeaders: { "X-CSRF-Token": csrf_token } })
}

/** An API client with no session at all. */
export const visitorApi = () => pwRequest.newContext({ baseURL: API + "/" })

/** An API client signed in as an ordinary user created for this call (CSRF header set). Returns the context and the account. */
export async function userApi(email: string, password = E2E_PASSWORD): Promise<APIRequestContext> {
  const anon = await pwRequest.newContext({ baseURL: API + "/" })
  const r = await anon.post("auth/login", { data: { email, password } })
  expect(r.ok(), `user login failed with HTTP ${r.status()}`).toBeTruthy()
  const { csrf_token } = await r.json()
  const state = await anon.storageState()
  await anon.dispose()
  return pwRequest.newContext({ baseURL: API + "/", storageState: state, extraHTTPHeaders: { "X-CSRF-Token": csrf_token } })
}

export const uniqueEmail = (tag: string) => `e2e-${tag}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.test`
