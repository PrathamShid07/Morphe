import { useMemo, useState } from "react"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { ChevronDown, Download, FlaskConical, Info, Lock, Search } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { EmptyState, ErrorState, LoadingBlock } from "./states"
import { MonoId, Section } from "./ui-bits"
import { strategyExportUrl, useStrategyGroup, useStrategyRows, useStrategyRun, useStrategyRuns } from "@/api/hooks"
import type { Bootstrap, QualityMetrics, StrategyGroup, StrategyGroupResult, StrategyName, StrategyRow } from "@/api/types"
import { NO_DATA, dateTime, ms, num } from "@/lib/format"
import { cn } from "@/lib/utils"

const NAMES: StrategyName[] = ["global_dense", "fixed_graph", "progressive", "soft"]
const LABEL: Record<StrategyName, string> = { global_dense: "Global dense", fixed_graph: "Fixed graph", progressive: "Progressive expansion", soft: "Soft graph routing" }
const QUALITY: { key: keyof QualityMetrics; label: string }[] = [
  { key: "ndcg_at_10", label: "nDCG@10" }, { key: "map_at_10", label: "MAP@10" }, { key: "recall_at_5", label: "Recall@5" }, { key: "recall_at_10", label: "Recall@10" },
  { key: "recall_at_100", label: "Recall@100" }, { key: "mrr_at_10", label: "MRR@10" }, { key: "precision_at_10", label: "Precision@10" },
]
const f4 = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(v) ? NO_DATA : v.toFixed(4))
const sgn = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}`
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? NO_DATA : `${(v * 100).toFixed(d)}%`)

function CI({ b }: { b: Bootstrap }) {
  return <span className={cn("tabular-nums", b.excludes_zero ? (b.mean_diff > 0 ? "text-success" : "text-warning") : "text-muted-foreground")}>{sgn(b.mean_diff)} [{sgn(b.ci95_low)}, {sgn(b.ci95_high)}]</span>
}

function StatusBadge({ r }: { r: StrategyGroupResult }) {
  const tone = r.group === "validation" ? "border-info/40 bg-info-soft text-info" : r.status.startsWith("confirmatory") ? "border-success/40 bg-success-soft text-success" : "border-warning/50 bg-warning-soft text-warning"
  return <Badge variant="outline" className={cn("max-w-full whitespace-normal text-left", tone)} data-testid="group-status">{r.smoke ? "SMOKE (non-official) · " : ""}{r.status}</Badge>
}

// -------------------------------------------------------------------------------------------------------------------- tables
function Quality({ r, shown }: { r: StrategyGroupResult; shown: StrategyName[] }) {
  const ctx = r.baseline_context?.metrics
  const best = (k: keyof QualityMetrics) => Math.max(...shown.map((s) => r.metrics[s][k] as number))
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Retrieval quality by strategy">
          <TableHeader><TableRow><TableHead>Strategy</TableHead>{QUALITY.map((q) => <TableHead key={q.key} className="text-right">{q.label}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {shown.map((s) => (
              <TableRow key={s}><TableCell className="whitespace-nowrap font-medium">{LABEL[s]}</TableCell>
                {QUALITY.map((q) => { const v = r.metrics[s][q.key] as number; return <TableCell key={q.key} className={cn("text-right tabular-nums", Math.abs(v - best(q.key)) < 1e-9 && shown.length > 1 && "font-semibold text-success")}>{f4(v)}</TableCell> })}</TableRow>
            ))}
            {ctx?.bm25 && (
              <TableRow className="bg-muted/40 text-muted-foreground"><TableCell className="whitespace-nowrap">BM25 <span className="text-[10px]">(context, frozen v2 run)</span></TableCell>{QUALITY.map((q) => <TableCell key={q.key} className="text-right tabular-nums">{f4(ctx.bm25[q.key] as number)}</TableCell>)}</TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">{r.queries} queries · green = best of the strategies shown · graded relevance kept · BEIR metrics (pytrec_eval). The context row is read from the frozen benchmark and is only shown when its test group matches this one exactly.</p>
    </div>
  )
}

function Efficiency({ r, shown }: { r: StrategyGroupResult; shown: StrategyName[] }) {
  const cfg: ChartConfig = {
    embedding: { label: "query embedding", color: "var(--chart-5)" }, seed: { label: "module profile search", color: "var(--chart-1)" }, expand: { label: "module selection", color: "var(--chart-2)" },
    search: { label: "vector search", color: "var(--chart-4)" }, rank: { label: "ranking", color: "var(--chart-3)" }, fallback: { label: "global fallback", color: "var(--warning)" },
  }
  const data = shown.map((s) => { const l = r.latency[s]; return { strategy: LABEL[s].replace(" (hard filter)", ""), embedding: l.query_embedding.mean_ms, seed: l.stage_seed?.mean_ms ?? 0, expand: l.stage_expand?.mean_ms ?? 0, search: l.stage_search?.mean_ms ?? 0, rank: l.stage_rank?.mean_ms ?? 0, fallback: l.stage_fallback?.mean_ms ?? 0 } })
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Efficiency by strategy">
          <TableHeader><TableRow><TableHead>Strategy</TableHead><TableHead className="text-right">p50 e2e</TableHead><TableHead className="text-right">p95 e2e</TableHead><TableHead className="text-right">Retrieval p50</TableHead><TableHead className="text-right">Records examined</TableHead><TableHead className="text-right">Pool cut</TableHead><TableHead className="text-right">…incl. fallback</TableHead><TableHead className="text-right">Fallback rate</TableHead></TableRow></TableHeader>
          <TableBody>{shown.map((s) => { const l = r.latency[s]; return (
            <TableRow key={s}><TableCell className="whitespace-nowrap font-medium">{LABEL[s]}</TableCell>
              <TableCell className="text-right tabular-nums">{ms(l.end_to_end.p50_ms)}</TableCell><TableCell className="text-right tabular-nums">{ms(l.end_to_end.p95_ms)}</TableCell><TableCell className="text-right tabular-nums">{ms(l.retrieval.p50_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{num(l.mean_eligible_candidates, 1)}</TableCell><TableCell className="text-right tabular-nums">{pct(l.candidate_pool_reduction_vs_corpus)}</TableCell>
              <TableCell className="text-right tabular-nums">{num(l.mean_candidates_incl_fallback, 1)}</TableCell><TableCell className="text-right tabular-nums">{pct(l.fallback_rate)}</TableCell></TableRow>) })}</TableBody>
        </Table>
      </div>
      <ChartContainer config={cfg} className="aspect-auto h-60 w-full">
        <BarChart data={data} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="strategy" tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} unit=" ms" />
          <ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent className="flex-wrap gap-y-1" />} />
          {Object.keys(cfg).map((k) => <Bar key={k} dataKey={k} stackId="t" fill={`var(--color-${k})`} />)}</BarChart>
      </ChartContainer>
      <p className="text-xs text-muted-foreground">Mean time per stage (median of {r.repetitions} repetitions per query, strategies interleaved). “Pool cut” is the share of the corpus not examined; it is not a speed-up by itself, and latency here comes from the local in-process Qdrant, whose filtered search costs more than an unfiltered one (see docs/BEIR_PERFORMANCE_ANALYSIS.md).</p>
    </div>
  )
}

function Comparisons({ r, shown }: { r: StrategyGroupResult; shown: StrategyName[] }) {
  const rows = r.comparisons.filter((c) => shown.includes(c.treatment as StrategyName) && shown.includes(c.baseline as StrategyName))
  if (!rows.length) return <p className="text-sm text-muted-foreground">Select at least two strategies to see paired comparisons.</p>
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Paired bootstrap comparisons">
          <TableHeader><TableRow><TableHead>Comparison</TableHead><TableHead className="text-right">Δ nDCG@10 [95% CI]</TableHead><TableHead className="text-right">Δ Recall@5 [95% CI]</TableHead><TableHead className="text-right">improved / regressed / same</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map((c) => (
            <TableRow key={c.comparison}><TableCell className="whitespace-nowrap font-medium">{LABEL[c.treatment as StrategyName]} − {LABEL[c.baseline as StrategyName]}</TableCell>
              <TableCell className="text-right"><CI b={c.bootstrap.ndcg_at_10} /></TableCell><TableCell className="text-right"><CI b={c.bootstrap.recall_at_5} /></TableCell>
              <TableCell className="text-right tabular-nums">{c.bootstrap.ndcg_at_10.queries_improved} / {c.bootstrap.ndcg_at_10.queries_regressed} / {c.bootstrap.ndcg_at_10.queries_unchanged}</TableCell></TableRow>))}</TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">Paired query-level bootstrap ({rows[0].bootstrap.ndcg_at_10.resamples} resamples, seed {rows[0].bootstrap.ndcg_at_10.seed}); resampling unit = query. Coloured only when the interval excludes 0. Intervals describe query sampling noise, not other seeds, splits or corpora. Latency noise is a separate matter and is not covered by these intervals.</p>
    </div>
  )
}

function Diagnostics({ r, shown }: { r: StrategyGroupResult; shown: StrategyName[] }) {
  const items = (["fixed_graph", "progressive", "soft"] as StrategyName[]).filter((s) => shown.includes(s) && r.diagnostics[s])
  if (!items.length) return <p className="text-sm text-muted-foreground">Select a graph-based strategy to see routing diagnostics.</p>
  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {items.map((s) => { const d = r.diagnostics[s]; const g = d.vs_global_dense; const f = d.vs_fixed_graph; return (
        <div key={s} className="surface space-y-2 p-3 text-xs" data-testid={`diag-${s}`}>
          <h4 className="text-sm font-semibold">{LABEL[s]}</h4>
          {g && <p><b>vs global dense:</b> {g.queries_improved} queries improved, {g.queries_degraded} degraded, {g.queries_unchanged} unchanged · relevant top-10 documents lost {g.relevant_top10_lost}, gained {g.relevant_top10_gained}</p>}
          {f && <p><b>vs fixed graph:</b> {f.queries_improved} improved, {f.queries_degraded} degraded, {f.queries_unchanged} unchanged · recovered {f.relevant_top10_gained} relevant top-10 documents, lost {f.relevant_top10_lost}</p>}
          <p><b>Routing:</b> {num(d.routing.relevant_documents_excluded_by_routing)} relevant documents sat in modules it never searched, in {d.routing.queries_with_exclusion} queries ({d.routing.queries_degraded_with_exclusion} of them worse than global dense).{s === "soft" ? " (Soft routing searches everything, so this is zero by construction.)" : ""}</p>
          {d.progressive && <>
            <p><b>Stopping:</b> {Object.entries(d.progressive.stop_reasons).map(([k, v]) => `${k} ${v}`).join(" · ")} · stages run: {Object.entries(d.progressive.stages_run_distribution).map(([k, v]) => `${k}×${v}`).join(", ")}</p>
            <p><b>Fallback:</b> {d.progressive.queries_with_fallback} queries · expansion never changed the top-k in {d.progressive.queries_where_expansion_never_changed_the_top_k} queries (mean useful steps {d.progressive.mean_useful_expansion_steps.toFixed(2)})</p></>}
          {d.soft && <p><b>Re-ranking:</b> mean {d.soft.mean_promoted_in_top_k.toFixed(1)} promoted / {d.soft.mean_demoted_in_top_k.toFixed(1)} demoted per query · {d.soft.queries_with_any_reordering} queries reordered</p>}
        </div>) })}
    </div>
  )
}

/** Plain-language reading of the numbers above, generated from the saved artifact (never hand-written results). */
function Explain({ r, shown }: { r: StrategyGroupResult; shown: StrategyName[] }) {
  const lines: string[] = []
  for (const s of (["fixed_graph", "progressive", "soft"] as StrategyName[]).filter((x) => shown.includes(x))) {
    const c = r.comparisons.find((x) => x.baseline === "global_dense" && x.treatment === s)
    const l = r.latency[s], d = r.diagnostics[s]
    if (!c) continue
    const b = c.bootstrap.ndcg_at_10
    const verdict = !b.excludes_zero ? "the interval includes 0, so this is not a reliable change" : b.mean_diff > 0 ? "a reliable improvement on these queries" : "a reliable loss on these queries"
    let t = `${LABEL[s]} changed nDCG@10 by ${sgn(b.mean_diff)} versus global dense (95% CI ${sgn(b.ci95_low)} to ${sgn(b.ci95_high)}): ${verdict}.`
    if (s !== "soft") t += ` It examined ${num(l.mean_candidates_incl_fallback, 0)} records per query on average (${pct(1 - l.mean_candidates_incl_fallback / Math.max(1, r.latency.global_dense.mean_candidates_incl_fallback), 0)} fewer than global search) and fell back to global search for ${pct(l.fallback_rate, 1)} of queries.`
    else t += " It runs a full global search first, so the number of records examined is unchanged."
    if (s !== "soft" && d?.routing.relevant_documents_excluded_by_routing) t += ` ${num(d.routing.relevant_documents_excluded_by_routing)} relevant documents were in modules it never searched.`
    if (s === "progressive" && d?.progressive) t += ` The top results stopped changing early in ${d.progressive.stop_reasons.stable ?? 0} queries; the budget ended the search in ${d.progressive.stop_reasons.budget ?? 0}.`
    lines.push(t)
  }
  if (!lines.length) return null
  return (
    <div className="space-y-2 rounded-xl border bg-muted/40 p-3 text-sm" data-testid="explanation">
      <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground"><Info className="size-3.5" />What these numbers say (generated from this run’s saved results)</p>
      <ul className="list-disc space-y-1.5 pl-5">{lines.map((x) => <li key={x}>{x}</li>)}</ul>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------ per-query inspector
function Inspector({ runId, ds, group, shown }: { runId: string; ds: string; group: StrategyGroup; shown: StrategyName[] }) {
  const treatments = shown.filter((s) => s !== "global_dense")
  const [sort, setSort] = useState("progressive_vs_global_dense")
  const [only, setOnly] = useState("degraded")
  const [order, setOrder] = useState<"worst" | "best">("worst")
  const [search, setSearch] = useState("")
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const q = useStrategyRows(runId, ds, group, { sort, order, only, search, limit: 12, offset })
  const opts = useMemo(() => treatments.flatMap((t) => (["global_dense", "fixed_graph"] as StrategyName[]).filter((b) => b !== t && (shown.includes(b))).map((b) => `${t}_vs_${b}`)), [treatments, shown])
  const sortValue = opts.includes(sort) ? sort : opts[0] ?? sort
  const [t, , b] = [sortValue.split("_vs_")[0] as StrategyName, "", sortValue.split("_vs_")[1] as StrategyName]
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={sortValue} onValueChange={(v) => { setSort(v); setOffset(0) }}><SelectTrigger className="h-10 w-72 max-w-full rounded-xl" aria-label="Comparison to inspect"><SelectValue /></SelectTrigger>
          <SelectContent>{opts.map((o) => { const [x, y] = o.split("_vs_") as StrategyName[]; return <SelectItem key={o} value={o}>{LABEL[x]} vs {LABEL[y]}</SelectItem> })}</SelectContent></Select>
        <Select value={only} onValueChange={(v) => { setOnly(v); setOffset(0) }}><SelectTrigger className="h-10 w-44 rounded-xl" aria-label="Which queries"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="all">All queries</SelectItem><SelectItem value="degraded">Degraded only</SelectItem><SelectItem value="improved">Improved only</SelectItem><SelectItem value="excluded">With routing exclusions</SelectItem></SelectContent></Select>
        <ToggleGroup type="single" variant="outline" size="sm" value={order} onValueChange={(v) => { if (v) { setOrder(v as "worst" | "best"); setOffset(0) } }} aria-label="Order"><ToggleGroupItem value="worst" className="h-10 px-3">Worst first</ToggleGroupItem><ToggleGroupItem value="best" className="h-10 px-3">Best first</ToggleGroupItem></ToggleGroup>
        <div className="relative min-w-0 flex-1 basis-48"><Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0) }} placeholder="Search query text or id…" aria-label="Search queries" className="h-10 rounded-xl pl-9" /></div>
      </div>
      {q.isLoading ? <LoadingBlock rows={3} /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.items.length ? <EmptyState title="No queries match" description="Change the filter, or search for a different phrase." /> : (
        <>
          <ul className="space-y-2" aria-label="Per-query results">
            {q.data.items.map((row: StrategyRow) => (
              <li key={row.query_id} className="surface overflow-hidden">
                <button type="button" className="flex min-h-14 w-full items-center gap-3 p-3 text-left" onClick={() => setOpen(open === row.query_id ? null : row.query_id)} aria-expanded={open === row.query_id}>
                  <span className={cn("w-16 shrink-0 text-right text-sm font-semibold tabular-nums", row.delta_ndcg_at_10 > 1e-12 ? "text-success" : row.delta_ndcg_at_10 < -1e-12 ? "text-warning" : "text-muted-foreground")}>{sgn(row.delta_ndcg_at_10)}</span>
                  <span className="min-w-0 flex-1"><span className="line-clamp-2 text-sm">{row.query || row.query_id}</span><span className="mono-id block truncate">{row.query_id}</span></span>
                  <ChevronDown className={cn("size-4 shrink-0 text-muted-foreground transition", open === row.query_id && "rotate-180")} />
                </button>
                {open === row.query_id && (
                  <div className="overflow-x-auto border-t p-3">
                    <Table aria-label="Strategies for this query"><TableHeader><TableRow><TableHead>Strategy</TableHead><TableHead className="text-right">nDCG@10</TableHead><TableHead className="text-right">Recall@10</TableHead><TableHead className="text-right">Records examined</TableHead><TableHead className="text-right">Latency</TableHead><TableHead>Notes</TableHead></TableRow></TableHeader>
                      <TableBody>{shown.map((s) => { const x = row[s]; return (
                        <TableRow key={s} className={cn((s === t || s === b) && "bg-violet-soft/50")}><TableCell className="font-medium">{LABEL[s]}</TableCell><TableCell className="text-right tabular-nums">{f4(x.ndcg_at_10)}</TableCell><TableCell className="text-right tabular-nums">{f4(x.recall_at_10)}</TableCell>
                          <TableCell className="text-right tabular-nums">{num(x.eligible_candidates)}</TableCell><TableCell className="text-right tabular-nums">{ms(x.e2e_ms)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">{[x.excluded_relevant > 0 && `${x.excluded_relevant} of ${x.relevant_total} relevant docs in unsearched modules`, x.fallback ? `fallback: ${x.fallback_reason}` : null, x.stages_run ? `${x.stages_run} stages, stop: ${x.stop_reason}` : null, x.soft_promoted !== null && (x.soft_promoted || x.soft_demoted) ? `${x.soft_promoted} promoted / ${x.soft_demoted} demoted` : null].filter(Boolean).join(" · ") || "—"}</TableCell></TableRow>) })}</TableBody></Table>
                  </div>)}
              </li>))}
          </ul>
          <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{q.data.total} queries · showing {offset + 1}–{Math.min(offset + 12, q.data.total)}</span>
            <span className="flex gap-2"><Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 12))}>Previous</Button><Button size="sm" variant="outline" disabled={offset + 12 >= q.data.total} onClick={() => setOffset(offset + 12)}>Next</Button></span></div>
        </>
      )}
    </div>
  )
}

/** Same four strategies on an indexed Qdrant server: where a smaller candidate pool could actually pay off, and where it measurably does not. */
function ServerLatency({ runId, ds, shown }: { runId: string; ds: string; shown: StrategyName[] }) {
  const q = useStrategyRun(runId)
  const sl = q.data?.server_latency
  const d = sl?.datasets[ds]
  if (q.isLoading) return <LoadingBlock rows={2} />
  if (!sl || !d) return <p className="text-sm text-muted-foreground">No indexed-server latency was measured for this dataset in this run. Run <code className="mono-id">python -m app.evaluation.run_strategy_latency --run-id {runId}</code> with a Qdrant server on port 6343 (see SETUP.md).</p>
  const base = d.strategies.global_dense.retrieval_p50_ms
  return (
    <div className="space-y-2" data-testid="server-latency">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Latency on an indexed Qdrant server">
          <TableHeader><TableRow><TableHead>Strategy</TableHead><TableHead className="text-right">Retrieval p50</TableHead><TableHead className="text-right">p95</TableHead><TableHead className="text-right">vs global dense</TableHead><TableHead className="text-right">Same top-10 as local Qdrant</TableHead></TableRow></TableHeader>
          <TableBody>{shown.map((s) => { const v = d.strategies[s]; return (
            <TableRow key={s}><TableCell className="whitespace-nowrap font-medium">{LABEL[s]}</TableCell><TableCell className="text-right tabular-nums">{ms(v.retrieval_p50_ms)}</TableCell><TableCell className="text-right tabular-nums">{ms(v.retrieval_p95_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{s === "global_dense" ? "—" : `${(v.retrieval_p50_ms / base).toFixed(1)}×`}</TableCell><TableCell className="text-right tabular-nums">{v.top10_identical_to_local_qdrant}</TableCell></TableRow>) })}</TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">{sl.deployment}. {d.queries} validation queries, median of {sl.repetitions} repetitions, query embedding excluded. Same engine and frozen configuration as above; only the vector store differs. Candidate-pool reduction does not make any strategy faster than global search here: the extra Qdrant round trips cost more than the search they avoid.</p>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------- frozen configuration
function Frozen({ runId }: { runId: string }) {
  const q = useStrategyRun(runId)
  if (q.isLoading) return <LoadingBlock rows={2} />
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const { frozen_config: fz, selection: sel, manifest: m } = q.data
  if (!fz || !sel) return <p className="text-sm text-muted-foreground">This run has not been frozen yet (tuning is still running or was not performed).</p>
  const p = sel.progressive.table, s = sel.soft.table
  return (
    <div className="space-y-4 text-sm">
      <p className="flex flex-wrap items-center gap-2 text-xs"><Lock className="size-3.5" />Frozen {dateTime(fz.frozen_at)} from the <b>{fz.selected_from}</b>; config SHA-256 <MonoId id={m.frozen_config_sha256} short={12} />. The test group was evaluated only after this point.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="surface space-y-1 p-3 text-xs"><h4 className="text-sm font-semibold">Progressive expansion</h4><p>policy <b>{fz.progressive.policy}</b> · step {fz.progressive.step} modules · patience {fz.progressive.patience} · {fz.progressive.seed_modules} seeds · budget {fz.progressive.max_modules} modules / {fz.progressive.max_steps} steps · fallback on unsettled budget: {String(fz.progressive.fallback_on_budget)}</p><p className="text-muted-foreground">{sel.progressive.note}</p></div>
        <div className="surface space-y-1 p-3 text-xs"><h4 className="text-sm font-semibold">Soft graph routing</h4><p>weight λ <b>{fz.soft.weight}</b> · graph share {fz.soft.share} · pool {fz.soft.candidate_pool} · {fz.soft.seed_modules} seeds · depth {fz.soft.graph_depth}</p><p className="text-muted-foreground">Gain over weight 0 on validation: {sel.soft.reliable_gain ? "reliable" : "not reliable"}. {sel.soft.note}</p></div>
      </div>
      <Collapsible><CollapsibleTrigger asChild><Button variant="outline" size="sm" className="gap-2"><ChevronDown className="size-3.5" />Selection evidence (validation grid)</Button></CollapsibleTrigger>
        <CollapsibleContent className="mt-3 space-y-3">
          <p className="text-xs text-muted-foreground">{fz.selection_rules.progressive}</p>
          <div className="overflow-x-auto rounded-xl border"><Table aria-label="Progressive expansion grid"><TableHeader><TableRow><TableHead>policy</TableHead><TableHead className="text-right">patience</TableHead><TableHead className="text-right">step</TableHead><TableHead className="text-right">macro nDCG@10</TableHead><TableHead className="text-right">records examined (share of global)</TableHead><TableHead>non-inferior</TableHead></TableRow></TableHeader>
            <TableBody>{p.map((t, i) => <TableRow key={i} className={cn(t.config.policy === sel.progressive.selected.policy && t.config.patience === sel.progressive.selected.patience && t.config.step === sel.progressive.selected.step && "bg-violet-soft/60 font-medium")}><TableCell>{t.config.policy}</TableCell><TableCell className="text-right">{t.config.patience}</TableCell><TableCell className="text-right">{t.config.step}</TableCell><TableCell className="text-right tabular-nums">{f4(t.macro_ndcg)}</TableCell><TableCell className="text-right tabular-nums">{pct(t.macro_work_share)}</TableCell><TableCell>{t.non_inferior ? "yes" : "no"}</TableCell></TableRow>)}</TableBody></Table></div>
          <p className="text-xs text-muted-foreground">{fz.selection_rules.soft}</p>
          <div className="overflow-x-auto rounded-xl border"><Table aria-label="Soft routing grid"><TableHeader><TableRow><TableHead className="text-right">weight λ</TableHead><TableHead className="text-right">graph share</TableHead><TableHead className="text-right">macro nDCG@10</TableHead>{Object.keys(s[0].ndcg_delta_vs_dense).map((d) => <TableHead key={d} className="text-right">Δ {d}</TableHead>)}</TableRow></TableHeader>
            <TableBody>{s.map((t, i) => <TableRow key={i} className={cn(t.config.weight === sel.soft.selected.weight && t.config.share === sel.soft.selected.share && "bg-violet-soft/60 font-medium")}><TableCell className="text-right">{t.config.weight}</TableCell><TableCell className="text-right">{t.config.share}</TableCell><TableCell className="text-right tabular-nums">{f4(t.macro_ndcg)}</TableCell>{Object.values(t.ndcg_delta_vs_dense).map((v, j) => <TableCell key={j} className="text-right tabular-nums">{sgn(v)}</TableCell>)}</TableRow>)}</TableBody></Table></div>
        </CollapsibleContent></Collapsible>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------------------------- page tab
export function StrategyBenchmarks() {
  const runs = useStrategyRuns()
  const [pickedRun, setRun] = useState<string | null>(null)
  const [pickedDs, setDs] = useState<string | null>(null)
  const [pickedGroup, setGroup] = useState<StrategyGroup | null>(null)
  const [shown, setShown] = useState<StrategyName[]>(NAMES)
  const items = runs.data?.items ?? []
  const run = items.find((r) => r.run_id === pickedRun) ?? items.find((r) => Object.keys(r.evaluated).length > 0) ?? items[0] ?? null
  const dsNames = run ? Object.keys(run.evaluated) : []
  const ds = pickedDs && dsNames.includes(pickedDs) ? pickedDs : dsNames[0] ?? null
  const groups = (run && ds ? run.evaluated[ds] : []) as StrategyGroup[]
  const group: StrategyGroup = pickedGroup && groups.includes(pickedGroup) ? pickedGroup : groups.includes("test") ? "test" : "validation"
  const res = useStrategyGroup(run?.run_id ?? null, ds, group)
  const toggle = (v: string[]) => setShown(NAMES.filter((n) => v.includes(n)).length ? (NAMES.filter((n) => v.includes(n)) as StrategyName[]) : shown)

  if (runs.isLoading) return <LoadingBlock rows={3} />
  if (runs.isError) return <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
  if (!run) return <EmptyState icon={<FlaskConical className="size-5" />} title="No retrieval-strategy comparison has been saved yet" description="Run: python -m app.evaluation.run_strategies --datasets scifact,nfcorpus,arguana --run-id my_run  (needs MongoDB and the cached BEIR datasets; see SETUP.md). Results appear here as soon as they are written." />
  return (
    <div className="space-y-5" data-testid="strategy-benchmarks">
      <div className="flex flex-wrap items-center gap-3">
        <Select value={run.run_id} onValueChange={(v) => { setRun(v); setDs(null); setGroup(null) }}><SelectTrigger className="h-10 w-full max-w-sm rounded-xl text-xs" aria-label="Select strategy run"><SelectValue /></SelectTrigger>
          <SelectContent>{items.map((r) => <SelectItem key={r.run_id} value={r.run_id}>{r.run_id} · {r.status}{r.smoke ? " · smoke" : ""} · {r.datasets.join(", ")}</SelectItem>)}</SelectContent></Select>
        <Badge variant="outline">{run.status}</Badge>
        {run.frozen && <Badge variant="outline" className="gap-1"><Lock className="size-3" />configuration frozen</Badge>}
        <span className="text-xs text-muted-foreground">{dateTime(run.created_at)}</span>
        <div className="ml-auto flex gap-2">
          <Button asChild variant="outline" size="sm"><a href={strategyExportUrl(run.run_id, "json")} download><Download className="size-3.5" /> JSON</a></Button>
          <Button asChild variant="outline" size="sm"><a href={strategyExportUrl(run.run_id, "csv")} download><Download className="size-3.5" /> CSV</a></Button>
        </div>
      </div>
      {!dsNames.length ? <EmptyState title="Nothing evaluated in this run yet" description={run.frozen ? "The configuration is frozen; the evaluation phase has not written results." : "The tuning phase is still running or has not finished."} /> : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <Tabs value={ds ?? dsNames[0]} onValueChange={setDs} className="min-w-0 max-w-full"><TabsList className="max-w-full justify-start overflow-x-auto">{dsNames.map((n) => <TabsTrigger key={n} value={n}>{n}</TabsTrigger>)}</TabsList></Tabs>
            <Tabs value={group} onValueChange={(v) => setGroup(v as StrategyGroup)} className="min-w-0 max-w-full"><TabsList className="max-w-full justify-start overflow-x-auto">{(["validation", "test"] as StrategyGroup[]).filter((g) => groups.includes(g)).map((g) => <TabsTrigger key={g} value={g}>{g === "validation" ? "Validation" : "Test (frozen)"}</TabsTrigger>)}</TabsList></Tabs>
            <ToggleGroup type="multiple" variant="outline" size="sm" value={shown} onValueChange={toggle} aria-label="Strategies shown" className="flex-wrap">
              {NAMES.map((n) => <ToggleGroupItem key={n} value={n} aria-label={`strategy ${n}`} className="h-9 px-3 text-xs">{LABEL[n]}</ToggleGroupItem>)}
            </ToggleGroup>
          </div>
          {res.isLoading ? <LoadingBlock rows={4} /> : res.isError ? <ErrorState error={res.error} onRetry={() => res.refetch()} /> : res.data && ds ? (
            <div className="space-y-5">
              <StatusBadge r={res.data} />
              <Section title="Retrieval quality"><Quality r={res.data} shown={shown} /></Section>
              <Section title="Paired comparison against the controls"><div className="space-y-3"><Comparisons r={res.data} shown={shown} /><Explain r={res.data} shown={shown} /></div></Section>
              <Section title="Latency, candidate pool and fallback"><Efficiency r={res.data} shown={shown} /></Section>
              <Section title="Latency on an indexed Qdrant server" description="The local in-process Qdrant used above makes every filtered search expensive regardless of how few records are eligible; this is the same comparison where it does not."><ServerLatency runId={run.run_id} ds={ds} shown={shown} /></Section>
              <Section title="Routing failure analysis" description="Counting rules: a query is improved or degraded when its nDCG@10 differs by more than 1e-12; “excluded by routing” = a relevant document in a module the strategy never searched."><Diagnostics r={res.data} shown={shown} /></Section>
              <Section title="Per-query inspector" description="Sorted by the change in nDCG@10 between two strategies. Open a query to see every strategy's result for it."><Inspector runId={run.run_id} ds={ds} group={group} shown={shown} /></Section>
              <Section title="Frozen configuration and how it was chosen"><Frozen runId={run.run_id} /></Section>
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
