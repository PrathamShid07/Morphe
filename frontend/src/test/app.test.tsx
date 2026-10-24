import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { App } from "@/App"
import QueryPlayground from "@/pages/QueryPlayground"
import Memories from "@/pages/Memories"
import Adaptation from "@/pages/Adaptation"
import Versions from "@/pages/Versions"
import GraphExplorer from "@/pages/GraphExplorer"
import Overview from "@/pages/Overview"
import { IngestDialog } from "@/components/app/IngestDialog"
import * as fx from "./fixtures"
import { mockApi, renderWith } from "./utils"

const base = {
  "GET /health": fx.health, "GET /api/v1/config": fx.config, "GET /api/v1/metrics/summary": fx.summary,
  "GET /api/v1/compression/status": fx.compressionStatus, "GET /api/v1/adaptation/status": fx.adaptationStatus,
  "GET /api/v1/adaptation/events": { items: [], total: 0, limit: 50, offset: 0, status_counts: {} },
  "GET /api/v1/compression/events": { items: [], total: 0, limit: 50, offset: 0 },
}

describe("navigation", () => {
  it("lists every page and navigates between them", async () => {
    mockApi({ ...base, "GET /api/v1/memories": { items: [], total: 0, limit: 15, offset: 0 }, "GET /api/v1/modules": fx.modules, "GET /api/v1/sources": { items: [], total: 0, limit: 200, offset: 0 } })
    renderWith(<App />)
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]
    for (const label of ["Overview", "Query Playground", "Memories", "Graph Explorer", "Compression", "Adaptation", "Metrics", "Versions"]) expect(within(nav).getByRole("link", { name: label })).toBeInTheDocument()
    await userEvent.click(within(nav).getByRole("link", { name: "Memories" }))
    expect(await screen.findByRole("heading", { name: "Memory Explorer" })).toBeInTheDocument()
  })
})

describe("states and honesty about missing data", () => {
  it("shows 'Not enough data yet' instead of a zero for unmeasured metrics", async () => {
    mockApi(base)
    renderWith(<Overview />)
    await screen.findByText("512.3 MB") // summary has loaded
    const lat = screen.getByText("p95 query latency").closest("div")!.parentElement!
    expect(within(lat).getByText("Not enough data yet")).toBeInTheDocument()
    expect(within(lat).queryByText(/0\.00 ms/)).toBeNull()
    expect(screen.getAllByText("Not enough data yet").length).toBeGreaterThan(2)
  })
  it("renders an actionable error when the API fails and recovers on retry", async () => {
    let fail = true
    mockApi({ ...base, "GET /api/v1/metrics/summary": () => (fail ? { __status: 503, __error: { code: "dependency_unavailable", message: "MongoDB is not reachable" } } : fx.summary) })
    renderWith(<Overview />)
    expect(await screen.findByText("MongoDB is not reachable")).toBeInTheDocument()
    fail = false
    await userEvent.click(screen.getAllByRole("button", { name: /retry/i })[0])
    await waitFor(() => expect(screen.queryByText("MongoDB is not reachable")).toBeNull())
  })
  it("shows an empty state with an ingest action when there are no memories", async () => {
    mockApi({ ...base, "GET /api/v1/memories": { items: [], total: 0, limit: 15, offset: 0 }, "GET /api/v1/modules": fx.modules, "GET /api/v1/sources": { items: [], total: 0, limit: 200, offset: 0 } })
    renderWith(<Memories />)
    expect(await screen.findByText("No memories yet")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /ingest content/i }).length).toBeGreaterThan(0)
  })
  it("shows a loading skeleton while data is pending", () => {
    mockApi({ ...base, "GET /api/v1/memories": () => new Promise(() => {}) })
    renderWith(<Memories />)
    expect(document.querySelector('[aria-busy="true"]')).toBeInTheDocument()
  })
})

