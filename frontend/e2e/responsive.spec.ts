import { expect, test } from "@playwright/test"

const PAGES = ["/", "/query", "/memories", "/graph", "/compression", "/adaptation", "/metrics", "/versions", "/benchmarks"]
const VIEWPORTS = [
  { name: "phone-320", width: 320, height: 640 },
  { name: "phone-390", width: 390, height: 844 },
  { name: "tablet-820", width: 820, height: 1180 },
  { name: "laptop-1440", width: 1440, height: 900 },
]

for (const vp of VIEWPORTS) {
  test.describe(`${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height }, hasTouch: vp.width < 800 })
    for (const path of PAGES) {
      test(`${path} has no horizontal page overflow and no console errors`, async ({ page }) => {
        const errors: string[] = []
        page.on("pageerror", (e) => errors.push(String(e)))
        page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
        await page.goto(path, { waitUntil: "networkidle" })
        await page.waitForTimeout(500)
        const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }))
        expect(sw, `scrollWidth ${sw} > clientWidth ${cw}`).toBeLessThanOrEqual(cw + 1)
        expect(errors).toEqual([])
      })
    }
  })
}

for (const vp of VIEWPORTS) {
  test.describe(`${vp.name} new views`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height }, hasTouch: vp.width < 800 })
    const check = async (page: import("@playwright/test").Page, errors: string[]) => {
      await page.waitForTimeout(500)
      const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }))
      expect(sw, `scrollWidth ${sw} > clientWidth ${cw}`).toBeLessThanOrEqual(cw + 1)
      expect(errors).toEqual([])
    }
    const watch = (page: import("@playwright/test").Page) => {
      const errors: string[] = []
      page.on("pageerror", (e) => errors.push(String(e)))
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()))
      return errors
    }
    test("graph module list view and a traced route fit the viewport", async ({ page }) => {
      const errors = watch(page)
      await page.goto("/graph", { waitUntil: "networkidle" })
      await page.getByLabel("Query to trace").fill("How do I narrow a union type?")
      await page.getByRole("button", { name: /^Trace$/ }).click()
      await expect(page.getByTestId("route-banner")).toBeVisible()
      await check(page, errors)
      await page.getByRole("tab", { name: "Module list" }).click()
      await expect(page.getByRole("list", { name: "Modules" }).or(page.getByRole("table", { name: "Modules" }))).toBeVisible()
      await check(page, errors)
    })
    test("query playground comparison fits the viewport", async ({ page }) => {
      const errors = watch(page)
      await page.goto("/query", { waitUntil: "networkidle" })
      await page.getByLabel("Query", { exact: true }).fill("What happens when Redis reaches maxmemory?")
      await page.getByRole("tab", { name: /Compare all four/ }).click()
      await page.getByRole("button", { name: /Compare four strategies/ }).click()
      await expect(page.getByRole("table", { name: "Strategy comparison" })).toBeVisible()
      await check(page, errors)
    })
    test("benchmarks strategies tab fits the viewport", async ({ page }) => {
      const errors = watch(page)
      await page.goto("/benchmarks", { waitUntil: "networkidle" })
      await page.getByRole("tab", { name: "Retrieval strategies" }).click()
      await expect(page.getByRole("table", { name: "Retrieval quality by strategy" })).toBeVisible()
      await check(page, errors)
    })
  })
}

test("mobile navigation drawer opens and navigates", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/")
  await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0)
  await page.getByRole("button", { name: "Open navigation" }).click()
  await page.getByRole("link", { name: "Graph Explorer" }).click()
  await expect(page.getByRole("heading", { name: "Graph Explorer" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible()
})

test("mobile touch targets are at least 40px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/")
  for (const name of ["Open navigation", "Toggle light and dark theme"]) {
    const box = await page.getByRole("button", { name }).first().boundingBox()
    expect(box!.height).toBeGreaterThanOrEqual(40)
    expect(box!.width).toBeGreaterThanOrEqual(40)
  }
})

test("graph is touch-operable: tapping a module opens a bottom sheet with its details", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const page = await ctx.newPage()
  await page.goto("/graph", { waitUntil: "networkidle" })
  const node = page.locator('[data-testid^="module-node-"]').filter({ hasText: "records" }).first()
  await node.waitFor()
  await node.tap()
  await expect(page.getByTestId("module-panel")).toBeVisible()
  await expect(page.getByText(/Incoming \(\d+\)/)).toBeVisible()
  await ctx.close()
})
