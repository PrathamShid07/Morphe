import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { API, api } from "./client"
import { useAuth } from "@/lib/auth"
import type {
  AdaptationEvent, AdaptationStatus, AppConfig, Candidates, CompressionEvent, CompressionRun, CompressionStatus, EvalDataset,
  EvalRun, GraphData, IngestionResponse, Memory, MetricsHistory, MetricsSummary, ModeComparison, ModuleDetail, ModuleInfo,
  Page, QueryMode, QueryResponse, RestorePreview, RestoreResult, ScenarioInfo, StrategyComparison, StrategyGroup, StrategyGroupResult,
  StrategyRow, StrategyRunDetail, StrategyRunSummary, VersionCompare, VersionDetail, VersionManifest, QueryHistoryItem, StoredQuery,
  AdminUser, AdminUserQuery, AuditEvent, MyMemory, MySummary, PersonalQueryResponse, SaveMemoryResult,
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
/** Graph and module data come from the administrator endpoints for an administrator and from the signed-in user's own scoped /me endpoints otherwise. */
const useScope = () => (useAuth().isAdminView ? "admin" : "user")

/** The visitor endpoints omit operational fields; give them neutral values so the shared components keep one shape (the UI hides those fields in read-only mode). */
const asModuleInfo = (m: Partial<ModuleInfo> & Pick<ModuleInfo, "module_id" | "name">): ModuleInfo => ({
  parent_id: null, children: [], record_count: 0, is_container: false, keywords: [], description: "", token_count: 0, total_access_count: 0,
  last_accessed_at: null, shortcut_edges: 0, created_at: null, last_adapted_at: null, last_change_op: null, profile_status: "indexed", ...m,
})
export const useGraph = () => {
  const scope = useScope()
  return useQuery({ queryKey: ["graph", scope], queryFn: () => api.get<GraphData>(scope === "admin" ? "/graph" : "/me/graph"), refetchInterval: 20000 })
}
export const useModules = () => {
  const scope = useScope()
  return useQuery({
    queryKey: ["modules", scope],
    queryFn: async () => {
      const r = await api.get<{ items: ModuleInfo[] }>(scope === "admin" ? "/modules" : "/me/modules")
      return scope === "admin" ? r : { items: r.items.map(asModuleInfo) }
    },
  })
}
export const useModule = (id: string | null) => {
  const scope = useScope()
  return useQuery({
    queryKey: ["module", scope, id],
    queryFn: async () => {
      const d = await api.get<ModuleDetail>(scope === "admin" ? `/modules/${id}` : `/me/modules/${id}`)
      return scope === "admin" ? d : { ...asModuleInfo(d), architecture_version: null, parent: d.parent ? asModuleInfo(d.parent) : null, neighbors: d.neighbors ?? [], memories: [], adaptation_history: [], queries_touching_module: 0 }
    },
    enabled: !!id,
  })
}
/** The signed-in user's own recent questions (server-side, scoped to their account). */
export const useMyQueries = () => useQuery({ queryKey: ["me", "queries"], queryFn: () => api.get<{ items: QueryHistoryItem[] }>("/me/queries", { limit: 10 }) })
export const fetchStoredQuery = (id: string) => api.get<StoredQuery>(`/me/queries/${id}`)
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
    mutationFn: (body: { source_name: string; text: string; metadata?: Record<string, unknown>; tags?: string[]; pinned?: boolean; visibility?: "shared" | "restricted" }) =>
      api.post<IngestionResponse>("/ingestion", body),
    onSuccess: refresh,
  })
}
export function useIngestFile() {
  const refresh = useRefreshAll()
  return useMutation({
    mutationFn: (v: { file: File; topic?: string; visibility?: "shared" | "restricted" }) => {
      const fd = new FormData()
      fd.append("file", v.file)
      if (v.topic) fd.append("topic", v.topic)
      if (v.visibility) fd.append("visibility", v.visibility)
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
      qc.invalidateQueries({ queryKey: ["me", "queries"] })
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


// ---------------------------------------------------------------------------------------------- the signed-in user's own space
/** Ask a question inside the caller's own scope (their private memories + published shared knowledge). */
export function useAskMyMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { query: string; top_k: number; debug_trace: boolean; mode: QueryMode }) => api.post<PersonalQueryResponse>("/me/query", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me", "queries"] })
      qc.invalidateQueries({ queryKey: ["graph"] })  // retrieval can add personal co-access shortcuts
    },
  })
}
export const useMySummary = () => useQuery({ queryKey: ["me", "summary"], queryFn: () => api.get<MySummary>("/me/summary") })
export const useMyMemories = (limit = 5) =>
  useQuery({ queryKey: ["me", "memories", limit], queryFn: () => api.get<{ items: MyMemory[]; total: number }>("/me/memories", { limit }) })
export function useSaveMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { text: string; title?: string; topic?: string; group?: string }) => api.post<SaveMemoryResult>("/me/memories", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] })
      qc.invalidateQueries({ queryKey: ["graph"] })
      qc.invalidateQueries({ queryKey: ["modules"] })
    },
  })
}
export function useDeleteMyMemory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.del<{ memory_id: string }>(`/me/memories/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] })
      qc.invalidateQueries({ queryKey: ["graph"] })
      qc.invalidateQueries({ queryKey: ["modules"] })
    },
  })
}

// ---------------------------------------------------------------------------------------------- administrator: users and activity
export interface UserFilters { search?: string; status?: string; active_after?: string; active_before?: string; limit?: number; offset?: number }
export const useAdminUsers = (f: UserFilters) =>
  useQuery({ queryKey: ["admin", "users", f], queryFn: () => api.get<Page<AdminUser>>("/admin/users", { ...f }), placeholderData: keepPreviousData, refetchInterval: 20000 })
export const useAdminUser = (id: string | null) =>
  useQuery({ queryKey: ["admin", "user", id], queryFn: () => api.get<AdminUser>(`/admin/users/${id}`), enabled: !!id })
export const useAdminUserQueries = (id: string | null) =>
  useQuery({ queryKey: ["admin", "user", id, "queries"], queryFn: () => api.get<Page<AdminUserQuery>>(`/admin/users/${id}/queries`, { limit: 10 }), enabled: !!id })
export interface ActivityFilters { user_id?: string; action?: string; status?: string; target_type?: string; role?: string; since?: string; until?: string; search?: string; limit?: number; offset?: number }
export const useActivity = (f: ActivityFilters) =>
  useQuery({ queryKey: ["admin", "activity", f], queryFn: () => api.get<Page<AuditEvent>>("/admin/activity", { ...f }), placeholderData: keepPreviousData, refetchInterval: 15000 })
export const useActivityActions = () =>
  useQuery({ queryKey: ["admin", "activity-actions"], queryFn: () => api.get<{ items: string[] }>("/admin/activity/actions"), staleTime: 60000 })
export function useSetUserStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; status: "active" | "disabled"; reason?: string }) => api.post<AdminUser>(`/admin/users/${v.id}/status`, { status: v.status, reason: v.reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin"] }),
  })
}
