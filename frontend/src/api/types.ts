/** Response shapes of the MORPHE REST API (see docs/API.md). Fields that can be unmeasured are `| null`. */

export interface Metrics {
  sample_size: number
  recall_at_5: number | null
  mrr: number | null
  guided_recall_at_5: number | null
  guided_mrr: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  mean_candidate_count: number | null
  fallback_rate: number | null
}

export interface MetricsSummary {
  active_memory_count: number
  source_count: number
  module_count: number
  node_count: number
  edge_count: number
  active_tokens: number
  budget_tokens: number
  usage_percent: number
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  latency_samples: number
  latency_note: string | null
  average_candidate_count: number | null
  fallback_rate: number | null
  process_rss_mb: number
  qdrant_storage_mb: number | null
  retrieval_quality: { run_id: string; evaluated_at: string; metrics: Metrics; sample_size: number; mode: string; architecture_version?: string } | null
  retrieval_quality_note: string | null
  architecture_version: string | null
  thresholds: { compression_start_pct: number; emergency_cleanup_pct: number }
}

export interface HistoryPoint {
  timestamp: string
  active_tokens: number
  usage_percent: number
  record_count: number
  module_count: number
  edge_count: number
  process_rss_mb: number
  qdrant_storage_mb: number | null
}
export interface QueryPoint {
  timestamp: string
  queries: number
  p95_latency_ms: number | null
  mean_latency_ms: number | null
  mean_candidate_count: number | null
  fallback_rate: number | null
}
export interface EvalPoint {
  timestamp: string
  recall_at_5: number | null
  mrr: number | null
  p95_latency_ms: number | null
  sample_size: number
  architecture_version: string | null
  mode: string
}
export interface MetricsHistory {
  window: string
  points: HistoryPoint[]
  query_points: QueryPoint[]
  evaluation_points: EvalPoint[]
  note: string | null
}

export interface Memory {
  memory_id: string
  module_id: string
  module_name?: string | null
  source_id: string
  source_name: string
  section?: string | null
  status: "active" | "superseded" | "deleted" | "pending"
  token_count: number
  access_count: number
  last_accessed_at: string | null
  importance: "low" | "normal" | "high" | "critical"
  pinned: boolean
  created_at: string
  text_preview?: string
  text?: string | null
  summary_of: string[]
  superseded_by?: string | null
  merged_source_ids?: string[]
  kind?: string
  summary_sources?: { memory_id: string; source_name: string; status: string; text_preview: string }[]
}
export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface ModuleInfo {
  module_id: string
  name: string
  parent_id: string | null
  children: string[]
  record_count: number
  is_container: boolean
  keywords: string[]
  description: string
  token_count: number
  total_access_count: number
  last_accessed_at: string | null
  shortcut_edges: number
  created_at: string | null
  last_adapted_at: string | null
  last_change_op: string | null
  profile_status: string
  capacity_threshold?: number
  utilization?: number | null
}
export interface ModuleDetail extends ModuleInfo {
  architecture_version?: string | null
  parent: ModuleInfo | null
  neighbors: { module_id: string; name: string | null; relation_type: string; weight: number; co_access_count: number; direction: string; record_count: number }[]
  memories: Memory[]
  adaptation_history: AdaptationEvent[]
  queries_touching_module: number
}

export interface GraphNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: { label: string; recordCount: number; parentId: string | null; isContainer: boolean; lastChangeOp: string | null; lastChangeAt: string | null; utilization?: number | null }
}
export interface GraphEdge {
  id: string
  source: string
  target: string
  label: string
  data: { relationType: "hierarchy" | "co_access"; weight: number; coAccessCount: number; createdAt: string | null; evidence?: Record<string, unknown>; operationId?: string | null }
}
export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  architecture_version: string | null
  counts: { nodes: number; edges: number }
  capacity_threshold?: number
}

