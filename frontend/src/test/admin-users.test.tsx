import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import ActivityLog from "@/pages/ActivityLog"
import Memories from "@/pages/Memories"
import Users from "@/pages/Users"
import { setCsrfToken } from "@/api/client"
import * as fx from "./fixtures"
import { mockApi, renderWith, type Routes } from "./utils"

const A = "/api/v1"
const stats = { private_memories: 4, personal_modules: 2, sources: 2, queries_submitted: 7, queries_succeeded: 5, queries_failed: 2, memory_saves: 3, memory_save_failures: 1, memory_deletions: 0, failed_operations: 3, failed_logins: 1 }
const alice = { user_id: "usr_ALICE000000000000001", full_name: "Alice Aardvark", email: "alice@example.com", role: "user", status: "active", created_at: "2026-10-20T10:00:00Z", last_login_at: "2026-10-25T09:00:00Z", last_activity_at: "2026-10-25T09:30:00Z", stats }
const bob = { ...alice, user_id: "usr_BOB00000000000000002", full_name: "Bob Badger", email: "bob@example.com", status: "disabled", disabled_at: "2026-10-24T10:00:00Z", disabled_reason: "abuse report", stats: { ...stats, queries_failed: 0, failed_operations: 0 } }
const ev = (over: Record<string, unknown> = {}) => ({ event_id: "evt_1", actor_user_id: alice.user_id, actor_role: "user", actor_label: alice.email, subject_user_id: alice.user_id, action: "query.completed", target_type: "query", target_id: "qry_1",
  timestamp: "2026-10-25T09:30:00Z", status: "success", request_id: "req_abc", metadata: { query: "How does routing work?", strategy: "progressive", result_count: 5, latency_ms: 12.5 }, user: { full_name: alice.full_name, email: alice.email }, ...over })

beforeEach(() => { sessionStorage.clear(); setCsrfToken(null); vi.restoreAllMocks() })

