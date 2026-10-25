import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { App } from "@/App"
import GraphExplorer from "@/pages/GraphExplorer"
import Memories from "@/pages/Memories"
import { setCsrfToken } from "@/api/client"
import { AuthProvider } from "@/lib/auth"
import { setRoute } from "@/lib/route-store"
import { ThemeProvider } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import * as fx from "./fixtures"
import { mockApi, renderWith, testAuth, type Routes } from "./utils"

const A = "/api/v1"
const visitor = { authenticated: false, role: "user", username: null, expires_at: null, csrf_token: null, admin_configured: true }
const adminSession = { authenticated: true, role: "admin", username: "test-admin", expires_at: Math.floor(Date.now() / 1000) + 3600, csrf_token: "csrf-abc", admin_configured: true }
const ADMIN_ONLY_LABELS = ["Overview", "Memories", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks"]

/** The whole SPA with the real AuthProvider and routing; only the network is replaced. */
function renderApp(route = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 }, mutations: { retry: false } } })
  return render(
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[route]}>
            <AuthProvider><App /></AuthProvider>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}

const adminPages: Routes = {
  [`GET ${A}/metrics/summary`]: fx.summary, [`GET ${A}/config`]: fx.config, [`GET ${A}/compression/status`]: fx.compressionStatus,
  [`GET ${A}/adaptation/status`]: fx.adaptationStatus, [`GET ${A}/adaptation/candidates`]: { candidates: [], skipped: [] },
  [`GET ${A}/adaptation/events`]: { items: [], total: 0, status_counts: {} }, [`GET ${A}/versions`]: { items: [], total: 0, active_version: null },
  [`GET ${A}/metrics/history`]: { points: [] }, [`GET ${A}/compression/events`]: { items: [], total: 0 },
}
const userPages: Routes = { "GET /health": { status: "ok", ready: true }, [`GET ${A}/me/queries`]: { items: [] } }

/** A tiny server: the session endpoint reports whatever `state.session` currently is. */
function server(extra: Routes = {}, state = { session: visitor as unknown }) {
  const api = mockApi({
    ...userPages, ...adminPages,
    [`GET ${A}/auth/session`]: () => state.session,
    [`POST ${A}/auth/login`]: ({ body }: { body: unknown }) => {
      const b = body as { username: string; password: string }
      if (b.password !== "right-password") return { __status: 401, __error: { code: "invalid_credentials", message: "invalid username or password" } }
      state.session = adminSession
      return adminSession
    },
    [`POST ${A}/auth/logout`]: () => { state.session = visitor; return visitor },
    ...extra,
  })
  return { api, state }
}

beforeEach(() => { sessionStorage.clear(); setCsrfToken(null); setRoute(null); vi.restoreAllMocks() })

const navNames = () => within(screen.getAllByRole("navigation", { name: "Main", hidden: true })[0]).getAllByRole("link", { hidden: true }).map((a) => a.textContent)
async function signIn(user: ReturnType<typeof userEvent.setup>, password = "right-password") {
  await user.click((await screen.findAllByRole("button", { name: /admin login/i }))[0])
  await user.type(await screen.findByLabelText("Username"), "test-admin")
  await user.type(screen.getByLabelText("Password"), password)
  await user.click(screen.getByRole("button", { name: "Sign in" }))
}