export interface QueryResult {
  memory_id: string
  text: string
  score: number
  module_id: string
  module_name: string | null
  source_name: string | null
  section?: string | null
}
export type QueryMode = "graph" | "global" | "progressive" | "soft"
export interface ProgressiveStage {
  stage: number
  kind: "seed" | "expansion"
  policy: string
  modules: { module_id: string; name: string; profile_rank: number; profile_score: number; records: number }[]
  new_candidates: number
  cumulative_candidates: number
  hits_returned: number
  best_score: number | null
  kth_score: number | null
  top_k_changed: boolean | null
  new_in_top_k: number | null
  search_ms: number
  decision: string
}
export interface SoftResultTrace {
  memory_id: string; module_id: string; module_name: string; dense_score: number; dense_rank: number
  profile_score: number | null; profile_rank: number | null; profile_component: number | null
  graph_distance: number | null; graph_component: number; prior: number; prior_centered: number; score_shift: number
  final_score: number; final_rank: number; change: "promoted" | "demoted" | "unchanged"; reason: string
}
export interface Trace {
  mode: string
  strategy?: string
  embedding_ms?: number | null
  timings_ms?: Record<string, number>
  searched_modules?: { module_id: string; name: string; stage: number | null; origin: string; records: number }[]
  result_modules?: string[]
  stages?: ProgressiveStage[]
  progressive?: { stop_reason: string; stop_explanation: string; stages_run: number; modules_searched: number; modules_total: number; policy: string; stability_k: number; patience: number; fallback_on_budget: boolean; unsettled_at_stop: boolean; budget_modules: number; max_steps: number }
  soft?: { weight: number; graph_share: number; pool_size: number; seed_modules: string[]; graph_depth: number; min_coaccess_observations: number; pool_mean_prior: number; formula: string; promoted: number; demoted: number; unchanged: number; top_k_membership_changed: boolean; full_global_search: boolean; results: SoftResultTrace[] }
  seed_modules: { module_id: string; name: string; score: number }[]
  candidate_modules: { module_id: string; name: string; origin: string; via: string | null; via_name: string | null; record_count: number }[]
  expanded_modules: string[]
  candidate_count: number
  guided_candidate_count: number
  candidate_count_definition: string
  fallback_used: boolean
  fallback_reason: string | null
  guided_result_ids: string[]
  latency_ms: number
  architecture_version: string | null
}
export interface QueryResponse {
  query_id: string
  architecture_version: string | null
  top_k: number
  results: QueryResult[]
  latency_ms: number
  mode: string
  trace?: Trace | null
}

export interface IngestionResponse {
  source_id: string
  status: "completed" | "duplicate"
  chunks_created: number
  module_ids: string[]
  new_module_ids: string[]
  tokens_added: number
  active_tokens: number
  usage_percent: number
  operation_id?: string | null
  message?: string | null
  compression?: { triggered: boolean; state: string; reason?: string; mode?: string } | null
}

export interface CompressionStatus {
  active_tokens: number
  active_memories: number
  budget_tokens: number
  usage_percent: number
  budget_overridden: boolean
  start_threshold_percent: number
  emergency_threshold_percent: number
  cleanup_target_percent: number
  normal_target_percent: number
  state: "idle" | "queued" | "running" | "completed" | "failed"
  band: "normal" | "pressure" | "emergency"
  last_run_status: string | null
  last_run: { job_id: string; mode: string; status: string; tokens_before?: number; tokens_after?: number; tokens_reclaimed?: number; at?: string; error?: string } | null
  cooldown_remaining_seconds: number
  mode: string
  min_summary_saving_percent: number
  storage_note: string
}
export interface CompressionEvent {
  event_id: string
  operation: string
  scope: "job" | "operation"
  job_id?: string
  mode?: string
  trigger: string
  reason: string
  status: "running" | "completed" | "failed" | "skipped" | "simulated"
  timestamp: string
  finished_at: string | null
  tokens_before: number | null
  tokens_after: number | null
  tokens_reclaimed: number | null
  usage_before_pct?: number
  usage_after_pct?: number
  source_memory_ids?: string[]
  source_count?: number
  output_memory_id?: string | null
  provider?: string | null
  error?: string | null
  duplicates_removed?: number
  summaries_created?: number
  records_deleted?: number
  target_reached?: boolean
}
export interface CompressionRun {
  job_id: string
  mode: string
  status: string
  dry_run: boolean
  tokens_before: number
  tokens_after: number
  tokens_reclaimed: number
  usage_before_pct: number
  usage_after_pct: number
  duplicates_removed: number
  summaries_created: number
  records_deleted: number
  target_reached: boolean
  operations: { event_id: string; operation: string; status: string; tokens_reclaimed: number; reason?: string }[]
}

