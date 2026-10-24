import { expect, test } from "@playwright/test"

const API = "http://127.0.0.1:8000/api/v1"
test.describe.configure({ mode: "serial" })

test.beforeAll(async ({ request }) => {
  const r = await request.post(`${API}/admin/reset`, { data: { confirm: "RESET" } })
  expect(r.ok()).toBeTruthy()
  expect((await request.post(`${API}/admin/demo/seed`)).ok()).toBeTruthy()
})

test("query shows how graph routing selected modules", async ({ page }) => {
  await page.goto("/query")
  await page.getByLabel("Query", { exact: true }).fill("How do I configure Django to use PostgreSQL?")
  await page.getByRole("button", { name: /run query/i }).click()
  const trace = page.getByTestId("routing-trace")
  await expect(trace).toBeVisible()
  await expect(trace.getByText("1 · Seed modules")).toBeVisible()
  await expect(page.getByRole("list", { name: "Retrieved memories" }).locator("li").first()).toContainText("PostgreSQL")
})

test("ingesting new content creates memories visible in the explorer", async ({ page, request }) => {
  await page.goto("/")
  await page.getByRole("button", { name: "Ingest content" }).first().click()
  await page.getByLabel("Source name", { exact: true }).fill("e2e_notes.md")
  await page.getByLabel("Text", { exact: true }).fill("## Streams\n\nRedis streams add consumer groups for reliable processing of events.")
  await page.getByLabel(/^Topic/).fill("redis")
  await page.getByRole("button", { name: "Ingest", exact: true }).click()
  await expect(page.getByText(/Ingested \d+ memories/)).toBeVisible()
  await page.goto("/memories")
  await page.getByLabel("Search memories").fill("consumer groups")
  await expect(page.getByText("Redis streams add consumer groups").first()).toBeVisible()
  const s = await (await request.get(`${API}/metrics/summary`)).json()
  expect(s.active_memory_count).toBeGreaterThan(60)
})

test("mixed-topic module: evaluate, apply, see the new version, then roll back from the UI", async ({ page, request }) => {
  expect((await request.post(`${API}/admin/demo/scenario/mixed`)).ok()).toBeTruthy()
  const before = await (await request.get(`${API}/versions`)).json()

  await page.goto("/adaptation")
  const card = page.getByTestId("proposal").filter({ hasText: "Split module" })
  await expect(card).toBeVisible()
  await expect(card).toContainText("mixes distinct topics")
  await card.getByRole("button", { name: /evaluate & apply/i }).click()
  await page.getByRole("alertdialog").getByRole("button", { name: "Evaluate & apply" }).click()
  const dlg = page.getByRole("dialog")
  await expect(dlg).toContainText("Decision: accept", { timeout: 60_000 })
  await expect(dlg.getByRole("note")).toContainText("Committed")
  await expect(dlg.getByRole("table", { name: "Metric comparison" })).toBeVisible()
  await page.keyboard.press("Escape")

  const after = await (await request.get(`${API}/versions`)).json()
  expect(after.active_version).not.toBe(before.active_version)

  // roll back to the version that was active before the split, through the confirmation dialog
  await page.goto("/versions")
  const done = await (await request.get(`${API}/adaptation/events?status=completed&limit=1`)).json()
  const target = done.items[0].before_version as string // the checkpoint saved immediately before the split
  await page.getByRole("row").filter({ has: page.getByText(target, { exact: true }) }).getByRole("button", { name: /restore/i }).click()
  const rd = page.getByRole("dialog")
  await expect(rd).toContainText("Expected changes")
  await expect(rd.getByRole("button", { name: /restore this version/i })).toBeDisabled()
  await rd.getByRole("checkbox").click()
  await rd.getByRole("button", { name: /restore this version/i }).click()
  await expect(page.getByText(/Restored — new active version/)).toBeVisible({ timeout: 60_000 })

  const integrity = await (await request.get(`${API}/integrity`)).json()
  expect(integrity.passed).toBe(true)
  const graph = await (await request.get(`${API}/graph`)).json()
  const python = graph.nodes.find((n: { id: string }) => n.id === "mod_python")
  expect(python.data.recordCount).toBe(18) // children merged back into the original module
  const events = await (await request.get(`${API}/adaptation/events?status=rolled_back`)).json()
  expect(events.total).toBe(1)

  await page.goto("/adaptation")
  await page.getByRole("tab", { name: /Rolled back/ }).click()
  await expect(page.getByRole("button", { name: /Split module/ })).toContainText("Rolled Back")
})

test("compression center reflects a real threshold crossing", async ({ page, request }) => {
  expect((await request.post(`${API}/admin/demo/scenario/compression`)).ok()).toBeTruthy()
  await page.goto("/compression")
  await expect(page.getByText(/demo budget override active/)).toBeVisible()
  await expect(page.getByRole("button", { name: "Simulate" })).toBeVisible()
  await page.getByRole("button", { name: "Simulate" }).click()
  await expect(page.getByText(/Simulation result \(nothing was changed\)/)).toBeVisible()
  await request.put(`${API}/compression/budget`, { data: { budget_tokens: null } })
})

test.afterAll(async ({ request }) => {
  await request.post(`${API}/admin/reset`, { data: { confirm: "RESET" } })
  await request.post(`${API}/admin/demo/seed`)
})