describe("User Mode (the default for anyone without an administrator session)", () => {
  it("shows only Query Playground and Graph Explorer, a User Mode indicator and an Admin Login entry", async () => {
    server()
    renderApp("/")
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    expect(screen.getAllByTestId("mode-indicator")[0]).toHaveTextContent("User Mode")
    expect(screen.getAllByRole("button", { name: /admin login/i }).length).toBeGreaterThan(0)
    for (const label of ADMIN_ONLY_LABELS) expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest content/i })).not.toBeInTheDocument()
    expect(screen.queryByText("Admin Mode")).not.toBeInTheDocument()
  })

  it("never requests administrator data and keeps administrator routes unreachable", async () => {
    const { api } = server()
    renderApp("/memories")  // typing an admin URL does not open the admin page
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    await waitFor(() => expect(api.called("GET", `${A}/auth/session`).length).toBeGreaterThan(0))
    const forbidden = api.calls.filter((c) => !c.path.startsWith(`${A}/auth`) && !c.path.startsWith(`${A}/me`) && !c.path.startsWith(`${A}/explore`) && c.path !== "/health" && c.path !== `${A}/query`)
    expect(forbidden).toEqual([])
    for (const p of ["/versions", "/benchmarks", "/adaptation"]) expect(screen.queryByText(new RegExp(`^${p.slice(1)}$`, "i"))).not.toBeInTheDocument()
  })

  it("submits a question to the real query endpoint and shows results, the route explanation and the status", async () => {
    const { api } = server({ [`POST ${A}/query`]: { ...fx.queryResponse, architecture_version: null, mode: "progressive" } })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "How does Django route URLs?")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    const list = await screen.findByRole("list", { name: "Retrieved memories" })
    expect(within(list).getByText("Django maps URL patterns to views.")).toBeInTheDocument()
    expect(within(list).getByText("django.md")).toBeInTheDocument()
    expect(screen.getByTestId("route-explanation")).toHaveTextContent(/matched to Django/)
    expect(screen.getByTestId("retrieval-status")).toHaveTextContent(/Searched/)
    const sent = api.called("POST", `${A}/query`)[0].body as Record<string, unknown>
    expect(sent).toMatchObject({ query: "How does Django route URLs?", top_k: 5, debug_trace: true, mode: "progressive" })
    // none of the administrator-only query tools are offered
    expect(screen.queryByText(/compare all four/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Debug trace")).not.toBeInTheDocument()
    expect(screen.queryByText(/mem_1/)).not.toBeInTheDocument()
  })

  it("offers example questions, shows a loading state, an empty result state and a weak-match hint", async () => {
    let release: (v: unknown) => void = () => {}
    const pending = new Promise((r) => { release = r })
    let n = 0
    mockApi({
      ...userPages, [`GET ${A}/auth/session`]: visitor,
      [`POST ${A}/query`]: () => {
        n += 1
        return n === 1 ? { ...fx.queryResponse, results: [], mode: "graph" } : { ...fx.queryResponse, results: [{ ...fx.queryResponse.results[0], score: 0.12 }] }
      },
    })
    void pending; void release
    renderApp("/query")
    const user = userEvent.setup()
    expect(await screen.findByRole("region", { name: "Example questions" })).toBeInTheDocument()
    await user.type(screen.getByLabelText("Your question"), "something unrelated")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByText("No relevant memories were found")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByRole("note")).toHaveTextContent(/none is a strong match/)
  })

  it("shows a busy indicator while searching and a retryable error when the request fails", async () => {
    let calls = 0
    mockApi({ ...userPages, [`GET ${A}/auth/session`]: visitor, [`POST ${A}/query`]: () => { calls += 1; return calls === 1 ? { __status: 500, __error: { code: "internal_error", message: "unexpected server error" } } : fx.queryResponse } })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "hello")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("unexpected server error")
    await user.click(within(alert).getByRole("button", { name: /retry/i }))
    expect(await screen.findByRole("list", { name: "Retrieved memories" })).toBeInTheDocument()
  })

  it("shows the busy state while a request is in flight", async () => {
    let release: (v: Response) => void = () => {}
    const real = mockApi({ ...userPages, [`GET ${A}/auth/session`]: visitor })
    const inner = real.fn.getMockImplementation()!
    real.fn.mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/query") ? new Promise<Response>((r) => { release = r }) : inner(input, init))
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "hello")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByLabelText("Searching")).toBeInTheDocument()
    expect(screen.getByTestId("retrieval-status")).toHaveTextContent("Searching memory…")
    release(new Response(JSON.stringify(fx.queryResponse), { status: 200 }))
    expect(await screen.findByRole("list", { name: "Retrieved memories" })).toBeInTheDocument()
  })

  it("surfaces a permission or validation error from the API (for example top_k over the visitor limit)", async () => {
    mockApi({ ...userPages, [`GET ${A}/auth/session`]: visitor, [`POST ${A}/query`]: { __status: 422, __error: { code: "top_k_too_large", message: "top_k must be at most 20" } } })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "hello")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("top_k must be at most 20")
  })

  it("lists the visitor's own recent questions and reopens one from the server", async () => {
    const item = { query_id: "qry_1", query: "my earlier question", mode: "graph", top_k: 5, created_at: "2026-10-25T10:00:00Z", result_count: 1, latency_ms: 12 }
    const { api } = server({ [`GET ${A}/me/queries`]: { items: [item] }, [`GET ${A}/me/queries/qry_1`]: { ...fx.queryResponse, query: "my earlier question", created_at: item.created_at } })
    renderApp("/query")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: /my earlier question/ }))
    expect(await screen.findByRole("list", { name: "Retrieved memories" })).toBeInTheDocument()
    expect(api.called("GET", `${A}/me/queries/qry_1`)).toHaveLength(1)
  })
})

