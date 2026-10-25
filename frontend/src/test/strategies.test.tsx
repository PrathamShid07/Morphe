import { describe, expect, it } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import Benchmarks from "@/pages/Benchmarks"
import { mockApi, renderWith } from "./utils"

const q = (nd: number) => ({ ndcg_at_10: nd, map_at_10: nd - 0.05, recall_at_5: nd + 0.05, recall_at_10: nd + 0.1, recall_at_100: 0.9, mrr_at_10: nd, precision_at_10: 0.09, queries_evaluated: 300 })
const lat = (p50: number, elig: number, cut: number | null, fb: number) => ({ queries: 300, retrieval: { p50_ms: p50 - 2, p95_ms: p50, mean_ms: p50 }, end_to_end: { p50_ms: p50, p95_ms: p50 + 2, mean_ms: p50 }, query_embedding: { p50_ms: 2, p95_ms: 3, mean_ms: 2 },
  stage_seed: { mean_ms: 0.2 }, stage_expand: { mean_ms: 0.1 }, stage_search: { mean_ms: p50 - 3 }, stage_rank: { mean_ms: 0.01 }, stage_fallback: { mean_ms: 1 }, mean_eligible_candidates: elig, candidate_pool_reduction_vs_corpus: cut, mean_candidates_incl_fallback: elig, fallback_rate: fb })