export type AdaptationStatusValue = "running" | "proposed" | "completed" | "rejected" | "failed" | "rolled_back"
export interface Check {
  name: string
  passed: boolean
  detail?: string
  baseline?: number
  candidate?: number
  delta?: number
  override_used?: boolean
}
export interface Decision {
  accepted: boolean
  decision: "accept" | "reject"
  reason: string
  checks: Check[]
  thresholds: Record<string, number | boolean>
  override_used: boolean
  baseline_metrics: Metrics
  candidate_metrics: Metrics
  sample_size: number
}
export interface AdaptationEvent {
  event_id: string
  operation: "split_module" | "merge_modules" | "add_shortcut_edge" | "restore_version"
  trigger: string
  reason: string
  status: AdaptationStatusValue
  mode?: string
  stage?: string
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  error: string | null
  affected_modules?: string[]
  affected_record_count?: number
  created_module_ids?: string[]
  removed_module_ids?: string[]
  evidence?: Record<string, unknown>
  thresholds?: Record<string, unknown>
  evaluation?: { baseline: Metrics; candidate: Metrics; decision: Decision; by_split?: unknown; conditions?: Record<string, unknown> } | { before: unknown; after: unknown }
  decision?: "accept" | "reject"
  decision_reason?: string
  rejection_stage?: string | null
  structural_summary?: string
  summary?: string
  integrity_check?: string
  post_commit_verification?: { status: string; reason?: string; matches_prediction?: boolean } | null
  new_version?: string | null
  before_version?: string | null
  rolled_back_to?: string | null
  rolled_back_by?: string | null
  to_version?: string
  from_version?: string
  recovered?: boolean
  timeline?: { at: string; status: string | null; note: string | null }[]
  diff?: Record<string, unknown>
}
export interface Proposal {
  proposal_id: string
  operation: "split_module" | "merge_modules" | "add_shortcut_edge"
  module_ids: string[]
  trigger: string
  reason: string
  evidence: Record<string, unknown>
  thresholds: Record<string, unknown>
  affected_record_count: number
}
export interface Candidates {
  proposals: Proposal[]
  not_proposed: { operation: string; module_ids: string[]; blockers?: string[] }[]
  window: { queries: number; window_id: string | null; complete: boolean }
  checked_at: string
}
export interface AdaptationStatus {
  queries_in_window: number
  queries_per_window: number
  next_run_after_queries: number
  window_id: string | null
  running: boolean
  current_operation: { kind: string; operation_id: string } | null
  auto_adaptation: boolean
  auto_apply: boolean
  quality_evaluation_enabled: boolean
  rollback_enabled: boolean
  last_operation: string | null
  last_operation_status: string | null
  last_operation_at: string | null
  last_window_run: { window_id: string; queries: number; proposals_detected: number; applied: number; evaluated: { event_id: string; operation: string; status: string }[]; monitoring?: { status: string }; at: string } | null
  window_pairs: { modules: string[]; count: number; eligible: boolean }[]
  window_fallback_rate: number | null
  window_mean_candidates: number | null
  thresholds: Record<string, number | boolean>
}

