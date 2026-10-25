import { mkdirSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import { request } from "@playwright/test"
import { ADMIN_STATE, ADMIN_USERNAME, adminPassword } from "./auth"

/** Signs in once through the dev server's proxy (so the cookie belongs to the browser origin) and saves the cookie for admin specs. */
export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: "http://127.0.0.1:5173" })
  const r = await ctx.post("/api/v1/auth/login", { data: { username: ADMIN_USERNAME, password: adminPassword() } })
  if (!r.ok()) throw new Error(`admin sign-in for e2e failed with HTTP ${r.status()}: ${await r.text()}`)
  mkdirSync(dirname(ADMIN_STATE), { recursive: true })
  writeFileSync(ADMIN_STATE, JSON.stringify(await ctx.storageState()))
  await ctx.dispose()
}
