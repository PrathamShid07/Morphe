import { describe, expect, it } from "vitest"
import { describeRoute } from "@/lib/route-text"
import type { Trace } from "@/api/types"

const trace = (over: Partial<Trace> = {}) => ({ mode: "graph", seed_modules: [{ module_id: "a", name: "Django", score: 0.8 }, { module_id: "b", name: "Docker", score: 0.7 }], candidate_modules: [{ module_id: "a", name: "Django", origin: "seed" }, { module_id: "c", name: "Git", origin: "hierarchy" }],
  searched_modules: ["a", "b", "c"], candidate_count: 20, fallback_used: false, stages: [], ...over }) as unknown as Trace

describe("describeRoute", () => {
  it("explains global search without module routing", () => {
    expect(describeRoute(trace(), "global")).toEqual(["MORPHE compared your question with all 20 memories directly. No module routing was used."])
  })
  it("names the seed modules and any modules added through graph links", () => {
    const lines = describeRoute(trace(), "graph")
    expect(lines[0]).toBe("Your question was matched to Django and Docker by comparing it with each module's profile.")
    expect(lines[1]).toContain("also search Git")
    expect(lines).toHaveLength(2)
  })
  it("describes progressive expansion by stages and mentions a global fallback when one happened", () => {
    const lines = describeRoute(trace({ stages: [{}, {}] as never, fallback_used: true }), "progressive")
    expect(lines.join(" ")).toContain("widened over 2 stages (3 modules in total)")
    expect(lines.at(-1)).toContain("global fallback")
  })
  it("describes soft routing as a bounded nudge over a full search", () => {
    expect(describeRoute(trace(), "soft").join(" ")).toContain("small, bounded amount")
  })
})
