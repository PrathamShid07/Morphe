import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { API, api } from "./client"
import type {
  AdaptationEvent, AdaptationStatus, AppConfig, Candidates, CompressionEvent, CompressionRun, CompressionStatus, EvalDataset,
  EvalRun, GraphData, IngestionResponse, Memory, MetricsHistory, MetricsSummary, ModeComparison, ModuleDetail, ModuleInfo,
  Page, QueryMode, QueryResponse, RestorePreview, RestoreResult, ScenarioInfo, StrategyComparison, StrategyGroup, StrategyGroupResult,
  StrategyRow, StrategyRunDetail, StrategyRunSummary, VersionCompare, VersionDetail, VersionManifest,
} from "./types"

/** Server state is the only source of truth: after any successful mutation everything is refetched. */
const POLL = { fast: 4000, normal: 10000 }

export const useHealth = () =>
  useQuery({ queryKey: ["health"], queryFn: () => api.health(), refetchInterval: 15000, retry: false })
export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => api.get<AppConfig>("/config"), staleTime: 60000 })
export const useSummary = () =>
  useQuery({ queryKey: ["summary"], queryFn: () => api.get<MetricsSummary>("/metrics/summary"), refetchInterval: POLL.normal })
export const useMetricsHistory = (window: string) =>
  useQuery({ queryKey: ["history", window], queryFn: () => api.get<MetricsHistory>("/metrics/history", { window }), refetchInterval: 30000 })
export const useCompressionStatus = () =>
  useQuery({ queryKey: ["compression", "status"], queryFn: () => api.get<CompressionStatus>("/compression/status"), refetchInterval: POLL.fast })
export const useCompressionEvents = (scope?: string, limit = 50) =>
  useQuery({
    queryKey: ["compression", "events", scope, limit],
    queryFn: () => api.get<Page<CompressionEvent>>("/compression/events", { scope, limit }),
    refetchInterval: POLL.normal,
  })
export const useAdaptationStatus = () =>
  useQuery({ queryKey: ["adaptation", "status"], queryFn: () => api.get<AdaptationStatus>("/adaptation/status"), refetchInterval: POLL.fast })
export const useCandidates = () =>
  useQuery({ queryKey: ["adaptation", "candidates"], queryFn: () => api.get<Candidates>("/adaptation/candidates"), refetchInterval: 20000 })
export const useAdaptationEvents = (status?: string, limit = 50) =>
  useQuery({
    queryKey: ["adaptation", "events", status, limit],
    queryFn: () =>
      api.get<Page<AdaptationEvent> & { status_counts: Record<string, number> }>("/adaptation/events", { status: status === "all" ? undefined : status, limit }),
    refetchInterval: POLL.fast,
  })
export const useGraph = () => useQuery({ queryKey: ["graph"], queryFn: () => api.get<GraphData>("/graph"), refetchInterval: 20000 })
export const useModules = () => useQuery({ queryKey: ["modules"], queryFn: () => api.get<{ items: ModuleInfo[] }>("/modules") })
export const useModule = (id: string | null) =>
  useQuery({ queryKey: ["module", id], queryFn: () => api.get<ModuleDetail>(`/modules/${id}`), enabled: !!id })
export interface MemoryFilters {
  module_id?: string
  source_id?: string
  status?: string
  search?: string
  pinned?: boolean
  limit: number
  offset: number
}
export const useMemories = (f: MemoryFilters) =>
  useQuery({ queryKey: ["memories", f], queryFn: () => api.get<Page<Memory>>("/memories", f as unknown as Record<string, string>), placeholderData: keepPreviousData })
export const useMemory = (id: string | null) =>
  useQuery({ queryKey: ["memory", id], queryFn: () => api.get<Memory>(`/memories/${id}`), enabled: !!id })
export const useSources = () =>
  useQuery({ queryKey: ["sources"], queryFn: () => api.get<Page<{ source_id: string; source_name: string; chunk_count: number }>>("/sources", { limit: 200 }) })
export const useVersions = () =>
  useQuery({
    queryKey: ["versions"],
    queryFn: () => api.get<Page<VersionManifest> & { active_version: string | null }>("/versions", { limit: 100 }),
    refetchInterval: POLL.normal,
  })
export const useVersion = (id: string | null) =>
  useQuery({ queryKey: ["version", id], queryFn: () => api.get<VersionDetail>(`/versions/${id}`), enabled: !!id })
export const useRestorePreview = (id: string | null) =>
  useQuery({ queryKey: ["restore-preview", id], queryFn: () => api.get<RestorePreview>(`/versions/${id}/restore-preview`), enabled: !!id, retry: false })
