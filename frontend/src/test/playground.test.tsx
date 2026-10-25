import { beforeEach, describe, expect, it } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import QueryPlayground from "@/pages/QueryPlayground"
import { setRoute } from "@/lib/route-store"
import * as fx from "./fixtures"
import { mockApi, renderWith } from "./utils"

const stage = (n: number, kind: "seed" | "expansion", changed: boolean | null, decision = "continue") => ({
  stage: n, kind, policy: "profile", modules: [{ module_id: `m${n}`, name: `Module ${n}`, profile_rank: n, profile_score: 0.5 - n / 10, records: 5 }],
  new_candidates: 5, cumulative_candidates: 5 * n, hits_returned: 3, best_score: 0.71, kth_score: 0.4, top_k_changed: changed, new_in_top_k: changed ? 1 : changed === false ? 0 : null, search_ms: 1.2, decision,
})
const progressive = {
  ...fx.queryResponse, mode: "progressive",
  trace: { ...fx.queryResponse.trace, mode: "progressive", strategy: "progressive", embedding_ms: 3.1, timings_ms: { seed: 0.4, expand: 0.1, search: 2.2, rank: 0.1, fallback: 0 },
    stages: [stage(1, "seed", null), stage(2, "expansion", false, "stop: the top-10 did not change for 1 expansion step(s)")],
    progressive: { stop_reason: "stable", stop_explanation: "stable", stages_run: 2, modules_searched: 6, modules_total: 12, policy: "profile", stability_k: 10, patience: 1, fallback_on_budget: true, unsettled_at_stop: false, budget_modules: 12, max_steps: 4 },
    searched_modules: [{ module_id: "mod_django", name: "Django", stage: 1, origin: "seed", records: 5 }], result_modules: ["mod_django"] },
}
const softRow = (rank: number, was: number, change: "promoted" | "demoted" | "unchanged") => ({
  memory_id: `mem_${rank}`, module_id: "mod_django", module_name: "Django", dense_score: 0.8 - rank / 100, dense_rank: was, profile_score: 0.4, profile_rank: 1, profile_component: 1, graph_distance: 0, graph_component: 1,
  prior: 0.9, prior_centered: 0.3, score_shift: change === "promoted" ? 0.006 : change === "demoted" ? -0.004 : 0, final_score: 0.8, final_rank: rank, change, reason: "r",
})
const soft = {
  ...fx.queryResponse, mode: "soft",
  trace: { ...fx.queryResponse.trace, mode: "soft", strategy: "soft", timings_ms: { seed: 0.3, expand: 0, search: 5, rank: 0.1, fallback: 0 },
    soft: { weight: 0.02, graph_share: 0.3, pool_size: 100, seed_modules: ["mod_django"], graph_depth: 2, min_coaccess_observations: 10, pool_mean_prior: 0.6, formula: "final = dense + weight * (prior - pool_mean_prior)", promoted: 1, demoted: 1, unchanged: 1, top_k_membership_changed: false, full_global_search: true,
      results: [softRow(1, 2, "promoted"), softRow(2, 1, "demoted"), softRow(3, 3, "unchanged")] } },
}
const evalDs = { query_count: 59, splits: { dev: 59 }, dataset_hash: "h", valid: true, issues: [], usable_queries: 59, skipped_queries: 0, labels_missing: 0, min_eval_queries: 10, thresholds: {}, meta: {},
  sample: [{ query_id: "q_django", query: "How does Django route URLs?", split: "dev", tags: [], relevant: 2 }] }
