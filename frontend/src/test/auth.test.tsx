import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { App } from "@/App"
import GraphExplorer from "@/pages/GraphExplorer"
import { setCsrfToken } from "@/api/client"
import { AuthProvider } from "@/lib/auth"
import { setRoute } from "@/lib/route-store"
import { ThemeProvider } from "@/lib/theme"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import * as fx from "./fixtures"
import { mockApi, renderWith, testAuth, type Routes } from "./utils"

const A = "/api/v1"
const base = { admin_configured: true, registration_enabled: true }
const visitor = { authenticated: false, role: null, user: null, username: null, expires_at: null, csrf_token: null, ...base }
const account = { user_id: "usr_TEST", full_name: "Alice Tester", email: "alice@example.com", role: "user", status: "active", created_at: "2026-10-25T10:00:00Z", last_login_at: null, last_activity_at: null }
const userSession = { authenticated: true, role: "user", user: account, username: null, expires_at: Math.floor(Date.now() / 1000) + 3600, csrf_token: "csrf-user", ...base }
const adminSession = { authenticated: true, role: "admin", user: null, username: "test-admin", expires_at: Math.floor(Date.now() / 1000) + 3600, csrf_token: "csrf-admin", ...base }
const ADMIN_NAV = ["Overview", "Query Playground", "Memories", "Graph Explorer", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks", "Users", "Activity"]
const ADMIN_ONLY = ["Overview", "Memories", "Compression", "Adaptation", "Metrics", "Versions", "Benchmarks", "Users", "Activity"]
const PASSWORD = "Correct-Horse-9"

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
  [`GET ${A}/admin/users`]: { items: [], total: 0, limit: 15, offset: 0 }, [`GET ${A}/admin/activity`]: { items: [], total: 0, limit: 25, offset: 0 },
  [`GET ${A}/admin/activity/actions`]: { items: [] },
}
const memoryScope = { personal_records: 2, shared_records: 3, empty: false }
const personalResponse = { ...fx.queryResponse, architecture_version: null, mode: "progressive", fallback_used: false, memory_scope: memoryScope,
  results: [{ ...fx.queryResponse.results[0], scope: "personal" }, { ...fx.queryResponse.results[0], memory_id: "mem_2", text: "Shared note about PostgreSQL pooling.", scope: "shared", source_name: "pg.md" }] }
const userPages: Routes = {
  "GET /health": { status: "ok", ready: true }, [`GET ${A}/me/queries`]: { items: [] }, [`GET ${A}/me/summary`]: { memories: 2, modules: 1, sources: 1, tokens: 40, shared_records: 3 },
  [`GET ${A}/me/memories`]: { items: [], total: 0 },
}