export const useEvalDataset = () => useQuery({ queryKey: ["eval", "dataset"], queryFn: () => api.get<EvalDataset>("/evaluation/dataset") })
export const useEvalRuns = () =>
  useQuery({ queryKey: ["eval", "runs"], queryFn: () => api.get<Page<EvalRun>>("/evaluation/runs", { limit: 30 }) })
export const useScenarios = (enabled: boolean) =>
  useQuery({ queryKey: ["scenarios"], queryFn: () => api.get<{ scenarios: ScenarioInfo[] }>("/admin/demo/scenarios"), enabled })

/** Invalidate everything that can change after a successful write. */
function useRefreshAll() {
  const qc = useQueryClient()
  return () => qc.invalidateQueries()
}

export function useIngest() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (body: { source_name: string; text: string; metadata?: Record<string, unknown>; tags?: string[]; pinned?: boolean }) =>
      api.post<IngestionResponse>("/ingestion", body),
    onSuccess: refresh,
  })
}
export function useIngestFile() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { file: File; topic?: string }) => {
      const fd = new FormData()
      fd.append("file", v.file)
      if (v.topic) fd.append("topic", v.topic)
      return api.upload<IngestionResponse>("/ingestion/file", fd)
    },
    onSuccess: refresh,
  })
}
export function useQuery_() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { query: string; top_k: number; debug_trace: boolean; mode: QueryMode }) => api.post<QueryResponse>("/query", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["summary"] })
      qc.invalidateQueries({ queryKey: ["adaptation"] })
    },
  })
}
export function useDeleteMemory() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { id: string; force?: boolean }) => api.del<{ memory_id: string; active_tokens_reclaimed: number }>(`/memories/${v.id}`, { force: v.force }),
    onSuccess: refresh,
  })
}
export function usePatchMemory() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { id: string; pinned?: boolean; importance?: string }) => api.patch<Memory>(`/memories/${v.id}`, { pinned: v.pinned, importance: v.importance }),
    onSuccess: refresh,
  })
}
export function useRunCompression() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { mode: "auto" | "normal" | "emergency"; dry_run: boolean }) => api.post<CompressionRun>("/compression/run", v),
    onSettled: refresh,
  })
}
export function useSetBudget() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: (budget_tokens: number | null) => api.put<CompressionStatus>("/compression/budget", { budget_tokens }), onSuccess: refresh })
}
export function useAdaptationAction() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { kind: "evaluate" | "apply"; operation: string; module_ids: string[]; validation_override?: boolean }) =>
      api.post<AdaptationEvent>(`/adaptation/${v.kind}`, { operation: v.operation, module_ids: v.module_ids, validation_override: !!v.validation_override }),
    onSettled: refresh,
  })
}
export function useApplyEvent() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: (eventId: string) => api.post<AdaptationEvent>(`/adaptation/events/${eventId}/apply`), onSettled: refresh })
}
export function useRunWindow() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: () => api.post<{ applied: number; proposals_detected: number }>("/adaptation/run-window"), onSettled: refresh })
}
export function useMonitor() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: () => api.post<{ status: string; reason?: string }>("/adaptation/monitor"), onSettled: refresh })
}
export function useRestore() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { id: string; reason?: string }) => api.post<RestoreResult>(`/versions/${v.id}/restore`, { reason: v.reason }),
    onSettled: refresh,
  })
}
export function useCheckpoint() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: () => api.post<VersionManifest>("/versions/checkpoint"), onSuccess: refresh })
}
export function useCompareVersions() {
  return useMutation({
    mutationFn: (v: { from_version: string; to_version: string }) =>
      api.get<VersionCompare>("/versions/compare", { ...v, evaluate: true }),
  })
}
export function useRunEvaluation() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { mode: "graph" | "global"; version_id?: string }) => api.post<EvalRun>("/evaluation/run", v),
    onSuccess: refresh,
  })
}
export function useCompareModes() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: () => api.post<ModeComparison>("/evaluation/compare-modes"), onSuccess: refresh })
}
export function useReloadDataset() {
  const refresh = useRefreshAll()
  return useMutation({ mutationFn: () => api.post<EvalDataset>("/evaluation/dataset/load"), onSuccess: refresh })
}
export function useDemo() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { action: "seed" | "reset" } | { action: "scenario"; name: string }) => {
      if (v.action === "scenario") return api.post<unknown>(`/admin/demo/scenario/${v.name}`)
      if (v.action === "seed") return api.post<unknown>("/admin/demo/seed")
      return api.post<unknown>("/admin/reset", { confirm: "RESET" })
    },
    onSettled: refresh,
  })
}

