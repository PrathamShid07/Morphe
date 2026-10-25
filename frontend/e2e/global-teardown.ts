import { existsSync } from "node:fs"
import { USERS_FILE, adminApi, e2eUsers } from "./auth"

/** Delete the accounts (and their personal memories) that global-setup created, so test runs leave nothing behind in the live database. */
export default async function globalTeardown() {
  if (!existsSync(USERS_FILE)) return
  const admin = await adminApi()
  const { a, b } = e2eUsers()
  for (const u of [a, b]) await admin.delete(`admin/users/${u.id}`, { params: { confirm: "DELETE" } })
  await admin.dispose()
}