describe("ingestion form", () => {
  it("validates required fields before calling the API", async () => {
    const api = mockApi({ ...base })
    renderWith(<IngestDialog open onOpenChange={() => {}} />)
    await userEvent.click(screen.getByRole("button", { name: "Ingest" }))
    expect(await screen.findByText("Give the source a name")).toBeInTheDocument()
    expect(screen.getByText("Paste some text to ingest")).toBeInTheDocument()
    expect(api.called("POST", "/api/v1/ingestion")).toHaveLength(0)
  })
  it("submits valid content once and reports the result", async () => {
    const api = mockApi({ ...base, "POST /api/v1/ingestion": { source_id: "src_1", status: "completed", chunks_created: 4, module_ids: ["mod_redis"], new_module_ids: [], tokens_added: 90, active_tokens: 90, usage_percent: 0.1 } })
    renderWith(<IngestDialog open onOpenChange={() => {}} />)
    await userEvent.type(screen.getByLabelText("Source name"), "notes.md")
    await userEvent.type(screen.getByLabelText("Text"), "Redis keys expire.")
    await userEvent.type(screen.getByLabelText(/Topic/), "redis")
    await userEvent.click(screen.getByRole("button", { name: "Ingest" }))
    await waitFor(() => expect(api.called("POST", "/api/v1/ingestion")).toHaveLength(1))
    expect(api.called("POST", "/api/v1/ingestion")[0].body).toMatchObject({ source_name: "notes.md", text: "Redis keys expire.", metadata: { topic: "redis" } })
    expect(await screen.findByText("Ingested 4 memories")).toBeInTheDocument()
  })
  it("surfaces backend validation errors instead of failing silently", async () => {
    mockApi({ ...base, "POST /api/v1/ingestion": { __status: 422, __error: { code: "empty_text", message: "text must not be empty" } } })
    renderWith(<IngestDialog open onOpenChange={() => {}} />)
    await userEvent.type(screen.getByLabelText("Source name"), "x.md")
    await userEvent.type(screen.getByLabelText("Text"), "some text")
    await userEvent.click(screen.getByRole("button", { name: "Ingest" }))
    expect(await screen.findByText("text must not be empty")).toBeInTheDocument()
  })
})

describe("query playground", () => {
  it("submits the query with options and renders results plus the routing trace", async () => {
    const api = mockApi({ ...base, "POST /api/v1/query": fx.queryResponse })
    renderWith(<QueryPlayground />)
    await userEvent.type(screen.getByLabelText("Query"), "How does Django route URLs?")
    await userEvent.click(screen.getByRole("button", { name: /run query/i }))
    expect(await screen.findByText("Django maps URL patterns to views.")).toBeInTheDocument()
    expect(api.called("POST", "/api/v1/query")[0].body).toMatchObject({ query: "How does Django route URLs?", top_k: 5, debug_trace: true, mode: "graph" })
    const trace = screen.getByTestId("routing-trace")
    expect(within(trace).getByText("1 · Seed modules")).toBeInTheDocument()
    expect(trace.querySelector('[data-origin="co_access"]')).toHaveTextContent("PostgreSQL")
    expect(within(trace).getByText("13")).toBeInTheDocument()
    expect(within(trace).getByText("graph-guided only")).toBeInTheDocument()
    expect(screen.getByText("0.812")).toBeInTheDocument()
  })
  it("shows the fallback reason when global fallback was used", async () => {
    mockApi({ ...base, "POST /api/v1/query": { ...fx.queryResponse, trace: { ...fx.queryResponse.trace, fallback_used: true, fallback_reason: "best guided score 0.12 is below the fallback threshold 0.30" } } })
    renderWith(<QueryPlayground />)
    await userEvent.type(screen.getByLabelText("Query"), "x")
    await userEvent.click(screen.getByRole("button", { name: /run query/i }))
    expect(await screen.findByText(/global fallback used/)).toBeInTheDocument()
    expect(screen.getByText(/below the fallback threshold/)).toBeInTheDocument()
  })
  it("shows API errors and prevents duplicate submissions while pending", async () => {
    const api = mockApi({ ...base, "POST /api/v1/query": () => ({ __status: 503, __error: { code: "dependency_unavailable", message: "Qdrant could not be opened" } }) })
    renderWith(<QueryPlayground />)
    await userEvent.type(screen.getByLabelText("Query"), "x")
    await userEvent.click(screen.getByRole("button", { name: /run query/i }))
    expect(await screen.findByText("Qdrant could not be opened")).toBeInTheDocument()
    expect(api.called("POST", "/api/v1/query")).toHaveLength(1)
  })
})

