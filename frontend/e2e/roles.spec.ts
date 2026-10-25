import { expect, request as playwrightRequest, test } from "@playwright/test"
import { ADMIN_USERNAME, adminApi, adminPassword, visitorApi } from "./auth"

test.describe.configure({ mode: "serial" })

const ADMIN_LINKS = ["Overview", "Memories", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks"]

test.describe("User Mode (no session)", () => {
  test.use({ storageState: { cookies: [], origins: [] }, viewport: { width: 1440, height: 900 } })

  test("opens without authentication on the Query Playground with restricted navigation", async ({ page }) => {
    await page.goto("/")
    await expect(page).toHaveURL(/\/query$/)
    await expect(page.getByRole("heading", { name: "Ask your memory anything." })).toBeVisible()
    const nav = page.getByRole("navigation", { name: "Main" })
    await expect(nav.getByRole("link")).toHaveText(["Query Playground", "Graph Explorer"])
    await expect(page.getByTestId("mode-indicator").first()).toContainText("User Mode")
    for (const name of ADMIN_LINKS) await expect(nav.getByRole("link", { name })).toHaveCount(0)
    await page.goto("/memories")  // admin URLs are not routable for a visitor
    await expect(page).toHaveURL(/\/query$/)
  })

  test("a real question returns real results with a route explanation", async ({ page }) => {
    await page.goto("/query")
    await page.getByLabel("Your question").fill("How do I configure Django to use PostgreSQL?")
    await page.getByRole("button", { name: "Ask" }).click()
    const list = page.getByRole("list", { name: "Retrieved memories" })
    await expect(list.locator("li").first()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId("route-explanation")).toBeVisible()
    await expect(page.getByText(/administrator/i).and(page.getByRole("alert"))).toHaveCount(0)
  })

  test("the graph explorer is read-only and shows no operational detail", async ({ page }) => {
    await page.goto("/graph", { waitUntil: "networkidle" })
    await expect(page.getByText("read-only", { exact: true })).toBeVisible()
    await expect(page.getByText(/split capacity threshold/)).toHaveCount(0)
    await page.getByRole("tab", { name: "Module list" }).click()
    await expect(page.getByRole("button", { name: "Ingest content" })).toHaveCount(0)
  })

  test("the API refuses administrator operations with 401 and does not change state", async () => {
    const api = await visitorApi()
    const admin = await adminApi()
    const count = async () => (await (await admin.get("metrics/summary")).json()).active_memory_count
    const before = await count()
    expect((await api.get("http://127.0.0.1:8000/health")).status()).toBe(200)
    for (const [m, p] of [["get", "memories"], ["get", "metrics/summary"], ["get", "versions"], ["get", "benchmarks/runs"], ["get", "config"], ["get", "integrity"]] as const) {
      expect((await api[m](p)).status(), `${m} ${p}`).toBe(401)
    }
    for (const p of ["admin/reset", "admin/demo/seed", "compression/run", "adaptation/run-window", "ingestion", "versions/checkpoint", "benchmarks/run"]) expect((await api.post(p, { data: {} })).status(), p).toBe(401)
    expect((await api.get("explore/graph")).status()).toBe(200)  // the read-only graph stays available
    expect(await count()).toBe(before)  // nothing was mutated by the refused requests
    await admin.dispose()
    await api.dispose()
  })
})

test.describe("Admin sign-in, switching and sign-out", () => {
  test.use({ storageState: { cookies: [], origins: [] }, viewport: { width: 1440, height: 900 } })

  test("wrong password is rejected; the right one opens the dashboard; switch, then sign out", async ({ page, context }) => {
    await page.goto("/query")
    await page.getByRole("button", { name: /admin login/i }).first().click()
    await page.getByLabel("Username", { exact: true }).fill(ADMIN_USERNAME)
    await page.getByLabel("Password", { exact: true }).fill("definitely-not-the-password")
    await page.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText(/invalid/i)
    await expect(page.getByTestId("mode-indicator").first()).toContainText("User Mode")

    await page.getByLabel("Password", { exact: true }).fill(adminPassword())
    await page.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByTestId("mode-indicator").first()).toContainText("Admin Mode")
    const nav = page.getByRole("navigation", { name: "Main" })
    await expect(nav.getByRole("link")).toHaveText(["Overview", "Query Playground", "Memories", "Graph Explorer", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks"])
    expect((await context.cookies()).find((c) => c.name === "morphe_admin")?.httpOnly).toBe(true)
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(adminPassword())

    await page.getByRole("button", { name: /switch to user mode/i }).first().click()
    await expect(page.getByRole("heading", { name: "Ask your memory anything." })).toBeVisible()
    await expect(nav.getByRole("link")).toHaveText(["Query Playground", "Graph Explorer"])

    await page.getByRole("button", { name: /return to admin mode/i }).first().click()
    await expect(page.getByTestId("mode-indicator").first()).toContainText("Admin Mode")

    const stolen = (await context.cookies()).find((c) => c.name === "morphe_admin")!.value  // what an attacker who copied the cookie would hold
    await page.getByRole("button", { name: "Sign out" }).first().click()
    await expect(page.getByRole("heading", { name: "Ask your memory anything." })).toBeVisible()
    // the session was revoked on the server, not just forgotten by the browser: the old cookie no longer works anywhere
    const replay = await playwrightRequest.newContext({ extraHTTPHeaders: { cookie: `morphe_admin=${stolen}` } })
    expect((await replay.get("http://127.0.0.1:8000/api/v1/memories")).status()).toBe(401)
    await replay.dispose()
    expect((await context.request.get("http://127.0.0.1:5173/api/v1/memories")).status()).toBe(403)  // the browser is still a plain visitor: authenticated, but not an administrator
  })
})