// ------------------------------------------------------------------ BEIR benchmark
import type { BenchmarkComparison, BenchmarkDatasets, BenchmarkJob, BenchmarkMethod, BenchmarkResults, BenchmarkRunDetail, BenchmarkStatus } from "./types"

export const useBenchmarkDatasets = () => useQuery({ queryKey: ["bench", "datasets"], queryFn: () => api.get<BenchmarkDatasets>("/benchmarks/datasets") })
export const useBenchmarkStatus = () =>
  useQuery({ queryKey: ["bench", "status"], queryFn: () => api.get<BenchmarkStatus>("/benchmarks/status"), refetchInterval: (q) => (q.state.data?.running ? 2000 : 8000) })
export const useBenchmarkRuns = () =>
  useQuery({ queryKey: ["bench", "runs"], queryFn: () => api.get<{ items: BenchmarkJob[] }>("/benchmarks/runs"), refetchInterval: 8000 })
export const useBenchmarkResults = (runId: string | null) =>
  useQuery({ queryKey: ["bench", "results", runId], queryFn: () => api.get<BenchmarkResults>(`/benchmarks/runs/${runId}/results`), enabled: !!runId })
export const useBenchmarkRun = (runId: string | null) =>
  useQuery({ queryKey: ["bench", "run", runId], queryFn: () => api.get<BenchmarkRunDetail>(`/benchmarks/runs/${runId}`), enabled: !!runId })
export const useBenchmarkCompare = (runId: string | null, dataset: string | null, baseline: BenchmarkMethod, treatment: BenchmarkMethod, enabled: boolean) =>
  useQuery({
    queryKey: ["bench", "compare", runId, dataset, baseline, treatment],
    queryFn: () => api.get<BenchmarkComparison>(`/benchmarks/runs/${runId}/compare`, { dataset, baseline, treatment }),
    enabled: !!runId && !!dataset && enabled, retry: false,
  })
export interface BenchmarkRunRequest {
  datasets: string[]; methods: BenchmarkMethod[]; query_limit?: number | null; download?: boolean; force_embeddings?: boolean; max_windows?: number; safety_probe?: boolean
}
export function useStartBenchmark() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: BenchmarkRunRequest) => api.post<BenchmarkJob>("/benchmarks/run", { split: "test", ...body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bench"] }),
  })
}
export const benchmarkUrl = (path: string) => `${API}/benchmarks${path}`


// ------------------------------------------------------------------------------------------ strategy comparison + strategy runs
export function useCompareStrategies() {
  return useMutation({
    mutationFn: (body: { query?: string; query_id?: string; top_k: number; modes?: string[] }) => api.post<StrategyComparison>("/evaluation/compare-strategies", body),
  })
}
export const useStrategyRuns = () =>
  useQuery({ queryKey: ["strategies", "runs"], queryFn: () => api.get<{ items: StrategyRunSummary[] }>("/benchmarks/strategies/runs"), refetchInterval: 15000 })
export const useStrategyRun = (runId: string | null) =>
  useQuery({ queryKey: ["strategies", "run", runId], queryFn: () => api.get<StrategyRunDetail>(`/benchmarks/strategies/runs/${runId}`), enabled: !!runId })
export const useStrategyGroup = (runId: string | null, dataset: string | null, group: StrategyGroup) =>
  useQuery({
    queryKey: ["strategies", "group", runId, dataset, group],
    queryFn: () => api.get<StrategyGroupResult>(`/benchmarks/strategies/runs/${runId}/${dataset}/${group}`),
    enabled: !!runId && !!dataset, retry: false,
  })
export interface StrategyRowsParams { sort: string; order: "worst" | "best"; only: string; search: string; limit: number; offset: number }
export const useStrategyRows = (runId: string | null, dataset: string | null, group: StrategyGroup, p: StrategyRowsParams) =>
  useQuery({
    queryKey: ["strategies", "rows", runId, dataset, group, p],
    queryFn: () => api.get<{ total: number; limit: number; offset: number; items: StrategyRow[] }>(`/benchmarks/strategies/runs/${runId}/${dataset}/${group}/rows`, { ...p, search: p.search || undefined }),
    enabled: !!runId && !!dataset, placeholderData: keepPreviousData, retry: false,
  })
export const strategyExportUrl = (runId: string, format: "json" | "csv") => `${API}/benchmarks/strategies/runs/${runId}/export?format=${format}`