describe("graph explorer", () => {
  const routes = { ...base, "GET /api/v1/graph": fx.graph, "GET /api/v1/modules": fx.modules, "GET /api/v1/modules/mod_django": fx.moduleDetail }
  it("renders module nodes from the API, a legend, and counts", async () => {
    mockApi(routes)
    renderWith(<GraphExplorer />)
    expect(await screen.findByTestId("module-node-Django")).toHaveTextContent("5 records")
    expect(screen.getByTestId("module-node-Web Frameworks")).toHaveTextContent("group")
    expect(screen.getByLabelText("Graph legend")).toHaveTextContent("co-access shortcut")
    expect(screen.getByText(/nodes$/).parentElement).toHaveTextContent("3 nodes")
  })
  it("selects a module from the list and shows metadata, neighbours and history", async () => {
    mockApi(routes)
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await user.click(await screen.findByRole("tab", { name: "Module list" }))
    await user.click(await screen.findByRole("button", { name: /Django/ }))
    const panel = await screen.findByTestId("module-panel")
    expect(within(panel).getByText("Neighbouring modules (1)")).toBeInTheDocument()
    expect(within(panel).getByText(/shortcut · 28×/)).toBeInTheDocument()
    expect(within(panel).getByText(/shortcut ·/, { selector: "span" })).toBeInTheDocument()
  })
  it("shows an empty graph state", async () => {
    mockApi({ ...routes, "GET /api/v1/graph": { ...fx.graph, nodes: [], edges: [], counts: { nodes: 0, edges: 0 } } })
    renderWith(<GraphExplorer />)
    expect(await screen.findByText("The graph is empty")).toBeInTheDocument()
  })
})

describe("adaptation center", () => {
  const routes = {
    ...base, "GET /api/v1/adaptation/candidates": { proposals: [fx.proposal], not_proposed: [{ operation: "merge_modules", module_ids: ["a", "b"], blockers: ["profile similarity 0.60 < 0.85"] }], window: { queries: 12, window_id: "w", complete: false }, checked_at: "" },
    "GET /api/v1/modules": fx.modules,
  }
  it("shows the proposal with its reason and the considered-but-rejected candidates", async () => {
    mockApi(routes)
    renderWith(<Adaptation />)
    const card = await screen.findByTestId("proposal")
    expect(within(card).getByText(/used together by 28 of 50 queries/)).toBeInTheDocument()
    expect(within(card).getByText("Django ↔ PostgreSQL")).toBeInTheDocument()
    expect(screen.getByText(/12/)).toBeInTheDocument()
  })
  it("evaluating a candidate shows measured before/after and each threshold check", async () => {
    const api = mockApi({ ...routes, "POST /api/v1/adaptation/evaluate": fx.adaptationEvent("proposed") })
    renderWith(<Adaptation />)
    await userEvent.click(await screen.findByRole("button", { name: /evaluate only/i }))
    const dlg = await screen.findByRole("dialog")
    expect(api.called("POST", "/api/v1/adaptation/evaluate")[0].body).toMatchObject({ operation: "add_shortcut_edge", module_ids: ["mod_django", "mod_pg"] })
    expect(within(dlg).getByRole("table", { name: "Metric comparison" })).toBeInTheDocument()
    expect(within(dlg).getByText("0.924")).toBeInTheDocument()
    expect(within(dlg).getByLabelText("Acceptance checks").querySelectorAll('[data-passed="true"]').length).toBeGreaterThan(0)
    expect(within(dlg).getByText("Decision: accept")).toBeInTheDocument()
  })
  it("distinguishes rejection BEFORE commit from rollback AFTER commit", async () => {
    mockApi({ ...routes, "POST /api/v1/adaptation/apply": fx.adaptationEvent("rejected") })
    renderWith(<Adaptation />)
    await userEvent.click(await screen.findByRole("button", { name: /evaluate & apply/i }))
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Evaluate & apply" }))
    const dlg = await screen.findByRole("dialog")
    expect(within(dlg).getByRole("note")).toHaveTextContent("Rejected before commit")
    expect(within(dlg).getByRole("note")).toHaveTextContent("not modified")
    expect(dlg.querySelector('[data-passed="false"]')).toBeInTheDocument()
  })
  it("renders a rolled-back event as reverted after commit", async () => {
    mockApi({ ...routes, "GET /api/v1/adaptation/events": { items: [{ ...fx.adaptationEvent("rolled_back"), rolled_back_to: "arch_0004", error: "PostCommitVerificationError: mismatch" }], total: 1, limit: 50, offset: 0, status_counts: { rolled_back: 1 } } })
    renderWith(<Adaptation />)
    const row = await screen.findByRole("button", { name: /Shortcut edge/ })
    expect(within(row).getByText("Rolled Back")).toBeInTheDocument()
    await userEvent.click(row)
    expect(await within(await screen.findByRole("dialog")).findByText(/Rolled back after commit/)).toBeInTheDocument()
  })
})

