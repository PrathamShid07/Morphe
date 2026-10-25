/** The last query's real backend trace, shared between the Query Playground and the Graph Explorer (never inferred on the client). */
import { useSyncExternalStore } from "react"
import type { QueryMode, QueryResponse } from "@/api/types"

export interface RouteSnapshot {
  query: string
  mode: QueryMode
  query_id: string
  architecture_version: string | null
  latency_ms: number
  trace: NonNullable<QueryResponse["trace"]>
  results: { memory_id: string; module_id: string; module_name: string | null; score: number }[]
  at: number
}

const KEY = "morphe.route.v1"
let current: RouteSnapshot | null = null
try {
  const raw = sessionStorage.getItem(KEY)
  current = raw ? (JSON.parse(raw) as RouteSnapshot) : null
} catch {
  current = null
}
const listeners = new Set<() => void>()

export function setRoute(s: RouteSnapshot | null): void {
  current = s
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s))
    else sessionStorage.removeItem(KEY)
  } catch { /* storage unavailable: the route still works for this page view */ }
  listeners.forEach((l) => l())
}

export function routeFromResponse(query: string, mode: QueryMode, r: QueryResponse): RouteSnapshot | null {
  if (!r.trace) return null  // no trace was returned, so there is no real route to show
  return {
    query, mode, query_id: r.query_id, architecture_version: r.architecture_version, latency_ms: r.latency_ms, trace: r.trace, at: Date.now(),
    results: r.results.map((x) => ({ memory_id: x.memory_id, module_id: x.module_id, module_name: x.module_name, score: x.score })),
  }
}

export function useRoute(): RouteSnapshot | null {
  return useSyncExternalStore(
    (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
    () => current,
    () => null,
  )
}

export type RouteRole = "seed" | "expanded" | "searched"
export interface RouteModuleState { role: RouteRole | null; stage: number | null; results: number }

/** Per-module roles derived ONLY from the backend trace: seeds, expansion stages, searched set and where the returned memories live. */
export function routeStates(r: RouteSnapshot | null): { modules: Map<string, RouteModuleState>; viaEdges: Set<string>; wholeCorpus: boolean; fallback: boolean } {
  const modules = new Map<string, RouteModuleState>()
  const viaEdges = new Set<string>()
  if (!r) return { modules, viaEdges, wholeCorpus: false, fallback: false }
  const t = r.trace
  const get = (id: string): RouteModuleState => {
    let m = modules.get(id)
    if (!m) { m = { role: null, stage: null, results: 0 }; modules.set(id, m) }
    return m
  }
  const wholeCorpus = r.mode === "global" || r.mode === "soft" || t.fallback_used
  for (const s of t.seed_modules ?? []) get(s.module_id).role = "seed"
  for (const c of t.searched_modules ?? []) {
    const m = get(c.module_id)
    m.stage = c.stage
    if (m.role !== "seed") m.role = c.origin === "seed" ? "seed" : "expanded"
  }
  for (const c of t.candidate_modules ?? []) {  // fixed graph: hierarchy / co-access expansion records which module it came through
    const m = get(c.module_id)
    if (m.role === null) m.role = c.origin === "seed" ? "seed" : "expanded"
    if (c.via) viaEdges.add(`${c.via}|${c.module_id}`)
  }
  for (const x of r.results) get(x.module_id).results += 1
  return { modules, viaEdges, wholeCorpus, fallback: t.fallback_used }
}
