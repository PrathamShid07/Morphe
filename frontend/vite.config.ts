import path from "node:path"
import { defineConfig } from "vitest/config"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// In development the SPA talks to FastAPI through this proxy, so the browser never needs CORS and no URL is hardcoded.
const target = process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  server: {
    port: 5173,
    proxy: { "/api": { target, changeOrigin: true }, "/health": { target, changeOrigin: true } },
  },
  preview: { proxy: { "/api": { target, changeOrigin: true }, "/health": { target, changeOrigin: true } } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    include: ["src/**/*.test.{ts,tsx}"],
  },
})
