import type { QueryMode, Trace } from "@/api/types"

const list = (names: string[]): string => {
  if (names.length <= 1) return names[0] ?? ""
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
}
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`

/** A plain-language account of the route a query took, built ONLY from what the backend's trace reports. */
export function describeRoute(trace: Trace, mode: QueryMode | string): string[] {
  const seeds = trace.seed_modules?.map((s) => s.name) ?? []
  const searched = trace.searched_modules ?? []
  const lines: string[] = []
  if (mode === "global") {
    lines.push(`MORPHE compared your question with all ${plural(trace.candidate_count, "memory", "memories")} directly. No module routing was used.`)
    return lines
  }
  if (mode === "soft") {
    lines.push(`MORPHE compared your question with all ${plural(trace.candidate_count, "memory", "memories")}, then let module closeness nudge the order by a small, bounded amount.`)
    if (seeds.length) lines.push(`The closest modules were ${list(seeds)}.`)
    return lines
  }
  if (seeds.length) lines.push(`Your question was matched to ${list(seeds)} by comparing it with each module's profile.`)
  if (mode === "progressive") {
    const stages = trace.stages?.length ?? 0
    lines.push(`The search then widened over ${plural(stages || 1, "stage")} (${plural(searched.length, "module")} in total) and stopped once the top results stopped changing.`)
  } else {
    const expanded = trace.candidate_modules?.filter((c) => c.origin !== "seed").map((c) => c.name) ?? []
    lines.push(expanded.length
      ? `It followed graph links to also search ${list(expanded)}, covering ${plural(trace.candidate_count, "memory", "memories")} in ${plural(trace.candidate_modules.length, "module")}.`
      : `No linked modules were added, so ${plural(trace.candidate_count, "memory", "memories")} in ${plural(trace.candidate_modules.length, "module")} were searched.`)
  }
  if (trace.fallback_used) lines.push("The guided results looked weak, so MORPHE also searched the whole memory to be safe (global fallback).")
  return lines
}

/** Best score below this reads as "only weakly related" in the visitor view (a display hint, not a retrieval parameter). */
export const WEAK_MATCH_SCORE = 0.3