describe("User Mode Graph Explorer is read-only and sanitised", () => {
  const publicGraph = { ...fx.graph, architecture_version: null, nodes: fx.graph.nodes.map((n) => ({ ...n, data: { ...n.data, lastChangeOp: null, lastChangeAt: null, utilization: null } })),
    edges: fx.graph.edges.map((e) => ({ ...e, data: { ...e.data, createdAt: null, evidence: {}, operationId: null } })) }
  const publicMod = (m: Record<string, unknown>) => Object.fromEntries(["module_id", "name", "parent_id", "children", "record_count", "is_container", "keywords", "description", "shortcut_edges"].map((k) => [k, m[k]]))
  const routes: Routes = {
    [`GET ${A}/explore/graph`]: publicGraph, [`GET ${A}/explore/modules`]: { items: fx.modules.items.map(publicMod) },
    [`GET ${A}/explore/modules/mod_django`]: { ...publicMod(fx.moduleDetail as never), parent: publicMod(fx.modules.items[0] as never), neighbors: fx.moduleDetail.neighbors },
  }

  it("uses only the /explore endpoints, hides operational detail and offers no editing", async () => {
    const api = mockApi(routes)
    renderWith(<GraphExplorer />, "/graph", testAuth("user"))
    expect(await screen.findByText("read-only")).toBeInTheDocument()
    expect(screen.queryByText(/split capacity threshold/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^architecture/)).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest/i })).not.toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("tab", { name: "Module list" }))
    const row = (await screen.findAllByRole("button", { name: /^Django/ }))[0]
    expect(screen.queryByText("Last structural change")).not.toBeInTheDocument()
    expect(screen.queryByText("Recent access")).not.toBeInTheDocument()
    await userEvent.setup().click(row)
    const panel = await screen.findByTestId("module-panel")
    for (const hidden of [/^Capacity/, /Related memories/, /Adaptation history/, /Retrieval activity/, /Tokens/, /Architecture version/, /Open all in Memory Explorer/, /Adaptation Center/]) expect(within(panel).queryByText(hidden)).not.toBeInTheDocument()
    expect(within(panel).getByText("Incoming (0)")).toBeInTheDocument()
    expect(within(panel).getByText("Outgoing & shortcuts (1)")).toBeInTheDocument()
    const paths = [...new Set(api.calls.map((c) => c.path))]
    expect(paths.every((p) => p.startsWith(`${A}/explore/`))).toBe(true)
    expect(paths).not.toContain(`${A}/graph`)
    expect(api.calls.every((c) => c.method === "GET")).toBe(true)  // nothing is ever written
  })

  it("keeps search and the graph/list switch working", async () => {
    mockApi(routes)
    renderWith(<GraphExplorer />, "/graph", testAuth("user"))
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Search modules"), "postgre")
    await waitFor(() => expect(screen.getByText(/Showing/).textContent).toContain("1 match “postgre”"))
    expect(screen.getByRole("tab", { name: "Graph" })).toBeInTheDocument()
  })

  it("shows a plain empty state without an Ingest button", async () => {
    mockApi({ [`GET ${A}/explore/graph`]: { nodes: [], edges: [], architecture_version: null, counts: { nodes: 0, edges: 0 } }, [`GET ${A}/explore/modules`]: { items: [] } })
    renderWith(<GraphExplorer />, "/graph", testAuth("user"))
    expect(await screen.findByText("The graph is empty")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest content/i })).not.toBeInTheDocument()
  })
})