/** A tiny server: the session endpoint reports whatever `state.session` currently is. */
function server(extra: Routes = {}, state = { session: visitor as unknown }) {
  const api = mockApi({
    ...userPages, ...adminPages,
    [`GET ${A}/auth/me`]: () => state.session,
    [`POST ${A}/auth/login`]: ({ body }: { body: unknown }) => {
      const b = body as { email: string; password: string }
      if (b.password !== PASSWORD) return { __status: 401, __error: { code: "invalid_credentials", message: "invalid email or password" } }
      state.session = userSession
      return userSession
    },
    [`POST ${A}/auth/register`]: ({ body }: { body: unknown }) => {
      if ((body as { email: string }).email === "taken@example.com") return { __status: 409, __error: { code: "email_taken", message: "an account with this email already exists" } }
      state.session = userSession
      return userSession
    },
    [`POST ${A}/auth/admin/login`]: ({ body }: { body: unknown }) => {
      if ((body as { password: string }).password !== "right-admin-password") return { __status: 401, __error: { code: "invalid_credentials", message: "invalid username or password" } }
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

describe("a visitor who is not signed in", () => {
  it("sees only the authentication page and no product navigation", async () => {
    const { api } = server()
    renderApp("/")
    expect(await screen.findByRole("tab", { name: /sign in/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /create account/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /administrator/i })).toBeInTheDocument()
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument()
    for (const label of ["Query Playground", "Graph Explorer", ...ADMIN_ONLY]) expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument()
    expect(api.calls.every((c) => c.path === `${A}/auth/me`)).toBe(true)  // nothing but the session check was requested
  })

  it("cannot reach any page by URL", async () => {
    const { api } = server()
    for (const route of ["/memories", "/users", "/query", "/graph"]) {
      const { unmount } = renderApp(route)
      expect(await screen.findByRole("tab", { name: /sign in/i })).toBeInTheDocument()
      unmount()
    }
    expect(api.calls.filter((c) => !c.path.startsWith(`${A}/auth`))).toEqual([])
  })

  it("reports a disabled account on the sign-in page", async () => {
    server({}, { session: { ...visitor, account_disabled: true } })
    renderApp("/query")
    expect(await screen.findByText(/this account has been disabled/i)).toBeInTheDocument()
  })
})

describe("creating an account", () => {
  async function openRegister() {
    const user = userEvent.setup()
    await user.click(await screen.findByRole("tab", { name: /create account/i }))
    return user
  }
  const fill = async (user: ReturnType<typeof userEvent.setup>, v: Partial<Record<"name" | "email" | "pw" | "confirm", string>> = {}) => {
    await user.type(screen.getByLabelText("Full name"), v.name ?? "Alice Tester")
    await user.type(screen.getByLabelText("Email address"), v.email ?? "alice@example.com")
    await user.type(screen.getByLabelText("Password", { exact: true }), v.pw ?? PASSWORD)
    await user.type(screen.getByLabelText("Confirm password"), v.confirm ?? v.pw ?? PASSWORD)
  }

  it("validates every field before sending anything", async () => {
    const { api } = server()
    renderApp("/")
    const user = await openRegister()
    await user.click(screen.getByRole("button", { name: "Create account" }))
    expect(screen.getByText("Enter your full name.")).toBeInTheDocument()
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument()
    expect(screen.getAllByText(/at least 10 characters/).length).toBeGreaterThan(0)
    await user.type(screen.getByLabelText("Password", { exact: true }), "alllettersonly")
    await user.type(screen.getByLabelText("Confirm password"), "different")
    expect(screen.getByText(/letters plus at least one digit or symbol/)).toBeInTheDocument()
    expect(screen.getByText("The passwords do not match.")).toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/register`)).toHaveLength(0)
  })

  it("toggles password visibility on both password fields", async () => {
    server()
    renderApp("/")
    const user = await openRegister()
    const pw = screen.getByLabelText("Password", { exact: true })
    expect(pw).toHaveAttribute("type", "password")
    await user.click(screen.getByRole("button", { name: "Show password" }))
    expect(pw).toHaveAttribute("type", "text")
    const confirm = screen.getByLabelText("Confirm password")
    await user.click(screen.getByRole("button", { name: "Show confirm password" }))
    expect(confirm).toHaveAttribute("type", "text")
  })

  it("registers a regular account (no role is ever sent) and opens the user workspace", async () => {
    const { api } = server()
    renderApp("/")
    const user = await openRegister()
    await fill(user)
    await user.click(screen.getByRole("button", { name: "Create account" }))
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    const sent = api.called("POST", `${A}/auth/register`)[0].body as Record<string, unknown>
    expect(Object.keys(sent).sort()).toEqual(["confirm_password", "email", "full_name", "password"])
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    expect(screen.getAllByTestId("account-identity")[0]).toHaveTextContent("Alice Tester")
    expect(screen.getAllByTestId("account-identity")[0]).toHaveTextContent("alice@example.com")
  })

  it("shows the server's message when the email is already registered", async () => {
    server()
    renderApp("/")
    const user = await openRegister()
    await fill(user, { email: "taken@example.com" })
    await user.click(screen.getByRole("button", { name: "Create account" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("an account with this email already exists")
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument()
  })

  it("shows a busy state while the account is being created, and a helpful message when the network fails", async () => {
    let release: (r: Response) => void = () => {}
    const real = mockApi({ ...userPages, [`GET ${A}/auth/me`]: visitor })
    const inner = real.fn.getMockImplementation()!
    real.fn.mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/auth/register") ? new Promise<Response>((r) => { release = r }) : inner(input, init))
    renderApp("/")
    const user = await openRegister()
    await fill(user)
    await user.click(screen.getByRole("button", { name: "Create account" }))
    const busy = await screen.findByRole("button", { name: "Creating account…" })
    expect(busy).toBeDisabled()
    release(new Response("{}", { status: 503 }))
    expect(await screen.findByRole("alert")).toBeInTheDocument()
    real.fn.mockImplementation(() => Promise.reject(new TypeError("Failed to fetch")))
    await user.click(screen.getByRole("button", { name: "Create account" }))
    expect(await screen.findByText(/Cannot reach the MORPHE backend/)).toBeInTheDocument()
  })

  it("links back to sign-in, and is unavailable when registration is switched off", async () => {
    server({}, { session: { ...visitor, registration_enabled: false } })
    renderApp("/")
    expect(await screen.findByRole("tab", { name: /create account/i })).toBeDisabled()
  })
})

describe("user sign-in", () => {
  const signIn = async (user: ReturnType<typeof userEvent.setup>, pw = PASSWORD) => {
    await user.type(await screen.findByLabelText("Email address"), "alice@example.com")
    await user.type(screen.getByLabelText("Password"), pw)
    await user.click(screen.getByRole("button", { name: "Sign in" }))
  }

  it("validates, rejects a wrong password and clears it", async () => {
    const { api } = server()
    renderApp("/")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: "Sign in" }))
    expect(screen.getByText("Enter a valid email address.")).toBeInTheDocument()
    expect(screen.getByText("Enter your password.")).toBeInTheDocument()
    await signIn(user, "wrong-password-1")
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid email or password")
    expect(screen.getByLabelText("Password")).toHaveValue("")
    expect(api.called("POST", `${A}/auth/login`)).toHaveLength(1)
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument()
  })

  it("signs in, lands on the Query Playground and shows exactly two navigation entries", async () => {
    const { api } = server()
    renderApp("/")
    await signIn(userEvent.setup())
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
    for (const label of ADMIN_ONLY) expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest content/i })).not.toBeInTheDocument()
    expect(screen.getAllByTestId("mode-indicator")[0]).toHaveTextContent("User Mode")
    expect(api.called("POST", `${A}/auth/login`)[0].body).toEqual({ email: "alice@example.com", password: PASSWORD })
    const stored = JSON.stringify({ ...localStorage, ...sessionStorage })
    expect(stored).not.toContain(PASSWORD)
    expect(stored).not.toContain("csrf-user")
  })

  it("keeps administrator pages unreachable and never requests administrator data", async () => {
    const { api } = server({}, { session: userSession })
    renderApp("/users")
    expect(await screen.findByRole("heading", { name: "Ask your memory anything." })).toBeInTheDocument()
    await waitFor(() => expect(api.called("GET", `${A}/auth/me`).length).toBeGreaterThan(0))
    const forbidden = api.calls.filter((c) => !c.path.startsWith(`${A}/auth`) && !c.path.startsWith(`${A}/me`) && c.path !== "/health")
    expect(forbidden).toEqual([])
  })

  it("restores the session after a page refresh", async () => {
    server({}, { session: userSession })
    renderApp("/graph")
    expect(await screen.findByRole("heading", { name: "Graph Explorer" })).toBeInTheDocument()
    expect(navNames()).toEqual(["Query Playground", "Graph Explorer"])
  })

  it("signs out: the server session is revoked (with the CSRF token) and the sign-in page returns", async () => {
    const { api } = server({}, { session: userSession })
    renderApp("/query")
    const user = userEvent.setup()
    await screen.findByRole("heading", { name: "Ask your memory anything." })
    await user.click(screen.getAllByRole("button", { name: /log out/i })[0])
    expect(await screen.findByRole("tab", { name: /sign in/i })).toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/logout`)).toHaveLength(1)
    expect(api.called("POST", `${A}/auth/logout`)[0].headers["X-CSRF-Token"]).toBe("csrf-user")
  })

  it("returns to the sign-in page when the server reports the session ended", async () => {
    const state = { session: userSession as unknown }
    server({ [`GET ${A}/me/summary`]: () => { state.session = visitor; return { __status: 401, __error: { code: "session_expired", message: "your session has expired; sign in again" } } } }, state)
    renderApp("/query")
    expect(await screen.findByRole("tab", { name: /sign in/i }, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument()
  })
})

describe("administrator sign-in", () => {
  const adminLogin = async (user: ReturnType<typeof userEvent.setup>, pw = "right-admin-password") => {
    await user.click(await screen.findByRole("tab", { name: /administrator/i }))
    await user.type(await screen.findByLabelText("Username"), "test-admin")
    await user.type(screen.getByLabelText("Password"), pw)
    await user.click(screen.getByRole("button", { name: "Sign in as administrator" }))
  }

  it("shows the sign-in form with its supporting text and validates empty fields", async () => {
    server()
    renderApp("/")
    const user = userEvent.setup()
    await user.click(await screen.findByRole("tab", { name: /administrator/i }))
    expect(screen.getByText(/memory management, evaluation, and architecture controls/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Sign in as administrator" }))
    expect(screen.getByText("Enter your username.")).toBeInTheDocument()
    expect(screen.getByText("Enter your password.")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Show password" }))
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "text")
  })

  it("rejects a wrong password and stays signed out", async () => {
    const { api } = server()
    renderApp("/")
    await adminLogin(userEvent.setup(), "wrong")
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid username or password")
    expect(screen.getByLabelText("Password")).toHaveValue("")
    expect(api.called("POST", `${A}/auth/admin/login`)).toHaveLength(1)
    expect(screen.queryByText("Admin Mode")).not.toBeInTheDocument()
  })

  it("explains when no administrator has been configured on the server", async () => {
    server({}, { session: { ...visitor, admin_configured: false } })
    renderApp("/")
    await userEvent.setup().click(await screen.findByRole("tab", { name: /administrator/i }))
    expect(await screen.findByText(/has not been set up on this server/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sign in as administrator" })).toBeDisabled()
  })

  it("opens the full dashboard including Users and Activity, with no way to switch into a user view", async () => {
    const { api } = server()
    renderApp("/")
    await adminLogin(userEvent.setup())
    expect((await screen.findAllByText("Admin Mode"))[0]).toBeInTheDocument()
    await waitFor(() => expect(navNames()).toEqual(ADMIN_NAV))
    await waitFor(() => expect(api.called("GET", `${A}/metrics/summary`).length).toBeGreaterThan(0))
    expect(screen.getAllByRole("button", { name: /ingest content/i }).length).toBeGreaterThan(0)
    expect(screen.queryByRole("button", { name: /switch to user mode/i })).not.toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/admin/login`)[0].body).toEqual({ username: "test-admin", password: "right-admin-password" })
  })

  it("sends the CSRF token on state-changing requests and signs out through the server", async () => {
    const { api } = server({ [`POST ${A}/ingestion`]: { source_id: "s", status: "completed", chunks_created: 1, module_ids: ["m"], new_module_ids: [], tokens_added: 1, active_tokens: 1, usage_percent: 0 } }, { session: adminSession })
    renderApp("/")
    const user = userEvent.setup()
    await screen.findAllByText("Admin Mode")
    await user.click(screen.getAllByRole("button", { name: /ingest content/i })[0])
    await user.type(await screen.findByLabelText("Source name", { exact: true }), "n.md")
    await user.type(screen.getByLabelText("Text", { exact: true }), "some text")
    await user.click(screen.getByRole("button", { name: /^Ingest$/ }))
    await waitFor(() => expect(api.called("POST", `${A}/ingestion`)).toHaveLength(1))
    expect(api.called("POST", `${A}/ingestion`)[0].headers["X-CSRF-Token"]).toBe("csrf-admin")
    expect(api.called("POST", `${A}/ingestion`)[0].body).not.toHaveProperty("visibility")  // administrator-only unless explicitly shared
    await user.click(screen.getAllByRole("button", { name: "Sign out" })[0])
    expect(await screen.findByRole("tab", { name: /sign in/i })).toBeInTheDocument()
    expect(api.called("POST", `${A}/auth/logout`)[0].headers["X-CSRF-Token"]).toBe("csrf-admin")
  })

  it("restores the administrator session after a reload", async () => {
    server({}, { session: adminSession })
    renderApp("/users")
    expect((await screen.findAllByText("Admin Mode"))[0]).toBeInTheDocument()
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument()
  })
})

describe("the user's Query Playground", () => {
  it("asks inside the user's own scope, labels personal and shared results, and offers no administrator tools", async () => {
    const { api } = server({ [`POST ${A}/me/query`]: personalResponse }, { session: userSession })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "How does Django route URLs?")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    const list = await screen.findByRole("list", { name: "Retrieved memories" })
    expect(within(list).getByText("Django maps URL patterns to views.")).toBeInTheDocument()
    expect(within(list).getAllByTestId("result-scope").map((e) => e.textContent)).toEqual(["Mine", "Shared"])
    expect(screen.getByTestId("route-explanation")).toHaveTextContent(/matched to Django/)
    expect(api.called("POST", `${A}/me/query`)[0].body).toMatchObject({ query: "How does Django route URLs?", top_k: 5, mode: "progressive" })
    expect(api.called("POST", `${A}/query`)).toHaveLength(0)  // the administrator endpoint is never used
    expect(screen.queryByText(/compare all four/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Debug trace")).not.toBeInTheDocument()
  })

  it("shows a helpful empty state when nothing has been saved yet, and the privacy statement", async () => {
    server({ [`GET ${A}/me/summary`]: { memories: 0, modules: 0, sources: 0, tokens: 0, shared_records: 0 } }, { session: userSession })
    renderApp("/query")
    expect(await screen.findByText("Your memory is empty")).toBeInTheDocument()
    expect(screen.getByTestId("privacy-line")).toHaveTextContent(/private by default/)
    expect(screen.getByTestId("privacy-line")).toHaveTextContent(/Administrators can see operational activity/)
  })

  it("explains an empty result for an account with no memories", async () => {
    server({ [`POST ${A}/me/query`]: { ...personalResponse, results: [], memory_scope: { personal_records: 0, shared_records: 0, empty: true } } }, { session: userSession })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "anything")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    expect(await screen.findByText("No relevant memories were found")).toBeInTheDocument()
    expect(screen.getByText(/You have not saved any memories yet/)).toBeInTheDocument()
  })

  it("shows a retryable error when the request fails and a weak-match hint for low scores", async () => {
    let n = 0
    server({ [`POST ${A}/me/query`]: () => { n += 1; return n === 1 ? { __status: 500, __error: { code: "query_failed", message: "the question could not be answered right now; please try again" } } : { ...personalResponse, results: [{ ...personalResponse.results[0], score: 0.1 }] } } }, { session: userSession })
    renderApp("/query")
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText("Your question"), "hello")
    await user.click(screen.getByRole("button", { name: "Ask" }))
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("could not be answered")
    await user.click(within(alert).getByRole("button", { name: /retry/i }))
    expect(await screen.findByRole("note")).toHaveTextContent(/none is a strong match/)
  })

  it("lists the user's own recent questions and reopens one", async () => {
    const item = { query_id: "qry_1", query: "my earlier question", mode: "graph", top_k: 5, created_at: "2026-10-25T10:00:00Z", result_count: 1, latency_ms: 12 }
    const { api } = server({ [`GET ${A}/me/queries`]: { items: [item] }, [`GET ${A}/me/queries/qry_1`]: { ...personalResponse, query: "my earlier question", created_at: item.created_at } }, { session: userSession })
    renderApp("/query")
    await userEvent.setup().click(await screen.findByRole("button", { name: /my earlier question/ }))
    expect(await screen.findByRole("list", { name: "Retrieved memories" })).toBeInTheDocument()
    expect(api.called("GET", `${A}/me/queries/qry_1`)).toHaveLength(1)
  })
})

describe("Save to My Memory", () => {
  const openComposer = async () => {
    const user = userEvent.setup()
    await user.click(await screen.findByRole("button", { name: /save to my memory/i }))
    return user
  }

  it("saves explicitly, sends no owner, and reports success", async () => {
    const { api } = server({ [`POST ${A}/me/memories`]: { source_id: "usrc_1", status: "completed", chunks_created: 2, memory_ids: ["a", "b"], module_ids: ["m"], new_module_ids: ["m"], tokens_added: 30, message: "Saved" } }, { session: userSession })
    renderApp("/query")
    const user = await openComposer()
    const save = screen.getByRole("button", { name: "Save to My Memory", hidden: false })
    expect(save).toBeDisabled()  // nothing to save yet
    await user.type(screen.getByLabelText("What should MORPHE remember?"), "Project Atlas ships in March.")
    await user.type(screen.getByLabelText(/Title or source label/), "Atlas")
    await user.type(screen.getByLabelText(/^Topic/), "Projects")
    await user.click(screen.getByRole("button", { name: "Save to My Memory" }))
    await waitFor(() => expect(api.called("POST", `${A}/me/memories`)).toHaveLength(1))
    const call = api.called("POST", `${A}/me/memories`)[0]
    expect(call.body).toEqual({ text: "Project Atlas ships in March.", title: "Atlas", topic: "Projects" })
    expect(call.headers["X-CSRF-Token"]).toBe("csrf-user")
    expect(await screen.findByText(/Saved to your private memory/)).toBeInTheDocument()
    expect(screen.getByLabelText("What should MORPHE remember?")).toHaveValue("")
  })

  it("shows the server's error and keeps the text", async () => {
    server({ [`POST ${A}/me/memories`]: { __status: 500, __error: { code: "save_failed", message: "your memory could not be saved and nothing was stored; please try again" } } }, { session: userSession })
    renderApp("/query")
    const user = await openComposer()
    await user.type(screen.getByLabelText("What should MORPHE remember?"), "keep me")
    await user.click(screen.getByRole("button", { name: "Save to My Memory" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("nothing was stored")
    expect(screen.getByLabelText("What should MORPHE remember?")).toHaveValue("keep me")
  })

  it("lists recently saved memories and deletes one", async () => {
    const item = { memory_id: "umem_1", module_id: "umod_1", module_name: "Projects", source_id: "usrc_1", source_name: "Atlas", text_preview: "Project Atlas ships in March.", token_count: 8, created_at: "2026-10-25T10:00:00Z", status: "active" }
    const { api } = server({ [`GET ${A}/me/memories`]: { items: [item], total: 1 }, [`DELETE ${A}/me/memories/umem_1`]: { memory_id: "umem_1" } }, { session: userSession })
    renderApp("/query")
    const list = await screen.findByRole("list", { name: "Recently saved memories" })
    expect(within(list).getByText("Project Atlas ships in March.")).toBeInTheDocument()
    await userEvent.setup().click(within(list).getByRole("button", { name: /delete memory/i }))
    await waitFor(() => expect(api.called("DELETE", `${A}/me/memories/umem_1`)).toHaveLength(1))
  })
})

describe("the user's Graph Explorer", () => {
  const mine = { ...fx.graph, architecture_version: null, nodes: fx.graph.nodes.map((n, i) => ({ ...n, data: { ...n.data, lastChangeOp: null, lastChangeAt: null, utilization: null, scope: i === 0 ? "shared" : "personal" } })) }
  const rows: Record<string, unknown>[] = (fx.modules.items as unknown as Record<string, unknown>[]).map((m, i) => ({ ...Object.fromEntries(["module_id", "name", "parent_id", "children", "record_count", "is_container", "keywords", "description", "shortcut_edges"].map((k) => [k, m[k]])), scope: i === 0 ? "shared" : "personal" }))
  const routes: Routes = {
    [`GET ${A}/me/graph`]: mine, [`GET ${A}/me/modules`]: { items: rows },
    [`GET ${A}/me/modules/mod_django`]: { ...rows.find((r) => r.module_id === "mod_django"), parent: rows[0], neighbors: fx.moduleDetail.neighbors },
  }

  it("uses only the user's own /me endpoints, is read-only, and labels where a module comes from", async () => {
    const api = mockApi(routes)
    renderWith(<GraphExplorer />, "/graph", testAuth("user"))
    expect(await screen.findByText("read-only")).toBeInTheDocument()
    expect(screen.queryByText(/split capacity threshold/i)).not.toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole("tab", { name: "Module list" }))
    await user.click((await screen.findAllByRole("button", { name: /^Django/ }))[0])
    const panel = await screen.findByTestId("module-panel")
    expect(within(panel).getByTestId("module-scope")).toBeInTheDocument()
    for (const hidden of [/^Capacity/, /Related memories/, /Adaptation history/, /Retrieval activity/, /Open all in Memory Explorer/]) expect(within(panel).queryByText(hidden)).not.toBeInTheDocument()
    const paths = [...new Set(api.calls.map((c) => c.path))]
    expect(paths.every((p) => p.startsWith(`${A}/me/`))).toBe(true)
    expect(api.calls.every((c) => c.method === "GET")).toBe(true)
  })

  it("explains an empty personal graph and has no Ingest button", async () => {
    mockApi({ [`GET ${A}/me/graph`]: { nodes: [], edges: [], architecture_version: null, counts: { nodes: 0, edges: 0 } }, [`GET ${A}/me/modules`]: { items: [] } })
    renderWith(<GraphExplorer />, "/graph", testAuth("user"))
    expect(await screen.findByText("Your graph is empty")).toBeInTheDocument()
    expect(screen.getByText(/fills in as you save information/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /ingest content/i })).not.toBeInTheDocument()
  })
})