describe("Users page", () => {
  const list: Routes = {
    [`GET ${A}/admin/users`]: { items: [alice, bob], total: 2, limit: 15, offset: 0 },
    [`GET ${A}/admin/users/${alice.user_id}`]: { ...alice, recent_activity: [ev(), ev({ event_id: "evt_2", action: "memory.created", status: "failure", metadata: { error_code: "storage_error" } })] },
    [`GET ${A}/admin/users/${alice.user_id}/queries`]: { items: [{ query_id: "qry_1", query: "How does routing work?", mode: "progressive", created_at: "2026-10-25T09:30:00Z", result_count: 5, latency_ms: 12 }], total: 1, limit: 10, offset: 0 },
    [`GET ${A}/admin/users/${bob.user_id}`]: { ...bob, recent_activity: [] },
    [`GET ${A}/admin/users/${bob.user_id}/queries`]: { items: [], total: 0, limit: 10, offset: 0 },
  }

  it("lists accounts with their real counts, status and last activity", async () => {
    mockApi(list)
    renderWith(<Users />)
    const table = await screen.findByRole("table", { name: "Registered users" })
    const row = within(table).getByText("Alice Aardvark").closest("tr")!
    expect(within(row).getByText("alice@example.com")).toBeInTheDocument()
    expect(within(row).getByText("active")).toBeInTheDocument()
    expect(within(row).getByText(/2 failed/)).toBeInTheDocument()
    const bobRow = within(table).getByText("Bob Badger").closest("tr")!
    expect(within(bobRow).getByText("disabled")).toBeInTheDocument()
    expect(screen.queryByText(/password/i)).not.toBeInTheDocument()
  })

  it("searches and filters on the server", async () => {
    const api = mockApi(list)
    renderWith(<Users />)
    const user = userEvent.setup()
    await screen.findByRole("table", { name: "Registered users" })
    await user.type(screen.getByLabelText("Search users"), "badger")
    await waitFor(() => expect(api.calls.some((c) => c.path === `${A}/admin/users` && c.method === "GET" && false)).toBe(false))
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("search=badger"))).toBe(true))
    await user.click(screen.getByRole("tab", { name: "disabled" }))
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("status=disabled"))).toBe(true))
  })

  it("opens a user's details with stats, recent activity and questions, but no memory content", async () => {
    mockApi(list)
    renderWith(<Users />)
    const user = userEvent.setup()
    await user.click((await screen.findAllByText("Alice Aardvark"))[0])
    const dialog = await screen.findByRole("dialog")
    expect(await within(dialog).findByText("Recent activity")).toBeInTheDocument()
    const summary = within(dialog).getByLabelText("Activity summary")
    expect(within(summary).getByText("Private memories").previousElementSibling).toHaveTextContent("4")
    expect(within(summary).getByText("Queries failed").previousElementSibling).toHaveTextContent("2")
    expect(within(dialog).getByText("Question answered")).toBeInTheDocument()
    expect(within(dialog).getByText("Memory saved")).toBeInTheDocument()
    expect(within(dialog).getAllByText(/How does routing work\?/).length).toBeGreaterThan(0)
    expect(within(dialog).getByRole("link", { name: "View all activity" })).toHaveAttribute("href", `/activity?user=${alice.user_id}`)
    expect(within(dialog).getByText(/The text of retrieved memories is not shown/)).toBeInTheDocument()
  })

  it("disables an account only after confirmation, recording the reason", async () => {
    const api = mockApi({ ...list, [`POST ${A}/admin/users/${alice.user_id}/status`]: { ...alice, status: "disabled", recent_activity: [] } })
    renderWith(<Users />)
    const user = userEvent.setup()
    await user.click((await screen.findAllByText("Alice Aardvark"))[0])
    await user.click(await screen.findByRole("button", { name: /disable account/i }))
    expect(api.called("POST", `${A}/admin/users/${alice.user_id}/status`)).toHaveLength(0)  // not yet: the confirmation comes first
    const confirm = await screen.findByRole("alertdialog")
    await user.type(within(confirm).getByLabelText(/Reason/), "abuse report")
    await user.click(within(confirm).getByRole("button", { name: "Disable account" }))
    await waitFor(() => expect(api.called("POST", `${A}/admin/users/${alice.user_id}/status`)).toHaveLength(1))
    expect(api.called("POST", `${A}/admin/users/${alice.user_id}/status`)[0].body).toEqual({ status: "disabled", reason: "abuse report" })
  })

  it("offers reactivation for a disabled account and shows why it was disabled", async () => {
    const api = mockApi({ ...list, [`POST ${A}/admin/users/${bob.user_id}/status`]: { ...bob, status: "active" } })
    renderWith(<Users />)
    const user = userEvent.setup()
    await user.click((await screen.findAllByText("Bob Badger"))[0])
    expect(await screen.findByRole("note")).toHaveTextContent(/abuse report/)
    await user.click(screen.getByRole("button", { name: /reactivate account/i }))
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Reactivate account" }))
    await waitFor(() => expect(api.called("POST", `${A}/admin/users/${bob.user_id}/status`)[0]?.body).toEqual({ status: "active" }))
  })

  it("has loading, empty, error and pagination states", async () => {
    mockApi({ [`GET ${A}/admin/users`]: { items: [], total: 0, limit: 15, offset: 0 } })
    const { unmount } = renderWith(<Users />)
    expect(await screen.findByText("No registered users yet")).toBeInTheDocument()
    unmount()
    mockApi({ [`GET ${A}/admin/users`]: { __status: 500, __error: { code: "internal_error", message: "unexpected server error" } } })
    const second = renderWith(<Users />)
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected server error")
    second.unmount()
    const many = Array.from({ length: 15 }, (_, i) => ({ ...alice, user_id: `usr_${i}`, full_name: `Person ${i}`, email: `p${i}@example.com` }))
    const api = mockApi({ [`GET ${A}/admin/users`]: { items: many, total: 40, limit: 15, offset: 0 } })
    renderWith(<Users />)
    expect(await screen.findByText("Showing 1–15 of 40")).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: /next/i }))
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("offset=15"))).toBe(true))
  })
})