const entry = (strategy: string, over: Record<string, unknown> = {}) => ({
  strategy, label: strategy, latency_ms: 1.5, embedding_ms: 2, timings_ms: { search: 1 }, candidate_count: 13, eligible_candidates: 13, candidate_pool_reduction: 0.78, fallback_used: false, fallback_reason: null,
  seed_modules: [], searched_modules: strategy === "graph" ? [{ module_id: "mod_django", name: "Django", origin: "seed", stage: null }] : [], stages: [], strategy_details: {},
  results: [{ rank: 1, memory_id: "mem_1", score: 0.81, module_id: "mod_django", module_name: "Django", source_name: "d.md", text: "Django maps URL patterns to views.", relevant: true, dense_score: null }],
  result_modules: ["mod_django"], metrics: { recall_at_k: 0.5, mrr: 1, ndcg_at_10: 0.63, precision_at_k: 0.2, map_at_10: 0.5, k: 5 }, relevant_retrieved: ["mem_1"], relevant_missed: ["mem_9"], relevant_excluded_by_routing: [], ...over,
})
const compare = {
  query: "How does Django route URLs?", query_id: "q_django", top_k: 5, labeled: true, architecture_version: "arch_0004", active_records: 60,
  relevant_memories: [{ memory_id: "mem_1", module_id: "mod_django", module_name: "Django", text_preview: "a" }, { memory_id: "mem_9", module_id: "mod_pg", module_name: "PostgreSQL", text_preview: "b" }],
  strategies: { global: entry("global", { candidate_pool_reduction: 0, candidate_count: 60, metrics: { recall_at_k: 1, mrr: 1, ndcg_at_10: 0.9, precision_at_k: 0.4, map_at_10: 0.8, k: 5 }, relevant_retrieved: ["mem_1", "mem_9"] }),
    graph: entry("graph", { relevant_excluded_by_routing: [{ memory_id: "mem_9", module_id: "mod_pg", module_name: "PostgreSQL", text_preview: "Postgres pooling" }] }), progressive: entry("progressive"), soft: entry("soft", { candidate_count: 60, candidate_pool_reduction: 0 }) },
  notes: ["Every strategy ran the real retrieval engine."],
}
const routes = { "GET /api/v1/evaluation/dataset": evalDs, "GET /api/v1/health": {}, "GET /api/v1/config": {} }

describe("query playground strategies", () => {
  beforeEach(() => setRoute(null))
  it("offers all four strategies with an explanation of the selected one", async () => {
    mockApi(routes)
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    expect(screen.getByTestId("mode-hint")).toHaveTextContent("Fixed graph")
    await user.click(screen.getByLabelText("retrieval mode"))
    for (const n of ["Global dense", "Fixed graph", "Progressive expansion", "Soft graph routing"]) expect(await screen.findByRole("option", { name: n })).toBeInTheDocument()
    await user.click(screen.getByRole("option", { name: "Soft graph routing" }))
    expect(screen.getByTestId("mode-hint")).toHaveTextContent("nudge the ranking")
  })
  it("progressive expansion sends its own mode and shows every stage, the stop reason and the timing breakdown", async () => {
    const api = mockApi({ ...routes, "POST /api/v1/query": progressive })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.type(screen.getByLabelText("Query"), "django urls")
    await user.click(screen.getByLabelText("retrieval mode"))
    await user.click(await screen.findByRole("option", { name: "Progressive expansion" }))
    await user.click(screen.getByRole("button", { name: /run query/i }))
    const trace = await screen.findByTestId("progressive-trace")
    expect(api.called("POST", "/api/v1/query")[0].body).toMatchObject({ mode: "progressive", debug_trace: true })
    expect(within(trace).getByTestId("stage-1")).toHaveTextContent("seed modules")
    expect(within(trace).getByTestId("stage-2")).toHaveTextContent("unchanged")
    expect(trace).toHaveTextContent("6 of 12 modules searched")
    expect(trace).toHaveTextContent("no global fallback")
    expect(screen.getByTestId("active-strategy")).toHaveTextContent("Progressive expansion")
    expect(screen.getByTestId("timing-bar")).toHaveTextContent("Vector search")
    expect(screen.queryByTestId("routing-trace")).not.toBeInTheDocument()
  })
  it("soft routing shows dense rank, shift and final rank per result and says the prior is not a relevance label", async () => {
    mockApi({ ...routes, "POST /api/v1/query": soft })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.type(screen.getByLabelText("Query"), "django urls")
    await user.click(screen.getByLabelText("retrieval mode"))
    await user.click(await screen.findByRole("option", { name: "Soft graph routing" }))
    await user.click(screen.getByRole("button", { name: /run query/i }))
    const t = await screen.findByTestId("soft-trace")
    const table = within(t).getByRole("table", { name: "Soft routing breakdown" })
    expect(within(table).getAllByRole("row")).toHaveLength(4)
    expect(table).toHaveTextContent("was 2")
    expect(table).toHaveTextContent("+0.0060")
    expect(t).toHaveTextContent("weight λ = 0.02")
    expect(t).toHaveTextContent("not a relevance label")
  })
  it("a successful query publishes its real trace for the Graph Explorer", async () => {
    mockApi({ ...routes, "POST /api/v1/query": progressive })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.type(screen.getByLabelText("Query"), "django urls")
    await user.click(screen.getByRole("button", { name: /run query/i }))
    await screen.findByTestId("active-strategy")
    const saved = JSON.parse(sessionStorage.getItem("morphe.route.v1")!)
    expect(saved.query).toBe("django urls")
    expect(saved.trace.stages).toHaveLength(2)
  })
  it("a failing query shows the backend error and does not leave a stale route", async () => {
    mockApi({ ...routes, "POST /api/v1/query": { __status: 503, __error: { code: "qdrant_unavailable", message: "Qdrant could not be opened" } } })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.type(screen.getByLabelText("Query"), "x")
    await user.click(screen.getByRole("button", { name: /run query/i }))
    expect(await screen.findByText("Qdrant could not be opened")).toBeInTheDocument()
    expect(sessionStorage.getItem("morphe.route.v1")).toBeNull()
  })
})

