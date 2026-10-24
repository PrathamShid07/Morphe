import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import Benchmarks from "@/pages/Benchmarks"
import { mockApi, renderWith } from "./utils"

const q = (n: number) => ({ ndcg_at_10: n, map_at_10: n - 0.05, recall_at_5: n + 0.05, recall_at_10: n + 0.1, recall_at_100: 0.9, mrr_at_10: n - 0.02, precision_at_10: 0.08, queries_evaluated: 300 })
const lat = (e2e: number, cand: number, red: number | null, fb = 0) => ({
  queries: 300, total_results: 30000, retrieval: { p50_ms: e2e - 5, p95_ms: e2e + 5, mean_ms: e2e }, end_to_end: { p50_ms: e2e, p95_ms: e2e + 10, mean_ms: e2e },
  query_embedding: { p50_ms: 5, p95_ms: 7, mean_ms: 5.5 }, stage_seed: { mean_ms: 1.5 }, stage_expand: { mean_ms: 0.5 }, stage_search: { mean_ms: 20 }, stage_fallback: { mean_ms: 0.2 },
  mean_eligible_candidates: cand, candidate_pool_reduction_vs_corpus: red, mean_candidates_incl_fallback: cand, fallback_rate: fb,
})
const boot = (d: number, lo: number, hi: number) => ({ mean_diff: d, ci95_low: lo, ci95_high: hi, n_queries: 300, resamples: 1000, seed: 13, excludes_zero: lo > 0 || hi < 0, queries_improved: 12, queries_regressed: 4, queries_unchanged: 284, note: "" })
const cmp = (name: string, b: string, t: string, d: number, lo: number, hi: number) => ({
  comparison: name, baseline: b, treatment: t, queries: 300, metric_deltas: {}, bootstrap: { ndcg_at_10: boot(d, lo, hi), recall_at_5: boot(d / 2, lo / 2, hi / 2) },
  most_improved: [{ query_id: "q77", query: "does vitamin d reduce fractures", ndcg_baseline: 0.4, ndcg_treatment: 0.9, delta: 0.5 }],
  most_regressed: [{ query_id: "q12", query: "statins and breast cancer survival", ndcg_baseline: 0.9, ndcg_treatment: 0.5, delta: -0.4 }],
})
const ev = (status: string, extra = {}) => ({
  event_id: `adapt_${status}`, operation: "add_shortcut_edge", status, reason: "Modules A and B were used together by 28 of 50 queries", trigger: "benchmark_window", affected_modules: ["m1", "m2"],
  affected_record_count: 0, decision: status === "rejected" ? "reject" : "accept", decision_reason: status === "rejected" ? "Rejected: no meaningful benefit" : "Accepted: nDCG@10 0.610→0.620",
  rejection_stage: status === "rejected" ? "pre_commit" : null, structural_summary: "+1 edge", before_version: "arch_0002", new_version: status === "completed" ? "arch_0003" : null,
  rolled_back_to: status === "rolled_back" ? "arch_0002" : null, integrity_check: "passed", error: null, started_at: "2026-10-25T00:00:00Z", duration_ms: 1000,
  baseline_metrics: { ndcg_at_10: 0.61, recall_at_5: 0.7, mean_candidate_count: 900 }, candidate_metrics: { ndcg_at_10: 0.62, recall_at_5: 0.71, mean_candidate_count: 850 }, ...extra,
})
const manifest = (o = {}) => ({
  run_id: "beir_run1", dataset_id: "scifact", status: "completed", official_test_split: true, smoke: false, seed: 13, started_at: "2026-10-25T00:00:00Z", duration_s: 321,
  dataset: { counts: { corpus: 5183 }, test_group_queries: 300, test_group_qrels: 339, relevant_grades: [1], source: { url: "u", license_note: "l", corpus_sha256: "s" } },
  split_protocol: { sizes: { workload: 404, validation: 403, test: 300 }, provenance: {}, notes: "" }, validation: { queries: 200 }, embedding: { model: "all-MiniLM-L6-v2", dimension: 384, cache_hit: true },
  graph_init: { modules: 52, groups: 7, module_size: { min: 36, median: 97, max: 172 } }, caveats: [], acceptance_thresholds: { max_ndcg_regression: 0.02, max_recall_regression: 0.02, max_mrr_regression: 0.02, max_p95_latency_regression_pct: 10, require_benefit: true },
  architecture_version_before: "arch_0002", architecture_version_after: "arch_0005", environment: { python: "3.12", platform: "macOS", torch_device: "mps", memory_gb: 16 }, timing_methodology: "time.perf_counter; warm", ...o,
})
const results = (o: { manifest?: object; events?: object[] } = {}) => ({
  run_id: "beir_run1", macro_average: {}, datasets: {
    scifact: {
      manifest: manifest(o.manifest),
      metrics: { bm25: q(0.55), global_dense: q(0.645), fixed_graph: q(0.612), adaptive_graph: q(0.621) },
      latency: { bm25: lat(8, 5183, 0), global_dense: lat(22, 5183, 0), fixed_graph: lat(41, 905.5, 0.8253, 0.033), adaptive_graph: lat(42, 880.1, 0.8302, 0.033) },
      system: { timings_s: {}, process_rss_mb: 812, graph: { initial: { nodes: 59, edges: 52 }, adaptive: { nodes: 62, edges: 56, co_access_edges: 1 } }, embedding_cache_mb: 7.6, qdrant_storage: "in-memory (no on-disk size)" },
      comparisons: [cmp("adaptive_vs_fixed", "fixed_graph", "adaptive_graph", 0.009, -0.004, 0.022), cmp("fixed_vs_global", "global_dense", "fixed_graph", -0.033, -0.05, -0.015)],
      adaptation: { events: o.events ?? [ev("completed"), ev("rejected"), ev("rolled_back", { trigger: "safety_probe" })], accepted: 1, rejected: 1, failed: 0, rolled_back: 1, proposals_evaluated: 3, initial_version: "arch_0002", adaptive_version: "arch_0005" },
      safety: { harmful_candidate: { status: "rejected", rejection_stage: "pre_commit", decision_reason: "x", state_unchanged: true, version_unchanged: true, baseline_ndcg_at_10: 0.55, candidate_ndcg_at_10: 0.12 },
        post_commit_fault: { status: "rolled_back", recovered: true, state_restored_exactly: true, integrity_passed: true }, restoration: { restore_status: "completed", state_matches_pre_change_snapshot: true, restored_again: "completed", state_matches_changed_architecture: true } },
    },
  },
})
const job = (o = {}) => ({ run_id: "beir_run1", status: "completed", created_at: "2026-10-25T00:00:00Z", started_at: null, finished_at: null, datasets: { scifact: { status: "completed" } }, error: null, official: true, ...o })
const datasets = { items: [
  { dataset_id: "scifact", cached: true, path: "p", domain: "scientific claim verification", license: "CC BY-NC 2.0", source_url: "u", corpus_size: 5183, size_mb: 8, splits: { train: { queries: 809, qrels: 919 }, test: { queries: 300, qrels: 339 } } },
  { dataset_id: "nfcorpus", cached: false, path: "p", domain: "biomedical", license: "nc", source_url: "u2" },
  { dataset_id: "arguana", cached: false, path: "p", domain: "argument retrieval", license: "cc", source_url: "u3" }], data_dir: "d", methods: ["bm25", "global_dense", "fixed_graph", "adaptive_graph"], note: "" }
