import type { ReactElement } from "react"
import { render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { vi } from "vitest"
import { ThemeProvider } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import { IngestProvider } from "@/components/app/IngestDialog"
import { AuthContext, type AuthState } from "@/lib/auth"

type Handler = unknown | ((req: { url: URL; method: string; body: unknown }) => unknown)
export type Routes = Record<string, Handler>
export interface Call { method: string; path: string; body: unknown; headers: Record<string, string> }

/** Routes are keyed "METHOD /api/v1/path". A handler may return data or a { __status, __error } object. */
export function mockApi(routes: Routes) {
  const calls: Call[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost")
    const method = (init?.method ?? "GET").toUpperCase()
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body
    calls.push({ method, path: url.pathname, body, headers: { ...((init?.headers as Record<string, string> | undefined) ?? {}) } })
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

/** A static auth context for component tests. The default is the administrator view (what the pre-existing page tests exercise). */
export function testAuth(kind: "admin" | "user" = "admin", over: Partial<AuthState> = {}): AuthState {
  const admin = kind === "admin"
  return { loading: false, isAdminSession: admin, isAdminView: admin, username: admin ? "test-admin" : null, adminConfigured: true, loginOpen: false,
    openLogin: () => {}, closeLogin: () => {}, login: async () => {}, logout: async () => {}, switchToUser: () => {}, switchToAdmin: () => {}, refresh: async () => {}, ...over }
}

export function renderWith(ui: ReactElement, route = "/", auth: AuthState = testAuth("admin")) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } } })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>
            <AuthContext.Provider value={auth}>
              <IngestProvider>{ui}</IngestProvider>
            </AuthContext.Provider>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}