describe("Admin login dialog", () => {
  it("opens from Admin Login, validates empty fields and toggles password visibility", async () => {
    server()
    renderApp("/query")
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole("button", { name: /admin login/i }))[0])
    const dialog = await screen.findByRole("dialog", { name: "Administrator sign in" })
    expect(within(dialog).getByText(/memory management, evaluation, and architecture controls/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: "Sign in" }))
    expect(within(dialog).getByText("Enter your username.")).toBeInTheDocument()
    expect(within(dialog).getByText("Enter your password.")).toBeInTheDocument()
    const pw = within(dialog).getByLabelText("Password")
    expect(pw).toHaveAttribute("type", "password")
    await user.click(within(dialog).getByRole("button", { name: "Show password" }))
    expect(pw).toHaveAttribute("type", "text")
    await user.click(within(dialog).getByRole("button", { name: "Hide password" }))
    expect(pw).toHaveAttribute("type", "password")
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])  // clicking Admin Login alone grants nothing
  })

  it("reports a wrong password, clears it, and stays in User Mode", async () => {
    const { api } = server()
    renderApp("/query")
    const user = userEvent.setup()
    await signIn(user, "wrong")
    const dialog = await screen.findByRole("dialog", { name: "Administrator sign in" })
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("invalid username or password")
    expect(within(dialog).getByLabelText("Password")).toHaveValue("")
    expect(api.called("POST", `${A}/auth/login`)).toHaveLength(1)
    expect(screen.queryByText("Admin Mode")).not.toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
  })

  it("shows a setup hint and disables sign-in when the server has no administrator configured", async () => {
    server({}, { session: { ...visitor, admin_configured: false } })
    renderApp("/query")
    await userEvent.setup().click((await screen.findAllByRole("button", { name: /admin login/i }))[0])
    expect(await screen.findByText(/has not been set up on this server/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled()
  })

  it("reports a lockout from too many attempts", async () => {
    server({ [`POST ${A}/auth/login`]: { __status: 429, __error: { code: "too_many_attempts", message: "too many failed sign-in attempts; try again in 300 seconds" } } })
    renderApp("/query")
    await signIn(userEvent.setup())
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many failed sign-in attempts/)
  })
})