const boot = (m: number, lo: number, hi: number) => ({ mean_diff: m, ci95_low: lo, ci95_high: hi, n_queries: 300, resamples: 1000, seed: 13, excludes_zero: lo > 0 || hi < 0, queries_improved: 5, queries_regressed: 9, queries_unchanged: 286, note: "" })
const cmp = (t: string, b: string, m: number, lo: number, hi: number) => ({ comparison: `${t}_vs_${b}`, baseline: b, treatment: t, queries: 300, metric_deltas: {}, bootstrap: { ndcg_at_10: boot(m, lo, hi), recall_at_5: boot(m, lo, hi) } })
const diag = { routing: { relevant_documents_excluded_by_routing: 66, queries_with_exclusion: 64, queries_degraded_with_exclusion: 42 }, vs_global_dense: { queries_improved: 14, queries_degraded: 42, queries_unchanged: 244, relevant_top10_lost: 44, relevant_top10_gained: 4 } }
const group = (g: string, status: string) => ({
  run_id: "strat_t", dataset_id: "scifact", group: g, queries: 300, status, smoke: false, official_test_split: g === "test", repetitions: 3, frozen_config_sha256: "abc",
  metrics: { global_dense: q(0.645), fixed_graph: q(0.55), progressive: q(0.632), soft: q(0.643) },
  latency: { global_dense: lat(20, 5183, 0, 0), fixed_graph: lat(35, 886, 0.83, 0.013), progressive: lat(123, 1250, 0.76, 0.41), soft: lat(20.5, 5183, 0, 0) },
  comparisons: [cmp("fixed_graph", "global_dense", -0.094, -0.127, -0.063), cmp("progressive", "global_dense", -0.0127, -0.0262, -0.0032), cmp("soft", "global_dense", -0.0021, -0.0053, 0.0005), cmp("progressive", "fixed_graph", 0.0817, 0.053, 0.112), cmp("soft", "fixed_graph", 0.092, 0.061, 0.125)],
  diagnostics: { fixed_graph: diag, progressive: { ...diag, progressive: { stop_reasons: { stable: 177, budget: 123 }, stages_run_distribution: { "3": 79, "4": 221 }, queries_with_fallback: 123, queries_where_expansion_never_changed_the_top_k: 100, mean_useful_expansion_steps: 0.4 } },
    soft: { ...diag, soft: { mean_promoted_in_top_k: 33.9, mean_demoted_in_top_k: 29.3, queries_with_any_reordering: 300 } } },
  baseline_context: g === "test" ? { source: "beir_full_v2_01 (frozen v2)", same_test_group_size: true, metrics: { bm25: q(0.663) } } : null,
})
const run = { run_id: "strat_t", status: "completed", created_at: "2026-10-25T01:00:00Z", datasets: ["scifact"], evaluated: { scifact: ["validation", "test"] }, smoke: false, frozen: true }
const detail = {
  manifest: { run_id: "strat_t", status: "completed", datasets: ["scifact"], seed: 13, smoke: false, created_at: "x", architecture: "initial", frozen_config_sha256: "deadbeefcafe1234" },
  frozen_config: { frozen_at: "2026-10-25T00:00:00Z", selected_from: "validation group only", progressive: { policy: "profile", patience: 2, step: 4, seed_modules: 3, max_modules: 12, max_steps: 4, fallback_on_budget: true }, soft: { weight: 0.01, share: 0, candidate_pool: 100, seed_modules: 3, graph_depth: 2 },
    selection_rules: { progressive: "rule p", soft: "rule s" }, code_sha256: {} },
  selection: { progressive: { selected: { policy: "profile", patience: 2, step: 4 }, note: "NO configuration was non-inferior to global dense on every validation dataset", table: [{ config: { policy: "profile", patience: 2, step: 4 }, macro_ndcg: 0.48, macro_work_share: 0.56, non_inferior: false, ndcg_delta_vs_global: { scifact: -0.0125 } }] },
    soft: { selected: { weight: 0.01, share: 0 }, reliable_gain: false, note: "n", table: [{ config: { weight: 0, share: 0 }, macro_ndcg: 0.488, ndcg_delta_vs_dense: { scifact: 0 } }, { config: { weight: 0.01, share: 0 }, macro_ndcg: 0.4876, ndcg_delta_vs_dense: { scifact: -0.0018 } }], bootstrap_vs_weight_zero_on_validation: {} } },
  server_latency: { deployment: "Qdrant server 1.20.0", group: "validation", queries: 100, repetitions: 5, datasets: { scifact: { documents: 5183, queries: 100, strategies: {
    global_dense: { retrieval_p50_ms: 0.97, retrieval_p95_ms: 1.0, retrieval_mean_ms: 1, top10_identical_to_local_qdrant: "100/100" }, fixed_graph: { retrieval_p50_ms: 1.82, retrieval_p95_ms: 1.9, retrieval_mean_ms: 1.8, top10_identical_to_local_qdrant: "100/100" },
    progressive: { retrieval_p50_ms: 5.07, retrieval_p95_ms: 6.2, retrieval_mean_ms: 5, top10_identical_to_local_qdrant: "100/100" }, soft: { retrieval_p50_ms: 1.75, retrieval_p95_ms: 1.8, retrieval_mean_ms: 1.7, top10_identical_to_local_qdrant: "100/100" } } } } },
}
const row = (id: string, d: number) => ({ query_id: id, query: `claim ${id}`, delta_ndcg_at_10: d, ...Object.fromEntries(["global_dense", "fixed_graph", "progressive", "soft"].map((s) => [s, { ...q(0.5), e2e_ms: 20, retrieval_ms: 18, eligible_candidates: 100, fallback: 0, modules_searched: 3, excluded_relevant: s === "fixed_graph" ? 1 : 0, relevant_total: 1, fallback_reason: null, stages_run: s === "progressive" ? 3 : null, stop_reason: s === "progressive" ? "stable" : null, useful_expansion_steps: null, soft_promoted: null, soft_demoted: null }])) })
const routes = (extra = {}) => ({
  "GET /api/v1/benchmarks/runs": { items: [] }, "GET /api/v1/benchmarks/status": { active_run: null, queued: [] }, "GET /api/v1/benchmarks/datasets": { datasets: [], data_dir: "d", methods: [] },
  "GET /api/v1/benchmarks/strategies/runs": { items: [run] }, "GET /api/v1/benchmarks/strategies/runs/strat_t": detail,
  "GET /api/v1/benchmarks/strategies/runs/strat_t/scifact/test": group("test", "exploratory: SciFact test labels were seen under benchmark protocol v1"),
  "GET /api/v1/benchmarks/strategies/runs/strat_t/scifact/validation": group("validation", "development (configuration was tuned on this group: optimistic)"),
  "GET /api/v1/benchmarks/strategies/runs/strat_t/scifact/test/rows": { total: 2, limit: 12, offset: 0, items: [row("q_bad", -0.4), row("q_ok", 0)] }, ...extra,
})
const open = async () => { const user = userEvent.setup(); renderWith(<Benchmarks />); await user.click(await screen.findByRole("tab", { name: "Retrieval strategies" })); return user }