describe("compare all four strategies", () => {
  beforeEach(() => setRoute(null))
  it("runs a labeled example and shows quality, candidates, fallback and the relevant memories routing excluded", async () => {
    const api = mockApi({ ...routes, "POST /api/v1/evaluation/compare-strategies": compare })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.click(screen.getByRole("tab", { name: /Compare all four/ }))
    await user.click(await screen.findByLabelText("Query to compare"))
    await user.click(await screen.findByRole("option", { name: "How does Django route URLs?" }))
    await user.click(screen.getByRole("button", { name: /Compare four strategies/ }))
    const table = await screen.findByRole("table", { name: "Strategy comparison" })
    expect(api.called("POST", "/api/v1/evaluation/compare-strategies")[0].body).toMatchObject({ query_id: "q_django", top_k: 5 })
    const row = (n: string) => within(table).getByText(n).closest("tr")!
    expect(row("Global dense")).toHaveTextContent("2 / 2")
    expect(row("Global dense")).toHaveTextContent("0.900")
    expect(row("Fixed graph")).toHaveTextContent("1 / 2")
    expect(row("Soft graph routing")).toHaveTextContent("0%")
    await user.click(screen.getByRole("tab", { name: "Fixed graph" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("1 labeled-relevant memory was in modules this strategy never searched")
    expect(screen.getByRole("alert")).toHaveTextContent("PostgreSQL")
  })
  it("free text is compared without inventing quality metrics", async () => {
    mockApi({ ...routes, "POST /api/v1/evaluation/compare-strategies": { ...compare, query_id: null, labeled: false, relevant_memories: [],
      strategies: Object.fromEntries(Object.entries(compare.strategies).map(([k, v]) => [k, { ...v, metrics: undefined, relevant_retrieved: undefined, relevant_missed: undefined, relevant_excluded_by_routing: undefined, results: v.results.map((r) => ({ ...r, relevant: null })) }])) } })
    const user = userEvent.setup()
    renderWith(<QueryPlayground />)
    await user.type(screen.getByLabelText("Query"), "some free text")
    await user.click(screen.getByRole("tab", { name: /Compare all four/ }))
    await user.click(screen.getByRole("button", { name: /Compare four strategies/ }))
    const table = await screen.findByRole("table", { name: "Strategy comparison" })
    await waitFor(() => expect(within(table).getAllByText("—").length).toBeGreaterThanOrEqual(12))
    expect(screen.getByText("unlabeled")).toBeInTheDocument()
  })
})