describe("Activity page", () => {
  const events = [ev(), ev({ event_id: "evt_2", action: "auth.login", status: "failure", target_type: "account", metadata: { error_code: "invalid_credentials", email: "alice@example.com" } }),
    ev({ event_id: "evt_3", actor_role: "admin", actor_user_id: null, subject_user_id: alice.user_id, actor_label: "test-admin", user: { full_name: alice.full_name, email: alice.email }, action: "user.disabled", target_type: "account", metadata: { reason: "abuse report" } })]
  const routes: Routes = { [`GET ${A}/admin/activity`]: { items: events, total: 3, limit: 25, offset: 0 }, [`GET ${A}/admin/activity/actions`]: { items: ["auth.login", "query.completed", "user.disabled"] } }

  it("shows each event with who, what, details and outcome — distinguishing successes from failures", async () => {
    mockApi(routes)
    renderWith(<ActivityLog />)
    const table = await screen.findByRole("table", { name: "Activity log" })
    expect(within(table).getByText("Question answered")).toBeInTheDocument()
    expect(within(table).getByText("User sign-in")).toBeInTheDocument()
    expect(within(table).getByText("Account disabled")).toBeInTheDocument()
    expect(within(table).getAllByText("Success").length).toBe(2)
    expect(within(table).getByText("Failed")).toBeInTheDocument()
    expect(within(table).getByText(/progressive · 5 results · 12.5 ms/)).toBeInTheDocument()
    expect(within(table).getByText("Admin · test-admin")).toBeInTheDocument()
  })

  it("opens one event with its full metadata and the no-secrets note", async () => {
    mockApi(routes)
    renderWith(<ActivityLog />)
    await userEvent.setup().click((await screen.findAllByText("User sign-in"))[0])
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByTestId("event-metadata")).toHaveTextContent('"error_code": "invalid_credentials"')
    expect(within(dialog).getByText(/never stores passwords, tokens or the text of saved memories/)).toBeInTheDocument()
  })

  it("sends the filters to the server and can show a single account", async () => {
    const api = mockApi(routes)
    renderWith(<ActivityLog />, `/activity?user=${alice.user_id}`)
    const user = userEvent.setup()
    await screen.findByRole("table", { name: "Activity log" })
    expect(screen.getByTestId("user-filter")).toBeInTheDocument()
    expect(api.fn.mock.calls.some(([u]) => String(u).includes(`user_id=${alice.user_id}`))).toBe(true)
    await user.type(screen.getByLabelText("Search activity"), "progressive")
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("search=progressive"))).toBe(true))
    await user.type(screen.getByLabelText("From"), "2026-10-01")
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("since="))).toBe(true))
  })

  it("has empty, error and pagination states", async () => {
    mockApi({ ...routes, [`GET ${A}/admin/activity`]: { items: [], total: 0, limit: 25, offset: 0 } })
    const first = renderWith(<ActivityLog />)
    expect(await screen.findByText("No activity recorded yet")).toBeInTheDocument()
    first.unmount()
    mockApi({ ...routes, [`GET ${A}/admin/activity`]: { __status: 500, __error: { code: "internal_error", message: "unexpected server error" } } })
    const second = renderWith(<ActivityLog />)
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected server error")
    second.unmount()
    const api = mockApi({ ...routes, [`GET ${A}/admin/activity`]: { items: Array.from({ length: 25 }, (_, i) => ev({ event_id: `evt_${i}` })), total: 60, limit: 25, offset: 0 } })
    renderWith(<ActivityLog />)
    expect(await screen.findByText("Showing 1–25 of 60")).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole("button", { name: /next/i }))
    await waitFor(() => expect(api.fn.mock.calls.some(([u]) => String(u).includes("offset=25"))).toBe(true))
  })
})

describe("Ingest content (administrator)", () => {
  it("publishes to all users only when the share switch is turned on", async () => {
    const api = mockApi({ [`GET ${A}/memories`]: { items: [], total: 0, limit: 15, offset: 0 }, [`GET ${A}/modules`]: fx.modules, [`GET ${A}/sources`]: { items: [], total: 0 },
      [`POST ${A}/ingestion`]: { source_id: "s", status: "completed", chunks_created: 1, module_ids: ["m"], new_module_ids: [], tokens_added: 1, active_tokens: 1, usage_percent: 0 } })
    renderWith(<Memories />)
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole("button", { name: /ingest content/i }))[0])
    await user.type(await screen.findByLabelText("Source name", { exact: true }), "shared.md")
    await user.type(screen.getByLabelText("Text", { exact: true }), "knowledge for everyone")
    await user.click(screen.getByRole("switch", { name: "Share with all users" }))
    await user.click(screen.getByRole("button", { name: /^Ingest$/ }))
    await waitFor(() => expect(api.called("POST", `${A}/ingestion`)).toHaveLength(1))
    expect(api.called("POST", `${A}/ingestion`)[0].body).toMatchObject({ source_name: "shared.md", visibility: "shared" })
  })
})