describe("benchmarks: retrieval strategies tab", () => {
  it("shows all four strategies with every metric, the honest status of the group, and the BM25 context row", async () => {
    mockApi(routes())
    await open()
    const t = await screen.findByRole("table", { name: "Retrieval quality by strategy" })
    const row = (n: string | RegExp) => within(t).getByText(n).closest("tr")!
    expect(row("Global dense")).toHaveTextContent("0.6450")
    expect(row("Progressive expansion")).toHaveTextContent("0.6320")
    expect(row("Soft graph routing")).toHaveTextContent("0.6430")
    expect(row("Fixed graph")).toHaveTextContent("0.5500")
    for (const h of ["nDCG@10", "MAP@10", "Recall@5", "Recall@10", "Recall@100", "MRR@10", "Precision@10"]) expect(within(t).getByText(h)).toBeInTheDocument()
    expect(row(/^BM25/)).toHaveTextContent("0.6630")
    expect(screen.getByTestId("group-status")).toHaveTextContent("exploratory")
  })
  it("paired comparisons show intervals, and the generated explanation does not call an interval that includes 0 reliable", async () => {
    mockApi(routes())
    await open()
    const t = await screen.findByRole("table", { name: "Paired bootstrap comparisons" })
    expect(within(t).getByText("Soft graph routing − Global dense").closest("tr")).toHaveTextContent("-0.0021 [-0.0053, +0.0005]")
    const ex = screen.getByTestId("explanation")
    expect(ex).toHaveTextContent("Soft graph routing changed nDCG@10 by -0.0021")
    expect(within(ex).getByText(/Soft graph routing changed/)).toHaveTextContent("not a reliable change")
    expect(within(ex).getByText(/Progressive expansion changed/)).toHaveTextContent("a reliable loss")
    expect(ex).toHaveTextContent("It runs a full global search first")
  })
  it("efficiency: pool reduction, fallback and latency; and the indexed-server table that shows no strategy beats global search", async () => {
    mockApi(routes())
    await open()
    const e = await screen.findByRole("table", { name: "Efficiency by strategy" })
    expect(within(e).getByText("Progressive expansion").closest("tr")).toHaveTextContent("41.0%")
    expect(within(e).getByText("Fixed graph").closest("tr")).toHaveTextContent("83.0%")
    const s = await screen.findByRole("table", { name: "Latency on an indexed Qdrant server" })
    expect(within(s).getByText("Progressive expansion").closest("tr")).toHaveTextContent("5.2×")
    expect(within(s).getByText("Soft graph routing").closest("tr")).toHaveTextContent("100/100")
  })
  it("strategy selector hides a strategy everywhere", async () => {
    mockApi(routes())
    const user = await open()
    await user.click(await screen.findByRole("button", { name: "strategy soft" }))
    const t = screen.getByRole("table", { name: "Retrieval quality by strategy" })
    await waitFor(() => expect(within(t).queryByText("Soft graph routing")).not.toBeInTheDocument())
    expect(screen.queryByTestId("diag-soft")).not.toBeInTheDocument()
  })
  it("routing diagnostics and the per-query inspector come from the saved rows", async () => {
    const api = mockApi(routes())
    const user = await open()
    expect(await screen.findByTestId("diag-progressive")).toHaveTextContent("stable 177")
    expect(screen.getByTestId("diag-fixed_graph")).toHaveTextContent("66 relevant documents sat in modules it never searched")
    expect(screen.getByTestId("diag-soft")).toHaveTextContent("by construction")
    const list = await screen.findByRole("list", { name: "Per-query results" })
    expect(api.called("GET", "/api/v1/benchmarks/strategies/runs/strat_t/scifact/test/rows")[0]).toBeTruthy()
    await user.click(within(list).getByText("claim q_bad"))
    expect(await screen.findByRole("table", { name: "Strategies for this query" })).toHaveTextContent("1 of 1 relevant docs in unsearched modules")
  })
  it("switching to the validation group loads it and labels it as optimistic development data", async () => {
    mockApi(routes({ "GET /api/v1/benchmarks/strategies/runs/strat_t/scifact/validation/rows": { total: 0, limit: 12, offset: 0, items: [] } }))
    const user = await open()
    await user.click(await screen.findByRole("tab", { name: /Validation/ }))
    await waitFor(() => expect(screen.getByTestId("group-status")).toHaveTextContent("optimistic"))
  })
  it("frozen configuration and selection evidence are shown with the freeze hash", async () => {
    mockApi(routes())
    await open()
    expect(await screen.findByText(/config SHA-256/)).toBeInTheDocument()
    expect(screen.getByText(/NO configuration was non-inferior/)).toBeInTheDocument()
  })
  it("with no strategy runs it explains how to create one instead of showing empty tables", async () => {
    mockApi(routes({ "GET /api/v1/benchmarks/strategies/runs": { items: [] } }))
    await open()
    expect(await screen.findByText("No retrieval-strategy comparison has been saved yet")).toBeInTheDocument()
    expect(screen.getByText(/run_strategies/)).toBeInTheDocument()
  })
  it("a backend error is shown, not hidden", async () => {
    mockApi(routes({ "GET /api/v1/benchmarks/strategies/runs": { __status: 500, __error: { code: "boom", message: "Artifacts unreadable" } } }))
    await open()
    expect(await screen.findByText("Artifacts unreadable")).toBeInTheDocument()
  })
})