export interface VersionManifest {
  version_id: string
  created_at: string
  parent_version_id: string | null
  reason: string
  operation_type: string
  operation_id: string | null
  node_count: number
  content_module_count: number
  edge_count: number
  co_access_edge_count: number
  memory_count: number
  checksum: string
  summary: { diff?: string; modules?: string[]; note?: string } & Record<string, unknown>
  evaluation: { baseline?: Metrics; candidate?: Metrics; decision?: string; decision_reason?: string; before?: { metrics?: Metrics } | null; after?: { metrics?: Metrics } | null } | null
  validation: { integrity_check?: string; post_commit_verification?: string; decision?: string } & Record<string, unknown>
  restored_from: string | null
  status: string
  is_active: boolean
  restorable: boolean
  snapshot_problems: string[]
}
export interface VersionDetail extends VersionManifest {
  modules: { module_id: string; name: string; parent_id: string | null; records: number }[]
  edges: { edge_id: string; relation_type: string; source: string; target: string; weight: number; co_access_count: number }[]
  event: AdaptationEvent | null
}
export interface Diff {
  modules_added: { module_id: string; name: string; records: number }[]
  modules_removed: { module_id: string; name: string; records: number }[]
  memories_moved: number
  memory_flows: { from: string; to: string; count: number }[]
  edges_added: { edge_id: string; relation_type: string; source: string; target: string }[]
  edges_removed: { edge_id: string; relation_type: string; source: string; target: string }[]
  counts: { old: { modules: number; edges: number; memories: number }; new: { modules: number; edges: number; memories: number } }
  identical: boolean
}
export interface RestorePreview {
  target_version: VersionManifest
  current_version: VersionManifest | null
  restorable: boolean
  problems: string[]
  is_already_active: boolean
  diff?: Diff
  summary?: string
  reconciliation?: Record<string, number>
  notes?: string[]
}
export interface RestoreResult {
  operation_id: string
  status: string
  restored_from: string
  replaced_version: string | null
  checkpoint_version: string
  new_version: VersionManifest
  summary: string
  diff: Diff
  rolled_back_events: string[]
  duration_ms: number
  evaluation?: { before?: { metrics?: Metrics } | null; after?: { metrics?: Metrics } | null }
}
export interface VersionCompare {
  from_version: string
  to_version: string
  diff: Diff
  summary: string
  evaluation?: { from: Metrics; to: Metrics; decision: Decision }
  note: string
}

export interface EvalDataset {
  query_count: number
  splits: Record<string, number>
  dataset_hash: string
  valid: boolean
  issues: string[]
  usable_queries: number
  skipped_queries: number
  labels_missing: number
  min_eval_queries: number
  thresholds: Record<string, number | boolean>
  meta: { name?: string; label_provenance?: string }
  sample?: { query_id: string; query: string; split: string; tags: string[]; relevant: number }[]
}
export interface EvalRun {
  run_id: string
  label: string
  mode: string
  evaluated_at: string
  valid: boolean
  invalid_reason: string | null
  metrics: Metrics
  by_split: Record<string, Metrics>
  conditions: Record<string, unknown>
  architecture_version?: string
  path?: string
}
export interface ModeComparison {
  modes: Record<"global" | "fixed_graph" | "adaptive_graph", { metrics: Metrics; valid: boolean; label: string; run_id: string }>
  fixed_graph_version: string | null
  adaptive_graph_version: string | null
  conditions: Record<string, unknown>
  note: string
}

export interface ScenarioInfo {
  name: string
  title: string
  description: string
  expected?: string
  loaded: boolean | null
}
export interface AppConfig {
  environment: string
  embedding_model: string
  admin_endpoints: boolean
  adaptation_query_batch: number
  auto_adaptation: boolean
  acceptance_thresholds: Record<string, number | boolean>
  compression: { start_pct: number; emergency_pct: number; cleanup_target_pct: number }
}

