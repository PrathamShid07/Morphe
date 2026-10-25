import { expect, request as playwrightRequest, test, type Page } from "@playwright/test"
import { ADMIN_USERNAME, E2E_PASSWORD, NO_SESSION, USER_B_STATE, WEB, adminApi, adminPassword, e2eUsers, uniqueEmail, userApi, visitorApi } from "./auth"

test.describe.configure({ mode: "serial" })

const ADMIN_NAV = ["Overview", "Query Playground", "Memories", "Graph Explorer", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks", "Users", "Activity"]
const ADMIN_ONLY = ["Overview", "Memories", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks", "Users", "Activity"]
const MODES = ["global", "graph", "progressive", "soft"]
const A_TEXT = "Project Zephyrwind: the quartz-nebula vault key rotates every Tuesday and is kept in the zebra rack."
const A_Q = "when does the quartz-nebula vault key rotate and where is it kept"
const B_TEXT = "Orion bakery notes: the sourdough starter is fed twice daily and the rye flour supplier is called Kestrel Mills."
const B_Q = "sourdough starter feeding schedule and rye flour supplier"

const nav = (page: Page) => page.getByRole("navigation", { name: "Main" }).getByRole("link")

test.describe("a visitor who is not signed in", () => {
  test.use({ storageState: NO_SESSION, viewport: { width: 1440, height: 900 } })

  test("sees only the authentication page, on every URL", async ({ page }) => {
    for (const path of ["/", "/query", "/memories", "/users", "/graph"]) {
      await page.goto(path)
      await expect(page.getByRole("tab", { name: /sign in/i })).toBeVisible()
      await expect(page.getByRole("tab", { name: /create account/i })).toBeVisible()
      await expect(page.getByRole("tab", { name: /administrator/i })).toBeVisible()
      await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0)
    }
  })

  test("the API refuses every protected route with 401 and registration cannot create an administrator", async () => {
    const api = await visitorApi()
    for (const p of ["me/graph", "me/summary", "memories", "metrics/summary", "versions", "admin/users", "admin/activity", "config", "integrity"]) {
      expect((await api.get(p)).status(), p).toBe(401)
    }
    for (const p of ["me/query", "me/memories", "query", "ingestion", "versions/checkpoint", "admin/reset"]) expect((await api.post(p, { data: {} })).status(), p).toBe(401)
    const bad = await api.post("auth/register", { data: { full_name: "Mallory Root", email: uniqueEmail("mal"), password: E2E_PASSWORD, confirm_password: E2E_PASSWORD, role: "admin" } })
    expect(bad.status()).toBe(422)  // a role in the request is refused outright
    expect((await api.get("http://127.0.0.1:8000/health")).status()).toBe(200)
    await api.dispose()
  })
})

test.describe("a new user, end to end through the browser", () => {
  test.use({ storageState: NO_SESSION, viewport: { width: 1440, height: 900 } })
  const email = uniqueEmail("flow")
  const name = "Flora Flow"

  test("creates an account, saves a private memory, asks about it, inspects the graph and signs out", async ({ page }) => {
    await page.goto("/")
    await page.getByRole("tab", { name: /create account/i }).click()
    await page.getByLabel("Full name", { exact: true }).fill(name)
    await page.getByLabel("Email address", { exact: true }).fill(email)
    await page.getByLabel("Password", { exact: true }).fill("short")
    await page.getByLabel("Confirm password", { exact: true }).fill("different")
    await page.getByRole("button", { name: "Create account" }).click()
    await expect(page.getByText("The passwords do not match.")).toBeVisible()
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD)
    await page.getByLabel("Confirm password", { exact: true }).fill(E2E_PASSWORD)
    await page.getByRole("button", { name: "Create account" }).click()

    await expect(page.getByRole("heading", { name: "Ask your memory anything." })).toBeVisible()
    await expect(nav(page)).toHaveText(["Query Playground", "Graph Explorer"])
    for (const label of ADMIN_ONLY) await expect(page.getByRole("link", { name: label })).toHaveCount(0)
    await expect(page.getByTestId("account-identity").first()).toContainText(email)
    await expect(page.getByTestId("mode-indicator").first()).toContainText("User Mode")

    // a brand-new account queries without error even though it has saved nothing
    await page.getByLabel("Your question", { exact: true }).fill("anything at all")
    await page.getByRole("button", { name: "Ask" }).click()
    await expect(page.getByRole("list", { name: "Retrieved memories" }).or(page.getByText("No relevant memories were found"))).toBeVisible({ timeout: 60_000 })

    await page.getByRole("button", { name: /save to my memory/i }).first().click()
    await page.getByLabel("What should MORPHE remember?", { exact: true }).fill(A_TEXT)
    await page.getByLabel(/Title or source label/).fill("Zephyrwind")
    await page.getByRole("button", { name: "Save to My Memory", exact: true }).click()
    await expect(page.getByText(/Saved to your private memory/)).toBeVisible({ timeout: 60_000 })
    await expect(page.getByRole("list", { name: "Recently saved memories" })).toContainText("quartz-nebula")

    await page.getByLabel("Your question", { exact: true }).fill(A_Q)
    await page.getByRole("button", { name: "Ask" }).click()
    const first = page.getByRole("list", { name: "Retrieved memories" }).locator("li").first()
    await expect(first).toContainText("quartz-nebula vault key", { timeout: 60_000 })
    await expect(first.getByTestId("result-scope")).toHaveText("Mine")
    await expect(page.getByTestId("route-explanation")).toBeVisible()

    await page.getByRole("link", { name: "Graph Explorer" }).first().click()
    await expect(page.getByText("read-only", { exact: true })).toBeVisible()
    await expect(page.getByTestId("module-node-Zephyrwind")).toBeVisible()

    await page.getByRole("button", { name: /log out/i }).first().click()
    await expect(page.getByRole("tab", { name: /sign in/i })).toBeVisible()
    await page.goto("/query")
    await expect(page.getByRole("tab", { name: /sign in/i })).toBeVisible()  // the session really ended

    // signing back in restores the same private memory
    await page.getByLabel("Email address", { exact: true }).fill(email)
    await page.getByLabel("Password", { exact: true }).fill("not-the-password-1")
    await page.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByRole("alert")).toContainText(/invalid email or password/i)
    await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD)
    await page.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByRole("heading", { name: "Ask your memory anything." })).toBeVisible()
    await expect(page.getByRole("list", { name: "Recently saved memories" })).toContainText("quartz-nebula")
  })

  test.afterAll(async () => {  // remove the account this test created
    const admin = await adminApi()
    const users = await (await admin.get("admin/users", { params: { search: email } })).json()
    for (const u of users.items) await admin.delete(`admin/users/${u.user_id}`, { params: { confirm: "DELETE" } })
    await admin.dispose()
  })
})

