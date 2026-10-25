import { mkdirSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import { request } from "@playwright/test"
import { ADMIN_STATE, ADMIN_USERNAME, E2E_PASSWORD, USERS_FILE, USER_A_STATE, USER_B_STATE, WEB, adminPassword, uniqueEmail } from "./auth"

/** Signs in once through the dev server's proxy (so the cookies belong to the browser origin) and saves them for the specs:
 *  the administrator, and two ordinary users with independent personal memories. The users are removed again by global-teardown. */
export default async function globalSetup() {
  mkdirSync(dirname(ADMIN_STATE), { recursive: true })
  const admin = await request.newContext({ baseURL: WEB })
  const r = await admin.post("/api/v1/auth/admin/login", { data: { username: ADMIN_USERNAME, password: adminPassword() } })
  if (!r.ok()) throw new Error(`admin sign-in for e2e failed with HTTP ${r.status()}: ${await r.text()}`)
  writeFileSync(ADMIN_STATE, JSON.stringify(await admin.storageState()))
  await admin.dispose()

  const out: Record<string, { id: string; email: string; name: string }> = {}
  for (const [key, label, file] of [["a", "Alice E2E", USER_A_STATE], ["b", "Bob E2E", USER_B_STATE]] as const) {
    const ctx = await request.newContext({ baseURL: WEB })
    const email = uniqueEmail(key)
    const reg = await ctx.post("/api/v1/auth/register", { data: { full_name: label, email, password: E2E_PASSWORD, confirm_password: E2E_PASSWORD } })
    if (!reg.ok()) throw new Error(`e2e user registration failed with HTTP ${reg.status()}: ${await reg.text()}`)
    out[key] = { id: (await reg.json()).user.user_id, email, name: label }
    writeFileSync(file, JSON.stringify(await ctx.storageState()))
    await ctx.dispose()
  }
  writeFileSync(USERS_FILE, JSON.stringify(out))
}
