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
  await expect(page.getByText("Neighbouring modules")).toBeVisible()
  await ctx.close()
})
