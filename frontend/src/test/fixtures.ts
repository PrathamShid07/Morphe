import type { Metrics } from "@/api/types"

export const metrics = (o: Partial<Metrics> = {}): Metrics => ({
  sample_size: 53, recall_at_5: 0.934, mrr: 0.915, guided_recall_at_5: 0.934, guided_mrr: 0.915, p50_latency_ms: 1.2,
  p95_latency_ms: 1.4, mean_candidate_count: 29.1, fallback_rate: 0, ...o,
})

export const summary = {
  active_memory_count: 60, source_count: 12, module_count: 12, node_count: 17, edge_count: 12, active_tokens: 3298, budget_tokens: 100000,
  usage_percent: 3.3, p50_latency_ms: null, p95_latency_ms: null, latency_samples: 2, latency_note: "Not enough data yet (2 of 5 queries)",
  average_candidate_count: null, fallback_rate: null, process_rss_mb: 512.3, qdrant_storage_mb: 0.5, retrieval_quality: null,
  retrieval_quality_note: "Not enough data yet — run an evaluation on the labeled dataset", architecture_version: "arch_0002",
  thresholds: { compression_start_pct: 75, emergency_cleanup_pct: 90 },
}

export const health = { status: "ok", ready: true, checks: {}, architecture_version: "arch_0002" }
export const config = { environment: "development", embedding_model: "m", admin_endpoints: false, adaptation_query_batch: 50, auto_adaptation: true, acceptance_thresholds: {}, compression: { start_pct: 75, emergency_pct: 90, cleanup_target_pct: 82 } }

export const compressionStatus = {
  active_tokens: 3298, active_memories: 60, budget_tokens: 100000, usage_percent: 3.3, budget_overridden: false, start_threshold_percent: 75,
  emergency_threshold_percent: 90, cleanup_target_percent: 82, normal_target_percent: 70, state: "idle", band: "normal", last_run_status: null,
  last_run: null, cooldown_remaining_seconds: 0, mode: "background", min_summary_saving_percent: 15, storage_note: "",
}
export const adaptationStatus = {
  queries_in_window: 12, queries_per_window: 50, next_run_after_queries: 38, window_id: "w1", running: false, current_operation: null,
  auto_adaptation: true, auto_apply: true, quality_evaluation_enabled: true, rollback_enabled: true, last_operation: null,
  last_operation_status: null, last_operation_at: null, last_window_run: null, window_pairs: [], window_fallback_rate: null,
  window_mean_candidates: null,
  thresholds: { max_recall_regression: 0.02, max_mrr_regression: 0.02, max_p95_latency_regression_pct: 10, min_eval_queries: 10, latency_noise_floor_ms: 1 },
}

export const graph = {
  nodes: [
    { id: "mod_web", type: "moduleNode", position: { x: 0, y: 0 }, data: { label: "Web Frameworks", recordCount: 0, parentId: null, isContainer: true, lastChangeOp: null, lastChangeAt: null } },
    { id: "mod_django", type: "moduleNode", position: { x: 0, y: 130 }, data: { label: "Django", recordCount: 5, parentId: "mod_web", isContainer: false, lastChangeOp: "shortcut", lastChangeAt: "2026-10-24T10:00:00Z" } },
    { id: "mod_pg", type: "moduleNode", position: { x: 220, y: 130 }, data: { label: "PostgreSQL", recordCount: 8, parentId: null, isContainer: false, lastChangeOp: null, lastChangeAt: null } },
  ],
  edges: [
    { id: "edge_h", source: "mod_web", target: "mod_django", label: "hierarchy", data: { relationType: "hierarchy", weight: 1, coAccessCount: 0, createdAt: null } },
    { id: "edge_c", source: "mod_django", target: "mod_pg", label: "co_access", data: { relationType: "co_access", weight: 0.56, coAccessCount: 28, createdAt: "2026-10-24T10:00:00Z" } },
  ],
  architecture_version: "arch_0004", counts: { nodes: 3, edges: 2 },
}
const mod = (id: string, name: string, extra = {}) => ({ module_id: id, name, parent_id: null, children: [], record_count: 5, is_container: false, keywords: ["orm"], description: "", token_count: 100, total_access_count: 7, last_accessed_at: null, shortcut_edges: 0, created_at: null, last_adapted_at: null, last_change_op: null, profile_status: "indexed", ...extra })
export const modules = { items: [mod("mod_web", "Web Frameworks", { is_container: true, record_count: 0, children: ["mod_django"] }), mod("mod_django", "Django", { parent_id: "mod_web", shortcut_edges: 1 }), mod("mod_pg", "PostgreSQL", { record_count: 8 })] }
export const moduleDetail = { ...mod("mod_django", "Django", { parent_id: "mod_web" }), parent: modules.items[0], neighbors: [{ module_id: "mod_pg", name: "PostgreSQL", relation_type: "co_access", weight: 0.56, co_access_count: 28, direction: "shortcut", record_count: 8 }], memories: [], adaptation_history: [], queries_touching_module: 4 }

