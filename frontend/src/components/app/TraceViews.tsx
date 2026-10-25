import { ArrowDown, ArrowUp, ArrowRight, CheckCircle2, Info, Minus, Zap } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { ms, num } from "@/lib/format"
import type { ProgressiveStage, Trace } from "@/api/types"

const SEGMENTS: { key: string; label: string; cls: string }[] = [
  { key: "embedding", label: "Query embedding", cls: "bg-chart-5" },
  { key: "seed", label: "Module profile search", cls: "bg-chart-1" },
  { key: "expand", label: "Graph / module selection", cls: "bg-chart-2" },
  { key: "search", label: "Vector search", cls: "bg-chart-4" },
  { key: "rank", label: "Ranking", cls: "bg-chart-3" },
  { key: "fallback", label: "Global fallback", cls: "bg-warning" },
]

/** Where the time went. Only stages the backend reported are drawn; the remainder is labelled "other" rather than hidden. */
export function TimingBar({ trace, totalMs }: { trace: Trace; totalMs: number }) {
  const t: Record<string, number> = { ...(trace.timings_ms ?? {}), embedding: trace.embedding_ms ?? 0 }
  const known = SEGMENTS.map((s) => ({ ...s, v: t[s.key] ?? 0 })).filter((s) => s.v > 0)
  const sum = known.reduce((a, b) => a + b.v, 0)
  const other = Math.max(0, totalMs - sum)
  const total = Math.max(totalMs, sum)
  if (!known.length) return <p className="text-xs text-muted-foreground">No stage timings were reported.</p>
  return (
    <div className="space-y-2" data-testid="timing-bar">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted" role="img" aria-label={`Latency breakdown, total ${ms(totalMs)}`}>
        {known.map((s) => <div key={s.key} className={s.cls} style={{ width: `${(s.v / total) * 100}%` }} title={`${s.label}: ${ms(s.v)}`} />)}
        {other > 0 && <div className="bg-muted-foreground/30" style={{ width: `${(other / total) * 100}%` }} title={`Other (locking, serialisation): ${ms(other)}`} />}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {known.map((s) => <li key={s.key} className="flex items-center gap-1.5"><span className={cn("size-2 rounded-full", s.cls)} />{s.label} <b className="tabular-nums text-foreground">{ms(s.v)}</b></li>)}
        {other > 0.05 && <li className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-muted-foreground/40" />Other <b className="tabular-nums text-foreground">{ms(other)}</b></li>}
      </ul>
    </div>
  )
}

function StageCard({ s, last }: { s: ProgressiveStage; last: boolean }) {
  const changed = s.top_k_changed
  return (
    <li className={cn("rounded-2xl border p-3 text-xs", last ? "border-primary/50 bg-violet-soft/60" : "bg-card")} data-testid={`stage-${s.stage}`}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-semibold">Stage {s.stage} · {s.kind === "seed" ? "seed modules" : "expansion"}</span>
        <Badge variant="outline" className="font-normal">{ms(s.search_ms)}</Badge>
      </div>
      <ul className="space-y-1">
        {s.modules.map((m) => (
          <li key={m.module_id} className="flex items-center justify-between gap-2 rounded-lg bg-muted/60 px-2 py-1">
            <span className="min-w-0 truncate font-medium">{m.name}</span>
            <span className="shrink-0 text-muted-foreground">#{m.profile_rank} · sim {m.profile_score.toFixed(2)} · {num(m.records)} rec</span>
          </li>
        ))}
      </ul>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
        <dt className="text-muted-foreground">New candidates</dt><dd className="text-right tabular-nums">+{num(s.new_candidates)}</dd>
        <dt className="text-muted-foreground">Examined so far</dt><dd className="text-right tabular-nums">{num(s.cumulative_candidates)}</dd>
        <dt className="text-muted-foreground">Best score</dt><dd className="text-right tabular-nums">{s.best_score === null ? "—" : s.best_score.toFixed(3)}</dd>
        <dt className="text-muted-foreground">Top-k</dt>
        <dd className="text-right">{changed === null ? <span className="text-muted-foreground">first look</span> : changed ? <span className="font-medium text-warning">changed (+{s.new_in_top_k})</span> : <span className="font-medium text-success">unchanged</span>}</dd>
      </dl>
      <p className={cn("mt-2 flex items-start gap-1.5 border-t pt-2", last ? "font-medium text-foreground" : "text-muted-foreground")}>
        {s.decision.startsWith("stop") ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-primary" /> : <ArrowRight className="mt-0.5 size-3.5 shrink-0" />}{s.decision}
      </p>
    </li>
  )
}

/** Progressive expansion: every stage the backend executed, why it stopped, and whether the global fallback ran. */
export function StageProgression({ trace }: { trace: Trace }) {
  const stages = trace.stages ?? []
  const pg = trace.progressive
  if (!stages.length) return <p className="text-sm text-muted-foreground">The backend returned no stage information for this query{trace.fallback_used ? ` (${trace.fallback_reason ?? "global fallback"})` : ""}.</p>
  return (
    <div className="space-y-3" data-testid="progressive-trace">
      <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{stages.map((s, i) => <StageCard key={s.stage} s={s} last={i === stages.length - 1} />)}</ol>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border p-3 text-xs">
        {pg && <span><b className="text-foreground">{pg.modules_searched}</b> of {pg.modules_total} modules searched · policy <b className="text-foreground">{pg.policy}</b> · stop: <b className="text-foreground">{pg.stop_reason}</b></span>}
        <span><b className="tabular-nums text-foreground">{num(trace.candidate_count)}</b> records examined{trace.fallback_used ? " (including the global fallback)" : ""}</span>
        {trace.fallback_used ? <Badge variant="outline" className="border-warning/50 bg-warning-soft text-warning"><Zap className="size-3" /> global fallback: {trace.fallback_reason}</Badge> : <Badge variant="outline" className="text-success">no global fallback</Badge>}
      </div>
      <p className="text-[11px] text-muted-foreground">Stopping uses only query-time signals (did the top results stop changing as modules were added?), never relevance labels. “Examined” counts active records inside searched modules.</p>
    </div>
  )
}

const Shift = ({ v, max }: { v: number; max: number }) => {
  const w = max > 0 ? Math.min(50, (Math.abs(v) / max) * 50) : 0
  return (
    <span className="relative inline-block h-2 w-20 rounded-full bg-muted align-middle" title={`score shift ${v >= 0 ? "+" : ""}${v.toFixed(4)}`}>
      <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
      <span className={cn("absolute inset-y-0 rounded-full", v >= 0 ? "bg-success" : "bg-destructive")} style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
    </span>
  )
}

/** Soft routing: dense similarity, graph/profile prior and the bounded shift for every returned memory. */
export function SoftBreakdown({ trace }: { trace: Trace }) {
  const sf = trace.soft
  if (!sf) return <p className="text-sm text-muted-foreground">The backend returned no soft-routing details for this query.</p>
  return (
    <div className="space-y-3" data-testid="soft-trace">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">weight λ = {sf.weight}</Badge><Badge variant="outline">graph share = {sf.graph_share}</Badge>
        <Badge variant="outline">candidate pool = {sf.pool_size}</Badge><Badge variant="outline">graph depth ≤ {sf.graph_depth}</Badge>
        <span className="text-muted-foreground">{sf.promoted} promoted · {sf.demoted} demoted · {sf.unchanged} unchanged · top-k membership {sf.top_k_membership_changed ? <b className="text-warning">changed</b> : <b className="text-foreground">same as dense</b>}</span>
      </div>
      <code className="block overflow-x-auto rounded-xl bg-muted/60 px-3 py-2 text-[11px]">{sf.formula} · shift bounded by ±{sf.weight}</code>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[40rem] text-xs" aria-label="Soft routing breakdown">
          <thead><tr className="border-b text-left text-muted-foreground"><th className="p-2 font-medium">Rank</th><th className="p-2 font-medium">Module</th><th className="p-2 text-right font-medium">Dense</th><th className="p-2 text-right font-medium">Module rank</th><th className="p-2 text-right font-medium">Graph dist.</th><th className="p-2 text-right font-medium">Prior</th><th className="p-2 font-medium">Shift</th><th className="p-2 text-right font-medium">Final</th></tr></thead>
          <tbody>
            {sf.results.map((r) => (
              <tr key={r.memory_id} className="border-b last:border-0" title={r.reason}>
                <td className="whitespace-nowrap p-2 font-medium tabular-nums">{r.final_rank}<span className="ml-1.5 inline-flex items-center text-muted-foreground">{r.change === "promoted" ? <ArrowUp className="size-3 text-success" /> : r.change === "demoted" ? <ArrowDown className="size-3 text-destructive" /> : <Minus className="size-3" />}<span className="ml-0.5">was {r.dense_rank}</span></span></td>
                <td className="max-w-[10rem] truncate p-2">{r.module_name}</td>
                <td className="p-2 text-right tabular-nums">{r.dense_score.toFixed(3)}</td>
                <td className="p-2 text-right tabular-nums">{r.profile_rank ?? "—"}</td>
                <td className="p-2 text-right tabular-nums">{r.graph_distance ?? "—"}</td>
                <td className="p-2 text-right tabular-nums">{r.prior.toFixed(2)}</td>
                <td className="p-2"><Shift v={r.score_shift} max={sf.weight} /> <span className="ml-1 tabular-nums text-muted-foreground">{r.score_shift >= 0 ? "+" : ""}{r.score_shift.toFixed(4)}</span></td>
                <td className="p-2 text-right font-medium tabular-nums">{r.final_score.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground"><Info className="mt-0.5 size-3.5 shrink-0" />The prior is a ranking nudge derived from module similarity and graph distance, not a relevance label. Soft routing runs a full global search first, so it can reorder results but does not reduce the number of records examined ({num(trace.candidate_count)}).</p>
    </div>
  )
}

export function GlobalTrace({ trace }: { trace: Trace }) {
  return (
    <div className="rounded-xl border p-3 text-sm" data-testid="global-trace">
      <p><b>Global dense search</b>: every active memory ({num(trace.candidate_count)}) was scored by vector similarity. The module graph was not used.</p>
    </div>
  )
}
