import { beforeEach, describe, expect, it } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import GraphExplorer from "@/pages/GraphExplorer"
import { EdgePanel } from "@/components/app/EdgePanel"
import { layoutGraph, NODE_W } from "@/components/graph/layout"
import { routeFromResponse, routeStates, setRoute } from "@/lib/route-store"
import type { GraphData, GraphEdge, GraphNode, QueryResponse } from "@/api/types"
import * as fx from "./fixtures"
import { mockApi, renderWith } from "./utils"

const node = (id: string, label: string, over: Partial<GraphNode["data"]> = {}, isContainer = false): GraphNode => ({
  id, type: "moduleNode", position: { x: 0, y: 0 }, data: { label, recordCount: isContainer ? 0 : 5, parentId: null, isContainer, lastChangeOp: null, lastChangeAt: null, ...over },
})
const hier = (p: string, c: string): GraphEdge => ({ id: `h_${p}_${c}`, source: p, target: c, label: "hierarchy", data: { relationType: "hierarchy", weight: 1, coAccessCount: 0, createdAt: null } })
const overlap = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

describe("graph layout (built only from real hierarchy edges)", () => {
  const nodes = [node("g1", "Data", {}, true), node("a", "Postgres"), node("b", "Redis"), node("c", "Alpha"), node("g2", "Web", {}, true), node("d", "Django"), node("lone1", "Loose one"), node("lone2", "Loose two")]
  const edges = [hier("g1", "a"), hier("g1", "b"), hier("g2", "d")]
  it("places every node exactly once, without overlaps, children below their group", () => {
    const l = layoutGraph(nodes, edges, 1.6)
    expect([...l.nodes.keys()].sort()).toEqual(nodes.map((n) => n.id).sort())
    const rects = [...l.nodes.values()]
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) expect(overlap(rects[i], rects[j])).toBe(false)
    expect(l.nodes.get("a")!.y).toBeGreaterThan(l.nodes.get("g1")!.y)
    expect(l.nodes.get("d")!.y).toBeGreaterThan(l.nodes.get("g2")!.y)
    expect(rects.every((r) => r.w === NODE_W)).toBe(true)
  })
  it("is deterministic and responds to the canvas aspect ratio", () => {
    expect(layoutGraph(nodes, edges, 1.6)).toEqual(layoutGraph(nodes, edges, 1.6))
    const wide = layoutGraph(nodes, edges, 3), tall = layoutGraph(nodes, edges, 0.6)
    expect(wide.width / wide.height).toBeGreaterThanOrEqual(tall.width / tall.height)
  })
  it("groups parentless, childless modules into one labelled cluster instead of inventing a parent", () => {
    const l = layoutGraph(nodes, edges)
    const loose = l.clusters.find((c) => c.label === "No parent group")
    expect(loose).toBeTruthy()
    expect(loose!.members).toBe(3)  // Alpha, Loose one, Loose two: none has a parent or a child
    expect(l.clusters.filter((c) => c.label === null).length).toBe(2)  // the two real hierarchy trees carry no invented label
  })
  it("survives dangling edges, duplicate parents and cycles", () => {
    const messy = [node("x", "X", {}, true), node("y", "Y"), node("p", "P", {}, true), node("q", "Q", {}, true)]
    const l = layoutGraph(messy, [hier("x", "y"), hier("ghost", "y"), hier("x", "nope"), hier("p", "q"), hier("q", "p"), hier("x", "x")])
    expect([...l.nodes.keys()].sort()).toEqual(["p", "q", "x", "y"])
    expect(Number.isFinite(l.width) && Number.isFinite(l.height)).toBe(true)
  })
  it("handles an empty graph", () => {
    const l = layoutGraph([], [])
    expect(l.nodes.size).toBe(0)
    expect(l.clusters).toEqual([])
  })
})

const trace = (over: Record<string, unknown> = {}) => ({ ...fx.queryResponse.trace, strategy: "progressive", ...over })
const response = (over: Record<string, unknown> = {}, tr: Record<string, unknown> = {}): QueryResponse => ({ ...fx.queryResponse, mode: "progressive", trace: trace(tr), ...over }) as unknown as QueryResponse