const base = (extra = {}) => ({
  "GET /api/v1/benchmarks/datasets": datasets, "GET /api/v1/benchmarks/status": { running: false, active_run: null, last_run: job() },
  "GET /api/v1/benchmarks/runs": { items: [job()] }, "GET /api/v1/benchmarks/runs/beir_run1/results": results(),
  "GET /api/v1/benchmarks/runs/beir_run1/compare": cmp("adaptive_vs_fixed", "fixed_graph", "adaptive_graph", 0.009, -0.004, 0.022), ...extra,
})

describe("benchmark page — results", () => {
  it("renders the actual server-reported metrics with nDCG@10 as the headline, and differences against the stated baselines", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    const table = await screen.findByRole("table", { name: "Retrieval quality by method" })
    expect(within(table).getByText("nDCG@10 ★")).toBeInTheDocument()
    const rowOf = (name: string) => within(table).getByText(name).closest("tr")!
    expect(rowOf("BM25")).toHaveTextContent("0.5500")
    expect(rowOf("Global dense")).toHaveTextContent("0.6450")
    expect(rowOf("Fixed-graph MORPHE")).toHaveTextContent("0.6120")
    expect(rowOf("Adaptive-graph MORPHE")).toHaveTextContent("0.6210")
    expect(rowOf("Adaptive graph − fixed graph")).toHaveTextContent("+0.0090")
    expect(rowOf("Fixed graph − global dense")).toHaveTextContent("-0.0330")
    for (const h of ["MAP@10", "Recall@5", "Recall@10", "Recall@100", "MRR@10", "Precision@10"]) expect(within(table).getByText(h)).toBeInTheDocument()
    expect(screen.getByText("official test split")).toBeInTheDocument()
    expect(screen.getByText(/corpus 5,183 docs · 300 test queries · 339 judgments/)).toBeInTheDocument()
  })
  it("reports latency, candidate pool reduction and fallback separately, without implying fewer candidates means faster", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    const t = await screen.findByRole("table", { name: "Latency and candidate pool by method" })
    const fixed = within(t).getByText("Fixed-graph MORPHE").closest("tr")!
    expect(fixed).toHaveTextContent("41.0 ms") // p50 e2e
    expect(fixed).toHaveTextContent("905.5")
    expect(fixed).toHaveTextContent("82.5%")
    expect(fixed).toHaveTextContent("3.3%")
    expect(within(t).getByText("BM25").closest("tr")).toHaveTextContent("—") // no query embedding for BM25
    expect(screen.getByText(/not a count of vectors the engine scanned/)).toBeInTheDocument()
    expect(screen.getByText(/Fewer candidates do not by themselves mean faster/)).toBeInTheDocument()
  })
  it("shows paired bootstrap intervals with an honest explanation", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    const t = await screen.findByRole("table", { name: "Paired bootstrap comparisons" })
    const row = within(t).getByText("Adaptive − fixed graph").closest("tr")!
    expect(row).toHaveTextContent("+0.0090 [-0.0040, +0.0220]")
    expect(row).toHaveTextContent("12 / 4 / 284")
    expect(screen.getByText(/contains 0 means this sample cannot distinguish/)).toBeInTheDocument()
    expect(screen.getByText(/1,000 resamples, seed 13, resampling unit = query/)).toBeInTheDocument()
  })
  it("shows adaptation evidence with distinct statuses, safety evidence and a per-query inspector", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    const list = await screen.findByRole("list", { name: "Adaptation events" })
    expect(within(list).getByText("Completed")).toBeInTheDocument()
    expect(within(list).queryByText("Rejected")).toBeNull() // rejected proposals are collapsed by default
    await userEvent.click(screen.getByRole("button", { name: /Show 1 rejected proposals/ }))
    expect(within(list).getByText("Rejected")).toBeInTheDocument()
    expect(within(list).getByText("Rolled Back")).toBeInTheDocument()
    expect(within(list).getByText("rejected before commit")).toBeInTheDocument()
    expect(within(list).getAllByText(/validation nDCG@10 0.6100 → 0.6200/).length).toBe(3)
    const safety = screen.getByTestId("safety-evidence")
    expect(safety).toHaveTextContent("Harmful candidate")
    expect(safety).toHaveTextContent("state unchanged: true")
    expect(safety).toHaveTextContent("state restored exactly: true")
    expect(await screen.findByText("does vitamin d reduce fractures")).toBeInTheDocument()
    expect(screen.getByText("statins and breast cancer survival")).toBeInTheDocument()
  })
  it("labels smoke and internal-split results as non-official and shows dataset caveats", async () => {
    mockApi(base({ "GET /api/v1/benchmarks/runs/beir_run1/results": results({ manifest: { smoke: true, official_test_split: false, caveats: ["5 judged document ids are not in the official corpus"] } }) }))
    renderWith(<Benchmarks />)
    expect(await screen.findByText(/SMOKE — not an official result/)).toBeInTheDocument()
    expect(screen.getByText(/5 judged document ids are not in the official corpus/)).toBeInTheDocument()
  })
  it("offers JSON/CSV export and artifact downloads pointing at the API", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    await screen.findByRole("table", { name: "Retrieval quality by method" })
    expect(screen.getByRole("link", { name: /JSON/ })).toHaveAttribute("href", "/api/v1/benchmarks/runs/beir_run1/export?format=json")
    expect(screen.getByRole("link", { name: /CSV/ })).toHaveAttribute("href", "/api/v1/benchmarks/runs/beir_run1/export?format=csv")
    expect(screen.getByRole("link", { name: "manifest.json" })).toHaveAttribute("href", "/api/v1/benchmarks/runs/beir_run1/artifacts/scifact/manifest.json")
  })
  it("shows the failure of a dataset instead of pretending it completed", async () => {
    mockApi(base({ "GET /api/v1/benchmarks/runs/beir_run1/results": results({ manifest: { status: "failed", error: "ValueError: duplicate document ids" } }) }))
    renderWith(<Benchmarks />)
    expect(await screen.findByText(/scifact: failed — ValueError: duplicate document ids/)).toBeInTheDocument()
  })
})