// ------------------------------------------------------------------ BEIR benchmark
export type BenchmarkMethod = "bm25" | "global_dense" | "fixed_graph" | "adaptive_graph"
export interface BenchmarkDataset {
  dataset_id: string
  cached: boolean
  path: string
  domain: string | null
  license: string | null
  source_url: string
  corpus_size?: number
  size_mb?: number
  splits?: Record<string, { queries: number; qrels: number }>
}
export interface BenchmarkDatasets { items: BenchmarkDataset[]; data_dir: string; methods: BenchmarkMethod[]; note: string }
export interface BenchmarkJob {
  run_id: string
  status: "queued" | "running" | "completed" | "failed"
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  datasets: Record<string, { status: string; phase?: string; message?: string; error?: string; official_test_split?: boolean | null; smoke?: boolean | null; queries?: number | null }>
  error: string | null
  official: boolean | null
  progress?: { t: string; phase: string; message: string; dataset: string }[]
  config?: Record<string, unknown>
  source?: string
}
export interface BenchmarkStatus { running: boolean; active_run: BenchmarkJob | null; last_run: BenchmarkJob | null }
export interface QualityMetrics {
  ndcg_at_10: number; map_at_10: number; recall_at_5: number; recall_at_10: number; recall_at_100: number
  mrr_at_10: number; precision_at_10: number; queries_evaluated: number
}
export interface LatencyStat { p50_ms: number; p95_ms: number; mean_ms: number }
export interface LatencySummary {
  queries: number; total_results: number
  retrieval: LatencyStat; end_to_end: LatencyStat; query_embedding: LatencyStat
  stage_seed?: { mean_ms: number }; stage_expand?: { mean_ms: number }; stage_search?: { mean_ms: number }; stage_fallback?: { mean_ms: number }
  mean_eligible_candidates: number; candidate_pool_reduction_vs_corpus: number | null; mean_candidates_incl_fallback: number
  fallback_rate: number; fallback_ms_mean_when_used?: number | null
}
export interface Bootstrap {
  mean_diff: number; ci95_low: number; ci95_high: number; n_queries: number; resamples: number; seed: number
  excludes_zero: boolean; queries_improved: number; queries_regressed: number; queries_unchanged: number; note: string
}
export interface BenchmarkComparison {
  comparison: string; baseline: BenchmarkMethod; treatment: BenchmarkMethod; queries: number
  metric_deltas: Record<string, { baseline: number; treatment: number; diff: number }>
  bootstrap: { ndcg_at_10: Bootstrap; recall_at_5: Bootstrap }
  most_improved?: QueryDelta[]; most_regressed?: QueryDelta[]
}
export interface QueryDelta { query_id: string; query: string; ndcg_baseline: number; ndcg_treatment: number; delta: number }
export interface BenchmarkAdaptationEvent {
  event_id: string; operation: string; status: string; reason: string; trigger: string; affected_modules: string[] | null
  affected_record_count: number | null; decision: string | null; decision_reason: string | null; rejection_stage: string | null
  structural_summary: string | null; before_version: string | null; new_version: string | null; rolled_back_to: string | null
  integrity_check: string | null; error: string | null; started_at: string; duration_ms: number | null
  baseline_metrics?: Record<string, number | null>; candidate_metrics?: Record<string, number | null>
  checks?: { name: string; passed: boolean; detail: string }[]; benefit?: string | null
}
export interface BenchmarkSafety {
  note?: string; skipped?: boolean; inconclusive?: string
  harmful_candidate?: { status: string; rejection_stage: string | null; decision_reason: string | null; state_unchanged: boolean; version_unchanged: boolean; baseline_ndcg_at_10: number | null; candidate_ndcg_at_10: number | null }
  post_commit_fault?: { status: string; recovered?: boolean; state_restored_exactly?: boolean; integrity_passed?: boolean; error?: string; detail?: string }
  restoration?: { restore_status?: string; state_matches_pre_change_snapshot?: boolean; restored_again?: string; state_matches_changed_architecture?: boolean; integrity_passed?: boolean; status?: string; detail?: string }
}
export interface BenchmarkManifest {
  run_id: string; dataset_id: string; status: string; official_test_split?: boolean; smoke?: boolean; truncated?: boolean
  query_limit?: number | null; seed?: number; started_at: string; duration_s?: number; error?: string; caveats?: string[]
  dataset?: { counts: Record<string, number>; test_group_queries: number; test_group_qrels: number; relevant_grades: number[]; source: { url: string; license_note: string; corpus_sha256: string } }
  split_protocol?: { sizes: { workload: number; validation: number; test: number }; provenance: Record<string, string>; notes: string }
  validation?: { queries: number }
  embedding?: { model: string; dimension: number; cache_hit: boolean }
  protocol?: { protocol: string; eval_interleaved: boolean; benefit_requires_ci: boolean; cumulative_guard: boolean }
  graph_init?: { modules: number; groups: number; module_size: { min: number; median: number; max: number } }
  retrieval_settings?: Record<string, number>
  acceptance_thresholds?: Record<string, number | boolean | string>
  architecture_version_before?: string; architecture_version_after?: string
  environment?: { python: string; platform: string; torch_device: string | null; memory_gb: number }
  timing_methodology?: string
}
export interface BenchmarkDatasetResult {
  manifest: BenchmarkManifest
  metrics?: Record<string, QualityMetrics>
  latency?: Record<string, LatencySummary>
  system?: { timings_s: Record<string, number>; process_rss_mb: number; graph: { initial: { nodes: number; edges: number }; adaptive: { nodes: number; edges: number; co_access_edges: number } }; embedding_cache_mb: number; qdrant_storage: string; live_parity?: { checked: number; identical_top10: number } }
  comparisons?: BenchmarkComparison[]
  adaptation?: { events: BenchmarkAdaptationEvent[]; accepted: number; rejected: number; failed: number; rolled_back: number; proposals_evaluated: number; initial_version: string; adaptive_version: string; protocol?: { windows_planned: number; workload_queries: number } }
  safety?: BenchmarkSafety | null
  sweep?: { group: string; queries: number; note: string; rows: { config: string; seed_modules: number | null; max_candidate_modules: number | null; ndcg_at_10: number; recall_at_5: number; mrr_at_10: number; mean_candidates: number; p95_latency_ms: number; fallback_rate: number }[] } | null
}
export interface BenchmarkResults {
  run_id: string
  macro_average: Record<string, QualityMetrics & { datasets: number }>
  macro_note?: string
  datasets: Record<string, BenchmarkDatasetResult>
  note?: string
}
export interface BenchmarkRunDetail { run_id: string; job: BenchmarkJob | null; manifests: Record<string, BenchmarkManifest> }

