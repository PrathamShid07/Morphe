import { defineConfig } from "@playwright/test"

// E2E tests drive the REAL stack: MongoDB + FastAPI on :8000 (see README) and the Vite dev server (started here).
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:5173", trace: "retain-on-failure" },
  webServer: { command: "npm run dev -- --host 127.0.0.1", url: "http://127.0.0.1:5173", reuseExistingServer: true, timeout: 60_000 },
})