export const queryResponse = {
  query_id: "qry_ABC", architecture_version: "arch_0004", top_k: 5, latency_ms: 18.4, mode: "graph",
  results: [{ memory_id: "mem_1", text: "Django maps URL patterns to views.", score: 0.8123, module_id: "mod_django", module_name: "Django", source_name: "django.md" }],
  trace: {
    mode: "graph", seed_modules: [{ module_id: "mod_django", name: "Django", score: 0.81 }],
    candidate_modules: [
      { module_id: "mod_django", name: "Django", origin: "seed", via: null, via_name: null, record_count: 5 },
      { module_id: "mod_pg", name: "PostgreSQL", origin: "co_access", via: "mod_django", via_name: "Django", record_count: 8 },
    ],
    expanded_modules: ["mod_pg"], candidate_count: 13, guided_candidate_count: 13, candidate_count_definition: "active records inside the searched modules",
    fallback_used: false, fallback_reason: null, guided_result_ids: ["mem_1"], latency_ms: 18.4, architecture_version: "arch_0004",
  },
}

const decision = (accepted: boolean) => ({
  accepted, decision: accepted ? "accept" : "reject", reason: accepted ? "Accepted: no metric regressed beyond its threshold" : "Rejected: recall_at_5 changed by -0.1200 (allowed regression 0.020)",
  checks: [
    { name: "same_evaluation_conditions", passed: true, detail: "same dataset" },
    { name: "recall_at_5", passed: accepted, detail: accepted ? "recall_at_5 changed by +0.0090" : "recall_at_5 changed by -0.1200 (allowed regression 0.020)" },
  ],
  thresholds: { max_recall_regression: 0.02, max_mrr_regression: 0.02, max_p95_latency_regression_pct: 10, min_eval_queries: 10 },
  override_used: false, baseline_metrics: metrics(), candidate_metrics: metrics(), sample_size: 53,
})
export const proposal = {
  proposal_id: "prop_1", operation: "add_shortcut_edge", module_ids: ["mod_django", "mod_pg"], trigger: "manual_inspection",
  reason: "Modules 'Django' and 'PostgreSQL' were used together by 28 of 50 queries in the current window (threshold 10).",
  evidence: { co_access_count: 28 }, thresholds: { min_count: 10 }, affected_record_count: 0,
}
export const adaptationEvent = (status: string, accepted = status !== "rejected") => ({
  event_id: `adapt_${status}`, operation: "add_shortcut_edge", trigger: "manual", reason: proposal.reason, status,
  started_at: "2026-10-24T10:00:00Z", finished_at: "2026-10-24T10:00:02Z", duration_ms: 2100, error: null, affected_modules: ["mod_django", "mod_pg"],
  affected_record_count: 0, integrity_check: "passed", rejection_stage: status === "rejected" ? "pre_commit" : null,
  decision_reason: decision(accepted).reason, evaluation: { baseline: metrics(), candidate: metrics(accepted ? { mrr: 0.924 } : { recall_at_5: 0.81 }), decision: decision(accepted) },
  new_version: status === "completed" ? "arch_0005" : null, before_version: "arch_0004",
})

export const version = (id: string, o = {}) => ({
  version_id: id, created_at: "2026-10-24T10:00:00Z", parent_version_id: null, reason: "split", operation_type: "split_module", operation_id: "adapt_x",
  node_count: 20, content_module_count: 15, edge_count: 17, co_access_edge_count: 1, memory_count: 78, checksum: "abc", summary: { diff: "+3 modules" },
  evaluation: null, validation: { integrity_check: "passed", decision: "accept" }, restored_from: null, status: "available", is_active: false,
  restorable: true, snapshot_problems: [], ...o,
})
export const diff = {
  modules_added: [], modules_removed: [{ module_id: "mod_py_web", name: "Python Web Notes", records: 6 }], memories_moved: 6, memory_flows: [],
  edges_added: [], edges_removed: [], counts: { old: { modules: 22, edges: 17, memories: 78 }, new: { modules: 20, edges: 14, memories: 78 } }, identical: false,
}
export const restorePreview = (id: string) => ({
  target_version: version(id), current_version: version("arch_0005", { is_active: true }), restorable: true, problems: [], is_already_active: false,
  diff, summary: "-1 module(s)", reconciliation: { inactive_memories_not_revived: 0, new_memories_kept_in_current_module: 2, new_memories_placed_by_similarity: 0 },
  notes: [],
})