// ---------------------------------------------------------------------------------- strategy comparison (live architecture)
export type StrategyKey = "global" | "graph" | "progressive" | "soft"
export interface CompareResult { rank: number; memory_id: string; score: number; module_id: string; module_name: string; source_name: string | null; text: string; relevant: boolean | null; dense_score: number | null }
export interface CompareStrategy {
  strategy: StrategyKey
  label: string
  latency_ms: number
  embedding_ms: number
  timings_ms: Record<string, number>
  candidate_count: number
  eligible_candidates: number
  candidate_pool_reduction: number | null
  fallback_used: boolean
  fallback_reason: string | null
  seed_modules: { module_id: string; name: string; score: number }[]
  searched_modules: { module_id: string; name: string; origin: string; stage: number | null }[]
  stages: ProgressiveStage[]
  strategy_details: { progressive?: NonNullable<Trace["progressive"]>; soft?: NonNullable<Trace["soft"]> }
  results: CompareResult[]
  result_modules: string[]
  metrics?: { recall_at_k: number; mrr: number; ndcg_at_10: number; precision_at_k: number; map_at_10: number; k: number }
  relevant_retrieved?: string[]
  relevant_missed?: string[]
  relevant_excluded_by_routing?: { memory_id: string; module_id: string; module_name: string; text_preview: string }[]
}
export interface StrategyComparison {
  query: string
  query_id: string | null
  top_k: number
  labeled: boolean
  architecture_version: string | null
  active_records: number
  strategies: Partial<Record<StrategyKey, CompareStrategy>>
  relevant_memories: { memory_id: string; module_id: string; module_name: string; text_preview: string }[]
  notes: string[]
}