describe("Switching between the two experiences", () => {
  it("signs in, shows the full admin navigation, then switches to User Mode and back without a second login", async () => {
    const { api } = server()
    renderApp("/query")
    const user = userEvent.setup()
    await signIn(user)
    expect((await screen.findAllByText("Admin Mode"))[0]).toBeInTheDocument()
    await waitFor(() => expect(navNames()).toEqual(["Overview", "Query Playground", "Memories", "Graph Explorer", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks"]))
    expect(await screen.findByText("Recent activity")).toBeInTheDocument()  // landed on the dashboard
    await waitFor(() => expect(api.called("GET", `${A}/metrics/summary`).length).toBeGreaterThan(0))
    expect(screen.getAllByRole("button", { name: /ingest content/i }).length).toBeGreaterThan(0)

    await user.click(screen.getAllByRole("button", { name: /switch to user mode/i })[0])
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    expect(screen.queryByText("Admin Mode")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest content/i })).not.toBeInTheDocument()
    expect(screen.getAllByText(/administrator session is still active/i).length).toBeGreaterThan(0)
    expect(api.called("POST", `${A}/auth/login`)).toHaveLength(1)

    await user.click(screen.getAllByRole("button", { name: /return to admin mode/i })[0])
    expect((await screen.findAllByText("Admin Mode"))[0]).toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/login`)).toHaveLength(1)  // no re-authentication was needed
  })

  it("sends the CSRF token on state-changing admin requests and never stores credentials in browser storage", async () => {
    const { api } = server({ [`POST ${A}/ingestion`]: { source_id: "s", status: "completed", chunks_created: 1, module_ids: ["m"], new_module_ids: [], tokens_added: 1, active_tokens: 1, usage_percent: 0 } })
    renderApp("/query")
    const user = userEvent.setup()
    await signIn(user)
    await screen.findAllByText("Admin Mode")
    await user.click(screen.getAllByRole("button", { name: /ingest content/i })[0])
    await user.type(await screen.findByLabelText("Source name", { exact: true }), "n.md")
    await user.type(screen.getByLabelText("Text", { exact: true }), "some text")
    await user.click(screen.getByRole("button", { name: /^Ingest$/ }))
    await waitFor(() => expect(api.called("POST", `${A}/ingestion`)).toHaveLength(1))
    expect(api.called("POST", `${A}/ingestion`)[0].headers["X-CSRF-Token"]).toBe("csrf-abc")
    expect(api.called("GET", `${A}/metrics/summary`)[0].headers["X-CSRF-Token"]).toBeUndefined()  // safe methods need no token
    const stored = JSON.stringify({ ...localStorage, ...sessionStorage })
    expect(stored).not.toContain("right-password")
    expect(stored).not.toContain("csrf-abc")
  })

  it("signs out: the session is revoked on the server and the app returns to User Mode", async () => {
    const { api } = server()
    renderApp("/query")
    const user = userEvent.setup()
    await signIn(user)
    await screen.findAllByText("Admin Mode")
    await user.click(screen.getAllByRole("button", { name: "Sign out" })[0])
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/logout`)).toHaveLength(1)
    expect(api.called("POST", `${A}/auth/logout`)[0].headers["X-CSRF-Token"]).toBe("csrf-abc")
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    expect(screen.getAllByRole("button", { name: /admin login/i }).length).toBeGreaterThan(0)
    expect(screen.queryByText(/administrator session is still active/i)).not.toBeInTheDocument()
  })

  it("returns to User Mode when the server reports the session expired", async () => {
    const state = { session: adminSession as unknown }
    server({ [`GET ${A}/metrics/summary`]: () => { state.session = visitor; return { __status: 401, __error: { code: "session_expired", message: "your administrator session has expired" } } } }, state)
    renderApp("/")
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." }, { timeout: 4000 })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    expect(screen.queryByText("Admin Mode")).not.toBeInTheDocument()
  })

  it("restores Admin Mode after a reload when the server still has the session", async () => {
    server({}, { session: adminSession })
    renderApp("/memories")
    expect((await screen.findAllByText("Admin Mode"))[0]).toBeInTheDocument()
    expect(navNames()).toContain("Memories")
  })

  it("stays in User Mode after a reload if the administrator chose to switch", async () => {
    sessionStorage.setItem("morphe.view-mode.v1", "user")
    server({}, { session: adminSession })
    renderApp("/")
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
  })
})

describe("Admin pages surface permission errors from the API", () => {
  it("shows the server's message when an admin request is refused", async () => {
    mockApi({ [`GET ${A}/memories`]: { __status: 403, __error: { code: "admin_required", message: "administrator privileges are required for this operation" } }, [`GET ${A}/modules`]: { items: [] }, [`GET ${A}/sources`]: { items: [], total: 0 } })
    renderWith(<Memories />, "/memories")
    expect(await screen.findByText("administrator privileges are required for this operation")).toBeInTheDocument()
  })
})
