import type { QueryMode } from "@/api/types"

export const STRATEGY_LABEL: Record<QueryMode, string> = {
  global: "Global dense", graph: "Fixed graph", progressive: "Progressive expansion", soft: "Soft graph routing",
}
export const STRATEGY_HINT: Record<QueryMode, string> = {
  global: "Search every memory with dense vector similarity. No graph is used.",
  graph: "Hard filter: search only the graph-selected modules (the original MORPHE retrieval).",
  progressive: "Search a few modules, then widen only while the top results are still changing; global fallback if unsettled.",
  soft: "Search everything, then let module and graph information nudge the ranking by a small, bounded amount.",
}
export const MODE_ORDER: QueryMode[] = ["global", "graph", "progressive", "soft"]