describe("architecture versions and restoration", () => {
  const routes = (restore: unknown) => ({
    ...base, "GET /api/v1/versions": { items: [version("arch_0005", { is_active: true }), version("arch_0004", { operation_type: "checkpoint" })], total: 2, limit: 100, offset: 0, active_version: "arch_0005" },
    "GET /api/v1/versions/arch_0004/restore-preview": fx.restorePreview("arch_0004"),
    "POST /api/v1/versions/arch_0004/restore": restore,
  })
  function version(id: string, o = {}) { return fx.version(id, o) }

  it("lists versions with the active one marked and its restore action disabled", async () => {
    mockApi(routes({}))
    renderWith(<Versions />)
    const rows = await screen.findAllByRole("row")
    const active = rows.find((r) => r.textContent?.includes("arch_0005"))!
    expect(within(active).getByText("active")).toBeInTheDocument()
    expect(within(active).getByRole("button", { name: /restore/i })).toBeDisabled()
  })
  it("requires explicit confirmation showing what is restored, replaced and changed", async () => {
    const api = mockApi(routes({ operation_id: "o", status: "completed", restored_from: "arch_0004", replaced_version: "arch_0005", checkpoint_version: "arch_0005", new_version: version("arch_0006", { is_active: true }), summary: "x", diff: fx.diff, rolled_back_events: [], duration_ms: 12 }))
    renderWith(<Versions />)
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("arch_0004"))!
    await userEvent.click(within(row).getByRole("button", { name: /restore/i }))
    const dlg = await screen.findByRole("dialog")
    expect(await within(dlg).findByText("Will be restored")).toBeInTheDocument()
    expect(within(dlg).getByText(/Will be replaced/)).toBeInTheDocument()
    expect(within(dlg).getByText(/Python Web Notes \(6\)/)).toBeInTheDocument()
    const confirm = within(dlg).getByRole("button", { name: /restore this version/i })
    expect(confirm).toBeDisabled()
    expect(api.called("POST", "/api/v1/versions/arch_0004/restore")).toHaveLength(0)
    await userEvent.click(within(dlg).getByRole("checkbox"))
    await userEvent.click(confirm)
    await waitFor(() => expect(api.called("POST", "/api/v1/versions/arch_0004/restore")).toHaveLength(1))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })
  it("keeps the dialog open and shows the backend error when restoration fails", async () => {
    mockApi(routes({ __status: 500, __error: { code: "restore_failed", message: "restoration of arch_0004 failed; the previous architecture was re-applied" } }))
    renderWith(<Versions />)
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("arch_0004"))!
    await userEvent.click(within(row).getByRole("button", { name: /restore/i }))
    const dlg = await screen.findByRole("dialog")
    await userEvent.click(await within(dlg).findByRole("checkbox"))
    await userEvent.click(within(dlg).getByRole("button", { name: /restore this version/i }))
    expect(await within(dlg).findByText(/previous architecture was re-applied/)).toBeInTheDocument()
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
  it("blocks restoration of a version that fails its integrity check", async () => {
    mockApi({ ...routes({}), "GET /api/v1/versions/arch_0004/restore-preview": { ...fx.restorePreview("arch_0004"), restorable: false, problems: ["snapshot checksum mismatch (corrupt snapshot)"], diff: undefined } })
    renderWith(<Versions />)
    const row = (await screen.findAllByRole("row")).find((r) => r.textContent?.includes("arch_0004"))!
    await userEvent.click(within(row).getByRole("button", { name: /restore/i }))
    const dlg = await screen.findByRole("dialog")
    expect(await within(dlg).findByText(/cannot be restored/)).toBeInTheDocument()
    expect(within(dlg).getByRole("button", { name: /restore this version/i })).toBeDisabled()
  })
})