test.describe("two users never see each other's memories", () => {
  test("private memories, graphs, ids and history stay separate under every strategy", async ({ browser }) => {
    const { a, b } = e2eUsers()
    const A = await userApi(a.email), B = await userApi(b.email)
    const sa = await (await A.post("me/memories", { data: { text: A_TEXT, title: "Zephyrwind" } })).json()
    const sb = await (await B.post("me/memories", { data: { text: B_TEXT, title: "Orion bakery" } })).json()
    expect(sa.chunks_created).toBeGreaterThan(0)
    expect(sb.chunks_created).toBeGreaterThan(0)
    const ask = async (c: typeof A, q: string, mode: string) => (await c.post("me/query", { data: { query: q, mode, top_k: 10, debug_trace: true } })).json()
    expect((await ask(A, A_Q, "progressive")).results[0].memory_id).toBe(sa.memory_ids[0])
    expect((await ask(B, B_Q, "progressive")).results[0].memory_id).toBe(sb.memory_ids[0])
    for (const mode of MODES) {
      const ra = await ask(B, A_Q, mode)
      expect(ra.results.map((r: { memory_id: string }) => r.memory_id), `B asking A's question, ${mode}`).not.toContain(sa.memory_ids[0])
      expect(JSON.stringify(ra)).not.toContain("quartz-nebula")
      const rb = await ask(A, B_Q, mode)
      expect(rb.results.map((r: { memory_id: string }) => r.memory_id), `A asking B's question, ${mode}`).not.toContain(sb.memory_ids[0])
      expect(JSON.stringify(rb)).not.toContain("sourdough")
    }
    const gA = JSON.stringify(await (await A.get("me/graph")).json()), gB = JSON.stringify(await (await B.get("me/graph")).json())
    expect(gA).toContain("Zephyrwind")
    expect(gA).not.toContain("Orion")
    expect(gB).toContain("Orion")
    expect(gB).not.toContain("Zephyrwind")
    // ids belonging to the other user grant nothing
    const qa = (await ask(A, A_Q, "graph")).query_id
    expect((await B.get(`me/queries/${qa}`)).status()).toBe(404)
    expect((await B.delete(`me/memories/${sa.memory_ids[0]}`)).status()).toBe(404)
    expect((await B.get(`me/modules/${sa.module_ids[0]}`)).status()).toBe(404)
    expect((await B.post("me/memories", { data: { text: "planted", owner_user_id: a.id } })).status()).toBe(422)
    expect((await A.get("me/memories")).status()).toBe(200)
    // ordinary users are refused every administrator route
    for (const p of ["admin/users", "admin/activity", "memories", "versions", "metrics/summary"]) expect((await B.get(p)).status(), p).toBe(403)
    expect((await B.post("ingestion", { data: { source_name: "x", text: "y" } })).status()).toBe(403)
    // and in the browser, B's graph shows only B's topic
    const ctx = await browser.newContext({ storageState: USER_B_STATE, viewport: { width: 1440, height: 900 } })
    const page = await ctx.newPage()
    await page.goto("/graph", { waitUntil: "networkidle" })
    await expect(page.getByTestId("module-node-Orion Bakery").or(page.getByTestId("module-node-Orion bakery"))).toBeVisible()
    await expect(page.getByText("Zephyrwind")).toHaveCount(0)
    await ctx.close()
    await A.dispose(); await B.dispose()
  })
})

