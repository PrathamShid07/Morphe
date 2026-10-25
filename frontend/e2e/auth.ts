import { expect, request as pwRequest, type APIRequestContext } from "@playwright/test"

/** Real credentials come from the environment — they are never stored in the repository.
 *  Create them with `backend/scripts/admin_credentials.py init` (see SETUP.md) and export MORPHE_ADMIN_PASSWORD. */
export const API = "http://127.0.0.1:8000/api/v1"
export const ADMIN_USERNAME = process.env.MORPHE_ADMIN_USERNAME ?? "morphe-admin"
export const ADMIN_STATE = "e2e/.auth/admin.json"

export function adminPassword(): string {
  const p = process.env.MORPHE_ADMIN_PASSWORD
  if (!p) throw new Error("MORPHE_ADMIN_PASSWORD is not set: export the administrator password to run the admin e2e tests (see SETUP.md)")
  return p
}

/** An API client signed in as the administrator (cookie jar + CSRF header), for set-up and for asserting backend state. */
export async function adminApi(): Promise<APIRequestContext> {
  const anon = await pwRequest.newContext({ baseURL: API + "/" })
  const r = await anon.post("auth/login", { data: { username: ADMIN_USERNAME, password: adminPassword() } })
  expect(r.ok(), `admin login failed with HTTP ${r.status()}`).toBeTruthy()
  const { csrf_token } = await r.json()
  const state = await anon.storageState()
  await anon.dispose()
  return pwRequest.newContext({ baseURL: API + "/", storageState: state, extraHTTPHeaders: { "X-CSRF-Token": csrf_token } })
}

/** An API client with no administrator session (a visitor). */
export const visitorApi = () => pwRequest.newContext({ baseURL: API + "/" })