// ----------------------------------------------------------------------------------------- strategy benchmark artifacts
export type StrategyName = "global_dense" | "fixed_graph" | "progressive" | "soft"
export type StrategyGroup = "validation" | "test"
export interface StrategyRunSummary { run_id: string; status: string; created_at: string; finished_at?: string; datasets: string[]; evaluated: Record<string, string[]>; smoke: boolean; frozen: boolean }
export interface ProgressiveConfig { policy: string; patience: number; step: number; seed_modules: number; max_modules: number; max_steps: number; fallback_on_budget: boolean }
export interface SoftConfig { weight: number; share: number; candidate_pool: number; seed_modules: number; graph_depth: number }
export interface StrategyRunDetail {
  manifest: { run_id: string; status: string; datasets: string[]; seed: number; smoke: boolean; created_at: string; finished_at?: string; architecture: string; frozen_config_sha256?: string
    environment?: { platform?: string; torch_device?: string; embedding_model?: string; packages?: Record<string, string | null> }
    dataset_info?: Record<string, { documents: number; modules: number; official_test_split: boolean; split_sizes: Record<string, number> }> }
  server_latency?: ServerLatency
  frozen_config?: { frozen_at: string; progressive: ProgressiveConfig; soft: SoftConfig; selected_from: string; selection_rules: Record<string, string>; code_sha256: Record<string, string> }
  selection?: {
    progressive: { selected: { policy: string; patience: number; step: number }; note: string; table: { config: { policy: string; patience: number; step: number }; macro_ndcg: number; macro_work_share: number; non_inferior: boolean; ndcg_delta_vs_global: Record<string, number> }[] }
    soft: { selected: { weight: number; share: number }; reliable_gain: boolean; note: string; table: { config: { weight: number; share: number }; macro_ndcg: number; ndcg_delta_vs_dense: Record<string, number> }[]
      bootstrap_vs_weight_zero_on_validation: Record<string, Bootstrap> }
  }
}
export interface ServerLatency {
  deployment: string
  group: string
  queries: number
  repetitions: number
  datasets: Record<string, { documents: number; queries: number; strategies: Record<StrategyName, { retrieval_p50_ms: number; retrieval_p95_ms: number; retrieval_mean_ms: number; top10_identical_to_local_qdrant: string }> }>
}
export interface StrategyLatency {
  queries: number
  retrieval: { p50_ms: number; p95_ms: number; mean_ms: number }
  end_to_end: { p50_ms: number; p95_ms: number; mean_ms: number }
  query_embedding: { p50_ms: number; p95_ms: number; mean_ms: number }
  stage_seed?: { mean_ms: number }; stage_expand?: { mean_ms: number }; stage_search?: { mean_ms: number }; stage_rank?: { mean_ms: number }; stage_fallback?: { mean_ms: number }
  mean_eligible_candidates: number
  candidate_pool_reduction_vs_corpus: number | null
  mean_candidates_incl_fallback: number
  fallback_rate: number
}
export interface StrategyDiag {
  vs_global_dense?: { queries_improved: number; queries_degraded: number; queries_unchanged: number; relevant_top10_lost: number; relevant_top10_gained: number }
  vs_fixed_graph?: { queries_improved: number; queries_degraded: number; queries_unchanged: number; relevant_top10_lost: number; relevant_top10_gained: number }
  routing: { relevant_documents_excluded_by_routing: number; queries_with_exclusion: number; queries_degraded_with_exclusion: number }
  progressive?: { stop_reasons: Record<string, number>; stages_run_distribution: Record<string, number>; queries_with_fallback: number; queries_where_expansion_never_changed_the_top_k: number; mean_useful_expansion_steps: number }
  soft?: { mean_promoted_in_top_k: number; mean_demoted_in_top_k: number; queries_with_any_reordering: number }
}
export interface StrategyGroupResult {
  run_id: string; dataset_id: string; group: StrategyGroup; queries: number; status: string; smoke: boolean; official_test_split: boolean | null; repetitions: number
  metrics: Record<StrategyName, QualityMetrics>
  latency: Record<StrategyName, StrategyLatency>
  comparisons: (Omit<BenchmarkComparison, "baseline" | "treatment"> & { baseline: string; treatment: string })[]
  diagnostics: Record<string, StrategyDiag>
  baseline_context?: { source: string; same_test_group_size: boolean; metrics: Record<string, QualityMetrics> | null } | null
  frozen_config_sha256: string
}
export type StrategyRow = {
  query_id: string
  query: string
  delta_ndcg_at_10: number
} & Record<StrategyName, QualityMetrics & { e2e_ms: number; retrieval_ms: number; eligible_candidates: number; fallback: number; modules_searched: number | null; excluded_relevant: number; relevant_total: number
  fallback_reason: string | null; stages_run: number | null; stop_reason: string | null; useful_expansion_steps: number | null; soft_promoted: number | null; soft_demoted: number | null }>

// ---------------------------------------------------------------------------------------------- authentication / visitor mode
export interface AuthSession {
  authenticated: boolean
  role: "admin" | "user"
  username: string | null
  /** Unix seconds at which the administrator session ends. */
  expires_at: number | null
  csrf_token: string | null
  admin_configured: boolean
}
export interface QueryHistoryItem {
  query_id: string
  query: string
  mode: QueryMode | null
  top_k: number | null
  created_at: string
  result_count: number
  latency_ms: number | null
}
export type StoredQuery = QueryResponse & { query: string; created_at: string }