test.describe("administrator", () => {
  test.use({ storageState: NO_SESSION, viewport: { width: 1440, height: 900 } })

  test("signs in separately, sees Users and Activity, and manages an account", async ({ page, browser }) => {
    const { a, b } = e2eUsers()
    await page.goto("/")
    await page.getByRole("tab", { name: /administrator/i }).click()
    await page.getByLabel("Username", { exact: true }).fill(ADMIN_USERNAME)
    await page.getByLabel("Password", { exact: true }).fill("definitely-not-the-password")
    await page.getByRole("button", { name: "Sign in as administrator" }).click()
    await expect(page.getByRole("alert")).toContainText(/invalid/i)
    await page.getByLabel("Password", { exact: true }).fill(adminPassword())
    await page.getByRole("button", { name: "Sign in as administrator" }).click()
    await expect(page.getByTestId("mode-indicator").first()).toContainText("Admin Mode")
    await expect(nav(page)).toHaveText(ADMIN_NAV)
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(adminPassword())
    expect((await page.context().cookies()).find((c) => c.name === "morphe_admin")?.httpOnly).toBe(true)

    // Users: the two e2e accounts with their real counts
    await page.getByRole("link", { name: "Users" }).first().click()
    const table = page.getByRole("table", { name: "Registered users" })
    await expect(table.getByText(a.email)).toBeVisible()
    await expect(table.getByText(b.email)).toBeVisible()
    await page.getByLabel("Search users", { exact: true }).fill("bob e2e")
    await expect(table.getByText(a.email)).toHaveCount(0)
    await table.getByText(b.email).click()
    const detail = page.getByRole("dialog")
    await expect(detail.getByLabel("Activity summary", { exact: true })).toContainText("Private memories")
    await expect(detail.getByText("Question answered").first()).toBeVisible()  // the latest events: B asked many questions
    await expect(detail.getByText(/The text of retrieved memories is not shown/)).toBeVisible()
    await expect(detail).not.toContainText("Kestrel Mills")  // memory content (not the question) is never on this screen

    // disabling takes effect immediately for B
    await detail.getByRole("button", { name: /disable account/i }).click()
    await page.getByRole("alertdialog").getByLabel(/Reason/).fill("e2e check")
    await page.getByRole("alertdialog").getByRole("button", { name: "Disable account" }).click()
    await expect(page.getByRole("region", { name: /Notifications/ }).getByText("Account disabled")).toBeVisible()
    const bctx = await browser.newContext({ storageState: USER_B_STATE })
    const bpage = await bctx.newPage()
    await bpage.goto("/query")
    await expect(bpage.getByRole("tab", { name: /sign in/i })).toBeVisible()  // its session was revoked
    const blocked = await playwrightRequest.newContext({ baseURL: WEB })
    const rl = await blocked.post("/api/v1/auth/login", { data: { email: b.email, password: E2E_PASSWORD } })
    expect(rl.status()).toBe(403)
    expect((await rl.json()).error.code).toBe("account_disabled")
    await blocked.dispose()
    await bctx.close()
    await page.getByRole("button", { name: /reactivate account/i }).click()
    await page.getByRole("alertdialog").getByRole("button", { name: "Reactivate account" }).click()
    await expect(page.getByRole("region", { name: /Notifications/ }).getByText("Account reactivated")).toBeVisible()
    expect((await (await userApi(b.email)).get("me/summary")).status()).toBe(200)  // B can sign in again

    // Activity: the audit trail distinguishes successes from failures and can be filtered
    await page.keyboard.press("Escape")
    await page.getByRole("link", { name: "Activity" }).first().click()
    const log = page.getByRole("table", { name: "Activity log" })
    await expect(log.getByText("Memory saved").first()).toBeVisible()
    await expect(log.getByText("User sign-in").first()).toBeVisible()
    await expect(log.getByText("Success").first()).toBeVisible()
    await page.getByLabel("Search activity", { exact: true }).fill("user.disabled")
    await expect(log.getByText("Account disabled").first()).toBeVisible()
    await log.getByText("Account disabled").first().click()
    await expect(page.getByTestId("event-metadata")).toContainText("e2e check")
  })

  test("administrator and user sessions are separate; sign-out revokes on the server", async () => {
    const { a } = e2eUsers()
    const admin = await adminApi()
    for (const p of ["me/graph", "me/summary"]) expect((await admin.get(p)).status(), p).toBe(403)  // administrators have no personal memory
    expect((await admin.get("admin/users")).status()).toBe(200)
    const user = await userApi(a.email)
    expect((await user.get("admin/users")).status()).toBe(403)
    const state = await user.storageState()
    const cookie = state.cookies.find((c) => c.name === "morphe_user")!.value
    expect((await user.post("auth/logout")).status()).toBe(200)
    const replay = await playwrightRequest.newContext({ extraHTTPHeaders: { cookie: `morphe_user=${cookie}` } })
    expect((await replay.get("http://127.0.0.1:8000/api/v1/me/summary")).status()).toBe(401)  // the old cookie no longer works anywhere
    await replay.dispose(); await user.dispose(); await admin.dispose()
  })
})