describe("route store derives roles only from the backend trace", () => {
  beforeEach(() => setRoute(null))
  it("returns null without a trace (no invented route)", () => {
    expect(routeFromResponse("q", "graph", response({ trace: null }))).toBeNull()
  })
  it("maps seeds, expansion stages, searched modules, result modules and the edge the expansion used", () => {
    const r = routeFromResponse("q", "graph", response({}, {
      searched_modules: [{ module_id: "mod_django", name: "Django", stage: 1, origin: "seed", records: 5 }, { module_id: "mod_pg", name: "PostgreSQL", stage: 2, origin: "expansion", records: 8 }],
    }))!
    const s = routeStates(r)
    expect(s.modules.get("mod_django")).toEqual({ role: "seed", stage: 1, results: 1 })
    expect(s.modules.get("mod_pg")).toMatchObject({ role: "expanded", stage: 2, results: 0 })
    expect(s.viaEdges.has("mod_django|mod_pg")).toBe(true)  // trace: mod_pg was reached via mod_django
    expect(s.wholeCorpus).toBe(false)
  })
  it("marks global and soft routes, and any fallback, as whole-corpus searches", () => {
    expect(routeStates(routeFromResponse("q", "global", response({ mode: "global" }))!).wholeCorpus).toBe(true)
    expect(routeStates(routeFromResponse("q", "soft", response({ mode: "soft" }))!).wholeCorpus).toBe(true)
    expect(routeStates(routeFromResponse("q", "graph", response({}, { fallback_used: true }))!).fallback).toBe(true)
  })
  it("persists across page views for the session", () => {
    setRoute(routeFromResponse("persist me", "graph", response())!)
    expect(JSON.parse(sessionStorage.getItem("morphe.route.v1")!).query).toBe("persist me")
    setRoute(null)
    expect(sessionStorage.getItem("morphe.route.v1")).toBeNull()
  })
})