describe("benchmark page — states and run setup", () => {
  it("shows an empty state when no benchmark has run", async () => {
    mockApi(base({ "GET /api/v1/benchmarks/runs": { items: [] } }))
    renderWith(<Benchmarks />)
    expect(await screen.findByText("No benchmark has been run yet")).toBeInTheDocument()
  })
  it("shows an error with retry when results cannot be loaded", async () => {
    mockApi(base({ "GET /api/v1/benchmarks/runs/beir_run1/results": { __status: 500, __error: { code: "internal_error", message: "results unavailable" } } }))
    renderWith(<Benchmarks />)
    expect(await screen.findByText("results unavailable")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: /retry/i }).length).toBeGreaterThan(0)
  })
  it("validates unavailable and incompatible configurations before enabling the start button", async () => {
    mockApi(base())
    renderWith(<Benchmarks />)
    const start = await screen.findByRole("button", { name: /start benchmark/i })
    expect(start).toBeEnabled() // scifact is cached, all methods selected
    await userEvent.click(screen.getByLabelText("dataset nfcorpus"))
    expect(start).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("Not cached locally: nfcorpus")
    await userEvent.click(screen.getByLabelText("allow download"))
    expect(start).toBeEnabled()
    await userEvent.click(screen.getByLabelText("method fixed_graph"))
    expect(start).toBeDisabled()
    expect(screen.getByRole("alert")).toHaveTextContent("select both")
  })
  it("starts a benchmark with the chosen configuration and marks a query limit as a smoke run", async () => {
    const api = mockApi(base({ "POST /api/v1/benchmarks/run": job({ run_id: "beir_new", status: "queued", official: false }) }))
    renderWith(<Benchmarks />)
    await userEvent.type(await screen.findByLabelText(/Query limit/), "20")
    expect(screen.getByRole("note")).toHaveTextContent("smoke run")
    await userEvent.click(screen.getByLabelText("method bm25"))
    await userEvent.click(screen.getByRole("button", { name: /start benchmark/i }))
    await waitFor(() => expect(api.called("POST", "/api/v1/benchmarks/run")).toHaveLength(1))
    expect(api.called("POST", "/api/v1/benchmarks/run")[0].body).toMatchObject({ datasets: ["scifact"], methods: ["global_dense", "fixed_graph", "adaptive_graph"], query_limit: 20, split: "test", download: false })
    expect(await screen.findByText("Benchmark beir_new queued")).toBeInTheDocument()
  })
  it("surfaces a server rejection (dataset missing) and shows live status while a job runs", async () => {
    mockApi(base({ "POST /api/v1/benchmarks/run": { __status: 404, __error: { code: "dataset_not_available", message: "dataset(s) ['x'] are not cached" } } }))
    renderWith(<Benchmarks />)
    await userEvent.click(await screen.findByRole("button", { name: /start benchmark/i }))
    expect((await screen.findAllByText(/are not cached/)).length).toBeGreaterThan(0)
  })
  it("shows live server job status and disables starting another run", async () => {
    const running = job({ status: "running", started_at: "2026-10-25T00:00:00Z", datasets: { scifact: { status: "running", phase: "adaptation", message: "window 2/6: 39 proposals, 1 applied" } } })
    mockApi(base({ "GET /api/v1/benchmarks/status": { running: true, active_run: running, last_run: running }, "GET /api/v1/benchmarks/runs": { items: [running] } }))
    renderWith(<Benchmarks />)
    expect(await screen.findByText(/adaptation: window 2\/6/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /benchmark running/i })).toBeDisabled()
  })
})
