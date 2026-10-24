import type { ReactElement } from "react"
import { render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { vi } from "vitest"
import { ThemeProvider } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import { IngestProvider } from "@/components/app/IngestDialog"

type Handler = unknown | ((req: { url: URL; method: string; body: unknown }) => unknown)
export type Routes = Record<string, Handler>
export interface Call { method: string; path: string; body: unknown }

/** Routes are keyed "METHOD /api/v1/path". A handler may return data or a { __status, __error } object. */
export function mockApi(routes: Routes) {
  const calls: Call[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost")
    const method = (init?.method ?? "GET").toUpperCase()
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body
    calls.push({ method, path: url.pathname, body })
    const key = `${method} ${url.pathname}`
    const h = routes[key]
    if (h === undefined) return new Response(JSON.stringify({ error: { code: "not_mocked", message: `no mock for ${key}` } }), { status: 404 })
    const data = typeof h === "function" ? (h as (r: unknown) => unknown)({ url, method, body }) : h
    const d = data as { __status?: number; __error?: { code: string; message: string } }
    if (d && typeof d === "object" && d.__status) return new Response(JSON.stringify({ error: d.__error }), { status: d.__status })
    return new Response(JSON.stringify(data), { status: 200 })
  })
  vi.stubGlobal("fetch", fn)
  return { calls, fn, called: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path) }
}

export function renderWith(ui: ReactElement, route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } } })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>
            <IngestProvider>{ui}</IngestProvider>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}