describe("graph explorer interactions", () => {
  const graph: GraphData = { ...fx.graph, capacity_threshold: 1000, nodes: fx.graph.nodes.map((n) => ({ ...n, data: { ...n.data, utilization: n.data.isContainer ? null : n.data.recordCount / 1000 } })) } as GraphData
  const routes = { "GET /api/v1/graph": graph, "GET /api/v1/modules": { items: fx.modules.items.map((m) => ({ ...m, capacity_threshold: 1000, utilization: m.is_container ? null : m.record_count / 1000 })) },
    "GET /api/v1/modules/mod_django": { ...fx.moduleDetail, capacity_threshold: 1000, utilization: 0.005, architecture_version: "arch_0004" }, "GET /api/v1/health": {}, "GET /api/v1/config": {} }
  beforeEach(() => { setRoute(null) })

  it("search highlights matches, reports the count, and shows a clear no-match state", async () => {
    mockApi(routes)
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await screen.findByTestId("module-node-Django")
    await user.type(screen.getByLabelText("Search modules"), "djan")
    await waitFor(() => expect(screen.getByText(/Showing/).textContent).toContain("1 match “djan”"))
    await user.clear(screen.getByLabelText("Search modules"))
    await user.type(screen.getByLabelText("Search modules"), "zzzz")
    expect(await screen.findByText("No module matches “zzzz”")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Clear search and filters" }))
    expect(screen.queryByText("No module matches “zzzz”")).not.toBeInTheDocument()
  })
  it("the record filter hides small modules and says how many are shown", async () => {
    mockApi(routes)
    renderWith(<GraphExplorer />)
    await screen.findByTestId("module-node-Django")
    expect(screen.getByText(/Showing/).textContent).toContain("3 of 3 modules")
    const user = userEvent.setup()
    await user.click(screen.getByLabelText("Minimum records per module"))
    await user.click(await screen.findByRole("option", { name: "≥ 5 records" }))
    await waitFor(() => expect(screen.getByText(/Showing/).textContent).toContain("3 of 3 modules"))  // Django has 5, PostgreSQL 8: both stay
  })
  it("edge toggles and view buttons are real controls", async () => {
    mockApi(routes)
    renderWith(<GraphExplorer />)
    await screen.findByTestId("module-node-Django")
    expect(screen.getByText(/2 of 2 edges/)).toBeInTheDocument()
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Show shortcut edges" }))
    expect(await screen.findByText(/1 of 2 edges/)).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Show hierarchy edges" }))
    expect(await screen.findByText(/0 of 2 edges/)).toBeInTheDocument()
    for (const name of ["Zoom in", "Zoom out", "Fit graph to view", "Reset view", "Refresh graph"]) expect(screen.getByRole("button", { name })).toBeEnabled()
  })
  it("module panel shows real capacity, architecture version and incoming/outgoing relationships", async () => {
    mockApi(routes)
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await user.click(await screen.findByRole("tab", { name: "Module list" }))
    await user.click(await screen.findByRole("button", { name: /^Django/ }))
    const panel = await screen.findByTestId("module-panel")
    expect(panel).toHaveTextContent("5 / 1,000 records")
    expect(panel).toHaveTextContent("0.50%")
    expect(within(panel).getByText("Incoming (0)")).toBeInTheDocument()
    expect(panel).toHaveTextContent("arch_0004")
  })
  it("tracing a query calls the real endpoint with the chosen strategy and highlights the backend route", async () => {
    const api = mockApi({ ...routes, "POST /api/v1/query": response({}, { searched_modules: [{ module_id: "mod_django", name: "Django", stage: 1, origin: "seed", records: 5 }], seed_modules: [{ module_id: "mod_django", name: "Django", score: 0.8 }] }) })
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await screen.findByTestId("module-node-Django")
    await user.type(screen.getByLabelText("Query to trace"), "how does django route urls")
    await user.click(screen.getByRole("button", { name: /^Trace$/ }))
    const banner = await screen.findByTestId("route-banner")
    expect(api.called("POST", "/api/v1/query")[0].body).toMatchObject({ query: "how does django route urls", mode: "progressive", debug_trace: true })
    expect(banner).toHaveTextContent("Progressive expansion")
    expect(screen.getByTestId("module-node-Django")).toHaveTextContent("Seed")
    expect(screen.getByLabelText("Route legend")).toHaveTextContent("seed module")
    await user.click(within(banner).getByRole("button", { name: /Hide route/ }))
    await waitFor(() => expect(screen.getByTestId("module-node-Django")).not.toHaveTextContent("Seed"))
    await user.click(within(banner).getByRole("button", { name: "Clear route" }))
    expect(screen.queryByTestId("route-banner")).not.toBeInTheDocument()
  })
  it("a failing trace shows the backend error and leaves no stale route", async () => {
    mockApi({ ...routes, "POST /api/v1/query": { __status: 503, __error: { code: "qdrant_unavailable", message: "Qdrant could not be opened" } } })
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await screen.findByTestId("module-node-Django")
    await user.type(screen.getByLabelText("Query to trace"), "x")
    await user.click(screen.getByRole("button", { name: /^Trace$/ }))
    expect(await screen.findByText("Qdrant could not be opened")).toBeInTheDocument()
    expect(screen.queryByTestId("route-banner")).not.toBeInTheDocument()
  })
  it("the module list is a complete alternative: relationships, filters and show-in-graph", async () => {
    mockApi(routes)
    const user = userEvent.setup()
    renderWith(<GraphExplorer />)
    await user.click(await screen.findByRole("tab", { name: "Module list" }))
    const list = await screen.findByRole("list", { name: "Modules" })
    expect(list).toHaveTextContent("Django")
    expect(list).toHaveTextContent("in Web Frameworks")
    expect(list).toHaveTextContent("shortcut ×1")
    await user.click(screen.getByRole("radio", { name: "Groups" }))
    expect(within(await screen.findByRole("list", { name: "Modules" })).queryByText("PostgreSQL")).not.toBeInTheDocument()
    await user.click(screen.getByRole("radio", { name: "All" }))
    const django = within(await screen.findByRole("list", { name: "Modules" })).getByText("Django").closest("li")!
    await user.click(within(django).getByRole("button", { name: /Show in graph/ }))
    expect(await screen.findByTestId("graph-canvas")).toBeInTheDocument()
  })
})

describe("edge panel", () => {
  const g = fx.graph as unknown as GraphData
  const co = { ...g.edges[1], data: { ...g.edges[1].data, evidence: { window_queries: 50, queries_with_both: 28 }, operationId: "op_123" } } as GraphEdge
  it("shows the real attributes and evidence of a shortcut, and does not oversell it", () => {
    renderWith(<EdgePanel edge={co} graph={g} onSelectModule={() => {}} />)
    const p = screen.getByTestId("edge-panel")
    expect(p).toHaveTextContent("Co-access shortcut")
    expect(p).toHaveTextContent("28")
    expect(p).toHaveTextContent("queries with both")
    expect(p).toHaveTextContent("op_123")
    expect(p).toHaveTextContent("Whether it actually helps retrieval was decided by the validation")
  })
  it("says so when no evidence was recorded", () => {
    renderWith(<EdgePanel edge={{ ...co, data: { ...co.data, evidence: {} } }} graph={g} onSelectModule={() => {}} />)
    expect(screen.getByText("No evidence was recorded for this edge.")).toBeInTheDocument()
  })
  it("a hierarchy edge shows no shortcut evidence section", () => {
    renderWith(<EdgePanel edge={g.edges[0]} graph={g} onSelectModule={() => {}} />)
    expect(screen.getByTestId("edge-panel")).toHaveTextContent("Hierarchy edge")
    expect(screen.queryByText(/Evidence recorded/)).not.toBeInTheDocument()
  })
})
