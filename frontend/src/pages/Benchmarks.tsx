import { useMemo, useState } from "react"
import { toast } from "sonner"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"
import { AlertTriangle, Download, FlaskConical, Loader2, Play, ShieldCheck } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock, errorMessage } from "@/components/app/states"
import { StatusBadge } from "@/components/app/StatusBadge"
import { MonoId, PageHeader, Section } from "@/components/app/ui-bits"
import { StrategyBenchmarks } from "@/components/app/StrategyBenchmarks"
import {
  benchmarkUrl, useBenchmarkCompare, useBenchmarkDatasets, useBenchmarkResults, useBenchmarkRuns, useBenchmarkStatus, useStartBenchmark,
} from "@/api/hooks"
import type { Bootstrap, BenchmarkComparison, BenchmarkDatasetResult, BenchmarkMethod, BenchmarkResults, QualityMetrics } from "@/api/types"
import { NO_DATA, dateTime, ms, num, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

const METHOD_LABEL: Record<string, string> = { bm25: "BM25", global_dense: "Global dense", fixed_graph: "Fixed-graph MORPHE", adaptive_graph: "Adaptive-graph MORPHE" }
const METHODS: BenchmarkMethod[] = ["bm25", "global_dense", "fixed_graph", "adaptive_graph"]
const QUALITY: { key: keyof QualityMetrics; label: string; headline?: boolean }[] = [
  { key: "ndcg_at_10", label: "nDCG@10", headline: true }, { key: "map_at_10", label: "MAP@10" }, { key: "recall_at_5", label: "Recall@5" },
  { key: "recall_at_10", label: "Recall@10" }, { key: "recall_at_100", label: "Recall@100" }, { key: "mrr_at_10", label: "MRR@10" },
  { key: "precision_at_10", label: "Precision@10" },
]
const f4 = (v: number | null | undefined) => (v === null || v === undefined || Number.isNaN(v) ? NO_DATA : v.toFixed(4))
const sgn = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}`

function ScopeBadge({ m }: { m: BenchmarkDatasetResult["manifest"] }) {
  if (m.smoke) return <Badge variant="outline" className="border-warning/50 bg-warning-soft text-warning">SMOKE — not an official result</Badge>
  if (m.official_test_split) return <Badge variant="outline" className="border-success/40 bg-success-soft text-success">official test split</Badge>
  return <Badge variant="outline" className="border-warning/50 bg-warning-soft text-warning">internal split — not the official test score</Badge>
}

// ------------------------------------------------------------------------------------------ run setup
function RunSetup() {
  const datasets = useBenchmarkDatasets()
  const status = useBenchmarkStatus()
  const start = useStartBenchmark()
  const [picked, setPicked] = useState<string[]>(["scifact"])
  const [methods, setMethods] = useState<BenchmarkMethod[]>([...METHODS])
  const [limit, setLimit] = useState("")
  const [download, setDownload] = useState(false)
  const running = !!status.data?.running
  const rows = datasets.data?.items ?? []
  const uncached = picked.filter((d) => !rows.find((r) => r.dataset_id === d)?.cached)
  const problems: string[] = []
  if (!picked.length) problems.push("Select at least one dataset.")
  if (!methods.length) problems.push("Select at least one method.")
  if (methods.includes("adaptive_graph") && !methods.includes("fixed_graph")) problems.push("Adaptive-graph is defined relative to fixed-graph: select both.")
  if (uncached.length && !download) problems.push(`Not cached locally: ${uncached.join(", ")}. Tick “allow download” to fetch from the BEIR host.`)
  if (limit && (!/^\d+$/.test(limit) || Number(limit) < 1)) problems.push("Query limit must be a positive whole number.")
  const toggle = <T,>(list: T[], v: T, set: (x: T[]) => void) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  return (
    <Section title="Run a benchmark" description="Official BEIR corpus, queries and qrels. Datasets are cached locally; nothing is substituted when one is missing.">
      {datasets.isLoading ? <LoadingBlock rows={2} /> : datasets.isError ? <ErrorState error={datasets.error} onRetry={() => datasets.refetch()} /> : (
        <div className="space-y-5">
          <fieldset className="grid gap-3 md:grid-cols-3">
            <legend className="sr-only">Datasets</legend>
            {rows.filter((r) => ["scifact", "nfcorpus", "arguana"].includes(r.dataset_id) || r.cached).map((r) => (
              <label key={r.dataset_id} className={cn("flex cursor-pointer gap-3 rounded-xl border p-3 text-sm transition", picked.includes(r.dataset_id) && "border-primary bg-violet-soft")}>
                <Checkbox checked={picked.includes(r.dataset_id)} onCheckedChange={() => toggle(picked, r.dataset_id, setPicked)} aria-label={`dataset ${r.dataset_id}`} className="mt-0.5" />
                <span className="min-w-0 space-y-1">
                  <span className="flex flex-wrap items-center gap-2 font-semibold">{r.dataset_id}
                    {r.cached ? <Badge variant="outline" className="text-success">cached</Badge> : <Badge variant="outline" className="text-muted-foreground">not downloaded</Badge>}</span>
                  <span className="block text-xs text-muted-foreground">{r.domain}</span>
                  {r.cached ? <span className="block text-xs text-muted-foreground">{num(r.corpus_size)} docs · {Object.entries(r.splits ?? {}).map(([k, v]) => `${k} ${v.queries}q`).join(" · ")} · {r.size_mb} MB</span>
                    : <span className="block text-xs text-muted-foreground">source: BEIR public host</span>}
                  <span className="block text-[11px] text-muted-foreground">license: {r.license}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap gap-x-5 gap-y-2">
            <legend className="mb-1 text-sm font-medium">Methods</legend>
            {METHODS.map((m) => (
              <label key={m} className="flex items-center gap-2 text-sm"><Checkbox checked={methods.includes(m)} onCheckedChange={() => toggle(methods, m, setMethods)} aria-label={`method ${m}`} />{METHOD_LABEL[m]}</label>
            ))}
          </fieldset>
          <div className="grid items-end gap-4 sm:grid-cols-[1fr_1fr_auto]">
            <div className="space-y-1.5"><Label htmlFor="qlimit">Query limit <span className="text-muted-foreground">(smoke run, optional)</span></Label>
              <Input id="qlimit" inputMode="numeric" placeholder="empty = full official split" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} /></div>
            <label className="flex h-10 items-center gap-2 text-sm"><Checkbox checked={download} onCheckedChange={(v) => setDownload(v === true)} aria-label="allow download" /> allow download of missing datasets</label>
            <Button disabled={running || start.isPending || problems.length > 0}
              onClick={() => start.mutate({ datasets: picked, methods, query_limit: limit ? Number(limit) : null, download }, {
                onSuccess: (j) => toast.success(`Benchmark ${j.run_id} queued`), onError: (e) => toast.error(errorMessage(e).message) })}>
              {running || start.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />} {running ? "Benchmark running…" : "Start benchmark"}
            </Button>
          </div>
          {limit && <p role="note" className="rounded-xl border border-warning/40 bg-warning-soft p-3 text-xs">A query limit makes this a <b>smoke run</b>: it is clearly marked non-official and must not be quoted as a BEIR benchmark score.</p>}
          {problems.length > 0 && <ul role="alert" className="space-y-1 text-xs text-destructive">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          {start.isError && <ErrorState error={start.error} />}
        </div>
      )}
    </Section>
  )
}

function LiveStatus() {
  const st = useBenchmarkStatus()
  const job = st.data?.active_run
  if (!st.data?.running || !job) return null
  return (
    <section aria-live="polite" className="surface space-y-3 border-info/40 bg-info-soft p-4">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={job.status} /><MonoId id={job.run_id} short={28} /><span className="text-xs text-muted-foreground">started {timeAgo(job.started_at)}</span></div>
      <ul className="grid gap-1.5 text-sm sm:grid-cols-3">
        {Object.entries(job.datasets).map(([d, v]) => (
          <li key={d} className="rounded-xl border bg-card p-2.5"><div className="flex items-center justify-between"><b>{d}</b><StatusBadge status={v.status} /></div>
            {v.message && <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{v.phase}: {v.message}</p>}</li>))}
      </ul>
      <p className="text-xs text-muted-foreground">Status comes from the server job record. Latency is measured on this machine while it runs; avoid other heavy work for clean timings.</p>
    </section>
  )
}

// ------------------------------------------------------------------------------------------ results
function QualityTable({ d }: { d: BenchmarkDatasetResult }) {
  const m = d.metrics!
  const present = METHODS.filter((x) => m[x])
  const diffRow = (label: string, a: string, b: string) => m[a] && m[b] ? (
    <TableRow key={label} className="bg-muted/40 text-xs">
      <TableCell className="font-medium">{label}</TableCell>
      {QUALITY.map((q) => { const v = (m[b][q.key] as number) - (m[a][q.key] as number); return <TableCell key={q.key} className={cn("text-right tabular-nums", v > 1e-9 && "text-success", v < -1e-9 && "text-warning")}>{sgn(v)}</TableCell> })}
    </TableRow>) : null
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table aria-label="Retrieval quality by method">
        <TableHeader><TableRow><TableHead>Method</TableHead>{QUALITY.map((q) => <TableHead key={q.key} className={cn("text-right", q.headline && "text-foreground")}>{q.label}{q.headline ? " ★" : ""}</TableHead>)}</TableRow></TableHeader>
        <TableBody>
          {present.map((x) => (
            <TableRow key={x}><TableCell className="font-medium whitespace-nowrap">{METHOD_LABEL[x]}</TableCell>
              {QUALITY.map((q) => <TableCell key={q.key} className={cn("text-right tabular-nums", q.headline && "font-semibold")}>{f4(m[x][q.key] as number)}</TableCell>)}</TableRow>))}
          {diffRow("Fixed graph − global dense", "global_dense", "fixed_graph")}
          {diffRow("Adaptive graph − global dense", "global_dense", "adaptive_graph")}
          {diffRow("Adaptive graph − fixed graph", "fixed_graph", "adaptive_graph")}
        </TableBody>
      </Table>
    </div>
  )
}

function Efficiency({ d }: { d: BenchmarkDatasetResult }) {
  const lat = d.latency!
  const present = METHODS.filter((x) => lat[x])
  const chart = present.map((x) => ({ method: METHOD_LABEL[x].replace(" MORPHE", ""), p50: lat[x].end_to_end.p50_ms, p95: lat[x].end_to_end.p95_ms, candidates: lat[x].mean_eligible_candidates }))
  const cfg: ChartConfig = { p50: { label: "p50 end-to-end (ms)", color: "var(--chart-1)" }, p95: { label: "p95 end-to-end (ms)", color: "var(--chart-3)" }, candidates: { label: "eligible candidates", color: "var(--chart-2)" } }
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Latency and candidate pool by method">
          <TableHeader><TableRow><TableHead>Method</TableHead><TableHead className="text-right">p50 e2e</TableHead><TableHead className="text-right">p95 e2e</TableHead><TableHead className="text-right">p50 retrieval</TableHead><TableHead className="text-right">p95 retrieval</TableHead><TableHead className="text-right">query embed p50</TableHead><TableHead className="text-right">eligible candidates</TableHead><TableHead className="text-right">pool reduction</TableHead><TableHead className="text-right">fallback</TableHead></TableRow></TableHeader>
          <TableBody>{present.map((x) => { const v = lat[x]; return (
            <TableRow key={x}><TableCell className="font-medium whitespace-nowrap">{METHOD_LABEL[x]}</TableCell>
              <TableCell className="text-right tabular-nums">{ms(v.end_to_end.p50_ms)}</TableCell><TableCell className="text-right tabular-nums">{ms(v.end_to_end.p95_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{ms(v.retrieval.p50_ms)}</TableCell><TableCell className="text-right tabular-nums">{ms(v.retrieval.p95_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{x === "bm25" ? "—" : ms(v.query_embedding.p50_ms)}</TableCell>
              <TableCell className="text-right tabular-nums">{num(v.mean_eligible_candidates, 1)}</TableCell>
              <TableCell className="text-right tabular-nums">{v.candidate_pool_reduction_vs_corpus === null ? NO_DATA : `${(v.candidate_pool_reduction_vs_corpus * 100).toFixed(1)}%`}</TableCell>
              <TableCell className="text-right tabular-nums">{(v.fallback_rate * 100).toFixed(1)}%</TableCell></TableRow>) })}</TableBody>
        </Table>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartContainer config={cfg} className="aspect-auto h-56 w-full"><BarChart data={chart} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="method" tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} unit=" ms" /><ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Bar dataKey="p50" fill="var(--color-p50)" radius={4} /><Bar dataKey="p95" fill="var(--color-p95)" radius={4} /></BarChart></ChartContainer>
        <ChartContainer config={cfg} className="aspect-auto h-56 w-full"><BarChart data={chart} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="method" tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} /><ChartTooltip content={<ChartTooltipContent />} /><Bar dataKey="candidates" fill="var(--color-candidates)" radius={4} /></BarChart></ChartContainer>
      </div>
      {lat.fixed_graph && (
        <p className="text-xs text-muted-foreground">Graph routing stage costs (mean, fixed graph): seed selection {ms(lat.fixed_graph.stage_seed?.mean_ms)} · expansion {ms(lat.fixed_graph.stage_expand?.mean_ms)} · filtered search {ms(lat.fixed_graph.stage_search?.mean_ms)} · fallback {ms(lat.fixed_graph.stage_fallback?.mean_ms)}.
          “Eligible candidates” are the active documents inside the searched modules (plus the whole corpus when fallback ran); it is not a count of vectors the engine scanned. Fewer candidates do not by themselves mean faster or better retrieval — compare the quality and latency columns.</p>
      )}
    </div>
  )
}

function CI({ b }: { b: Bootstrap }) {
  return <span className={cn("tabular-nums", b.excludes_zero ? (b.mean_diff > 0 ? "text-success" : "text-warning") : "text-muted-foreground")}>{sgn(b.mean_diff)} [{sgn(b.ci95_low)}, {sgn(b.ci95_high)}]</span>
}

function Statistics({ comps }: { comps: BenchmarkComparison[] }) {
  const label: Record<string, string> = { adaptive_vs_fixed: "Adaptive − fixed graph", fixed_vs_global: "Fixed graph − global dense", adaptive_vs_global: "Adaptive graph − global dense", global_dense_vs_bm25: "Global dense − BM25" }
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Paired bootstrap comparisons">
          <TableHeader><TableRow><TableHead>Comparison</TableHead><TableHead className="text-right">Δ nDCG@10 [95% CI]</TableHead><TableHead className="text-right">Δ Recall@5 [95% CI]</TableHead><TableHead className="text-right">improved / regressed / same</TableHead><TableHead className="text-right">n</TableHead></TableRow></TableHeader>
          <TableBody>{comps.map((c) => (
            <TableRow key={c.comparison}><TableCell className="font-medium whitespace-nowrap">{label[c.comparison] ?? c.comparison}</TableCell>
              <TableCell className="text-right"><CI b={c.bootstrap.ndcg_at_10} /></TableCell><TableCell className="text-right"><CI b={c.bootstrap.recall_at_5} /></TableCell>
              <TableCell className="text-right tabular-nums">{c.bootstrap.ndcg_at_10.queries_improved} / {c.bootstrap.ndcg_at_10.queries_regressed} / {c.bootstrap.ndcg_at_10.queries_unchanged}</TableCell>
              <TableCell className="text-right tabular-nums">{c.queries}</TableCell></TableRow>))}</TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">Paired percentile bootstrap, {comps[0]?.bootstrap.ndcg_at_10.resamples.toLocaleString()} resamples, seed {comps[0]?.bootstrap.ndcg_at_10.seed}, resampling unit = query (both systems share each resample).
        An interval that contains 0 means this sample cannot distinguish the two systems; a higher aggregate score alone is not evidence of improvement. Intervals describe this query sample, not other query distributions.</p>
    </div>
  )
}

function Adaptation({ d, runId, dataset }: { d: BenchmarkDatasetResult; runId: string; dataset: string }) {
  const a = d.adaptation
  const [baseline, setBaseline] = useState<BenchmarkMethod>("fixed_graph")
  const [treatment, setTreatment] = useState<BenchmarkMethod>("adaptive_graph")
  const cmp = useBenchmarkCompare(runId, dataset, baseline, treatment, !!d.comparisons?.length)
  const s = d.safety
  const [showAll, setShowAll] = useState(false)
  if (!a) return <EmptyState title="No adaptation was run" description="Select both fixed-graph and adaptive-graph to include adaptation evidence." />
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span>Proposals evaluated <b>{a.proposals_evaluated}</b></span><span>Accepted <b className="text-success">{a.accepted}</b></span><span>Rejected <b className="text-warning">{a.rejected}</b></span>
        <span>Failed <b>{a.failed}</b></span><span>Rolled back <b>{a.rolled_back}</b></span><span className="text-muted-foreground">versions {a.initial_version} → {a.adaptive_version}</span>
      </div>
      {a.events.length === 0 ? <p className="text-sm text-muted-foreground">The detector proposed no structural change for this workload.</p> : (
        <ul className="space-y-2" aria-label="Adaptation events">
          {a.events.filter((e) => e.operation !== "restore_version")
            .filter((e) => showAll || e.status !== "rejected" || e.trigger === "safety_probe").map((e) => (
            <li key={e.event_id} className="surface space-y-2 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2"><b>{e.operation.replace(/_/g, " ")}</b><StatusBadge status={e.status} />
                {e.rejection_stage === "pre_commit" && <Badge variant="outline" className="text-[10px]">rejected before commit</Badge>}
                {e.trigger === "safety_probe" && <Badge variant="outline" className="text-[10px]">safety probe</Badge>}
                {e.new_version && <span className="mono-id">→ {e.new_version}</span>}{e.rolled_back_to && <span className="mono-id">rolled back to {e.rolled_back_to}</span>}</div>
              <p className="text-xs text-muted-foreground">{e.reason}</p>
              <p className="text-xs">{e.decision_reason ?? e.error}</p>
              {e.baseline_metrics && e.candidate_metrics && (
                <p className="text-xs tabular-nums text-muted-foreground">validation nDCG@10 {f4(e.baseline_metrics.ndcg_at_10)} → {f4(e.candidate_metrics.ndcg_at_10)} · Recall@5 {f4(e.baseline_metrics.recall_at_5)} → {f4(e.candidate_metrics.recall_at_5)} · mean candidates {num(e.baseline_metrics.mean_candidate_count, 1)} → {num(e.candidate_metrics.mean_candidate_count, 1)} · affected: {(e.affected_modules ?? []).join(", ") || "—"}</p>)}
            </li>))}
        </ul>
      )}
      {a.rejected > 0 && (
        <Button variant="outline" size="sm" onClick={() => setShowAll((v) => !v)}>{showAll ? "Hide" : "Show"} {a.events.filter((e) => e.status === "rejected" && e.trigger !== "safety_probe").length} rejected proposals</Button>
      )}
      {s && !s.skipped && (
        <div className="rounded-xl border p-3" data-testid="safety-evidence">
          <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4 text-primary" /> Safety evidence (on this corpus)</h4>
          <ul className="space-y-1 text-xs">
            {s.harmful_candidate && <li>Harmful candidate (shuffled documents, stale profiles): <b>{s.harmful_candidate.status}</b>{s.harmful_candidate.rejection_stage ? ` (${s.harmful_candidate.rejection_stage})` : ""}; nDCG@10 {f4(s.harmful_candidate.baseline_ndcg_at_10)} → {f4(s.harmful_candidate.candidate_ndcg_at_10)}; state unchanged: <b>{String(s.harmful_candidate.state_unchanged)}</b></li>}
            {s.post_commit_fault && <li>Injected post-commit failure: <b>{s.post_commit_fault.status.replace("_", " ")}</b>{s.post_commit_fault.state_restored_exactly !== undefined && <> · state restored exactly: <b>{String(s.post_commit_fault.state_restored_exactly)}</b> · integrity: <b>{String(s.post_commit_fault.integrity_passed)}</b></>}{s.post_commit_fault.detail && ` — ${s.post_commit_fault.detail}`}</li>}
            {s.restoration && <li>Restore round-trip: <b>{s.restoration.restore_status ?? s.restoration.status}</b>{s.restoration.state_matches_pre_change_snapshot !== undefined && <> · matches pre-change snapshot: <b>{String(s.restoration.state_matches_pre_change_snapshot)}</b> · re-applied change matches: <b>{String(s.restoration.state_matches_changed_architecture)}</b></>}</li>}
          </ul>
          {s.note && <p className="mt-2 text-[11px] text-muted-foreground">{s.note}</p>}
        </div>
      )}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2"><h4 className="text-sm font-semibold">Per-query inspector</h4>
          {([["Baseline", baseline, setBaseline], ["Treatment", treatment, setTreatment]] as const).map(([lab, val, set]) => (
            <Select key={lab} value={val} onValueChange={(v) => set(v as BenchmarkMethod)}><SelectTrigger className="h-8 w-48 text-xs" aria-label={`${lab} method`}><SelectValue /></SelectTrigger>
              <SelectContent>{METHODS.map((m) => <SelectItem key={m} value={m}>{lab}: {METHOD_LABEL[m]}</SelectItem>)}</SelectContent></Select>))}
        </div>
        {cmp.isLoading ? <LoadingBlock rows={1} /> : cmp.isError ? <p className="text-xs text-muted-foreground">{errorMessage(cmp.error).message}</p> : cmp.data && (
          <div className="grid gap-3 lg:grid-cols-2">
            {([["Most improved", cmp.data.most_improved ?? []], ["Most regressed", cmp.data.most_regressed ?? []]] as const).map(([title, rows]) => (
              <div key={title} className="rounded-xl border p-3"><h5 className="mb-1.5 text-xs font-semibold">{title} (nDCG@10)</h5>
                {rows.length === 0 ? <p className="text-xs text-muted-foreground">none</p> : (
                  <ul className="space-y-1.5">{rows.slice(0, 6).map((r) => (
                    <li key={r.query_id} className="text-xs"><span className="line-clamp-2">{r.query}</span><span className="tabular-nums text-muted-foreground">{f4(r.ndcg_baseline)} → {f4(r.ndcg_treatment)} (</span><span className={cn("tabular-nums", r.delta > 0 ? "text-success" : "text-warning")}>{sgn(r.delta)}</span><span className="text-muted-foreground">) · {r.query_id}</span></li>))}</ul>)}
              </div>))}
          </div>)}
      </div>
    </div>
  )
}

function DatasetResult({ runId, name, d }: { runId: string; name: string; d: BenchmarkDatasetResult }) {
  const m = d.manifest
  if (m.status !== "completed") return <ErrorState error={new Error(`${name}: ${m.status}${m.error ? ` — ${m.error}` : ""}`)} />
  const counts = m.dataset!.counts
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2"><ScopeBadge m={m} /><span className="text-xs text-muted-foreground">
        corpus {num(counts.corpus)} docs · {m.dataset!.test_group_queries} test queries · {num(m.dataset!.test_group_qrels)} judgments (grades {m.dataset!.relevant_grades.join(",")}) · validation {m.validation?.queries} · workload {m.split_protocol?.sizes.workload} · seed {m.seed} · {m.duration_s}s</span></div>
      {(m.caveats?.length ?? 0) > 0 && <ul role="note" className="space-y-1 rounded-xl border border-warning/40 bg-warning-soft p-3 text-xs">{m.caveats!.map((c) => <li key={c} className="flex gap-2"><AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />{c}</li>)}</ul>}
      <Section title="Retrieval quality (BEIR metrics)" description="nDCG@10 is the headline metric; graded judgments are never binarised for it. Differences are shown against the stated baseline."><QualityTable d={d} /></Section>
      <Section title="Efficiency and routing overhead"><Efficiency d={d} /></Section>
      {d.comparisons && d.comparisons.length > 0 && <Section title="Statistical comparison"><Statistics comps={d.comparisons} /></Section>}
      {d.sweep && d.sweep.rows.length > 0 && (
        <Section title="Routing breadth sensitivity (validation group only)" description="How the fixed graph trades quality against the eligible candidate pool. Informational — the primary comparison keeps the default routing that was fixed before any evaluation.">
          <div className="overflow-x-auto rounded-xl border"><Table aria-label="Routing sensitivity">
            <TableHeader><TableRow><TableHead>Configuration</TableHead><TableHead className="text-right">nDCG@10</TableHead><TableHead className="text-right">Recall@5</TableHead><TableHead className="text-right">mean candidates</TableHead><TableHead className="text-right">p95 retrieval</TableHead></TableRow></TableHeader>
            <TableBody>{d.sweep.rows.map((r) => <TableRow key={r.config}><TableCell className="font-medium">{r.config}</TableCell><TableCell className="text-right tabular-nums">{f4(r.ndcg_at_10)}</TableCell><TableCell className="text-right tabular-nums">{f4(r.recall_at_5)}</TableCell><TableCell className="text-right tabular-nums">{num(r.mean_candidates, 0)}</TableCell><TableCell className="text-right tabular-nums">{ms(r.p95_latency_ms)}</TableCell></TableRow>)}</TableBody></Table></div>
          <p className="mt-2 text-xs text-muted-foreground">{d.sweep.queries} validation queries; {d.sweep.note}.</p>
        </Section>
      )}
      <Section title="Adaptation evidence" description="Every structural proposal was evaluated on the validation group only; the test group was untouched until the architecture was frozen."><Adaptation d={d} runId={runId} dataset={name} /></Section>
      <Section title="Reproducibility">
        <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
          <div><dt className="inline text-muted-foreground">Embedding: </dt><dd className="inline">{m.embedding?.model} ({m.embedding?.dimension}-d, cache {m.embedding?.cache_hit ? "hit" : "miss"})</dd></div>
          <div><dt className="inline text-muted-foreground">Initial graph: </dt><dd className="inline">{m.graph_init?.modules} modules, {m.graph_init?.groups} groups, size {m.graph_init?.module_size.min}–{m.graph_init?.module_size.max}</dd></div>
          <div><dt className="inline text-muted-foreground">Validation protocol: </dt><dd className="inline">{m.protocol?.protocol ?? "v1"} (interleaved latency: {String(m.protocol?.eval_interleaved ?? false)}, significance-gated benefit: {String(m.protocol?.benefit_requires_ci ?? false)}, cumulative guard: {String(m.protocol?.cumulative_guard ?? false)})</dd></div>
          <div><dt className="inline text-muted-foreground">Versions: </dt><dd className="inline">{m.architecture_version_before} → {m.architecture_version_after}</dd></div>
          <div><dt className="inline text-muted-foreground">Acceptance: </dt><dd className="inline">nDCG@10 ≤ {String(m.acceptance_thresholds?.max_ndcg_regression)}, Recall@5 ≤ {String(m.acceptance_thresholds?.max_recall_regression)}, MRR@10 ≤ {String(m.acceptance_thresholds?.max_mrr_regression)} abs, p95 ≤ +{String(m.acceptance_thresholds?.max_p95_latency_regression_pct)}%, benefit required: {String(m.acceptance_thresholds?.require_benefit)}</dd></div>
          <div><dt className="inline text-muted-foreground">Machine: </dt><dd className="inline">{m.environment?.platform}, {m.environment?.torch_device}, {m.environment?.memory_gb} GB · RSS {d.system?.process_rss_mb} MB · {d.system?.qdrant_storage}</dd></div>
          <div className="sm:col-span-2"><dt className="inline text-muted-foreground">Timing: </dt><dd className="inline">{m.timing_methodology}</dd></div>
        </dl>
        <div className="mt-3 flex flex-wrap gap-2">
          {["manifest.json", "metrics.json", "per_query_metrics.json", "latency.json", "comparisons.json", "adaptation.json", "run_global_dense.trec", "run_fixed_graph.trec", "run_adaptive_graph.trec", "run_bm25.trec"].filter((n) => !n.startsWith("run_") || (m as unknown as { methods?: string[] }).methods?.includes(n.slice(4, -5)) !== false).map((n) => (
            <a key={n} href={benchmarkUrl(`/runs/${runId}/artifacts/${name}/${n}`)} download className="rounded-full border px-2.5 py-1 text-xs hover:bg-muted">{n}</a>))}
        </div>
      </Section>
    </div>
  )
}

function Results({ runId }: { runId: string }) {
  const q = useBenchmarkResults(runId)
  const [dsPick, setDs] = useState<string | null>(null)
  const res: BenchmarkResults | undefined = q.data
  const names = useMemo(() => {
    const order = ["scifact", "nfcorpus", "arguana"]
    return Object.keys(res?.datasets ?? {}).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99) || a.localeCompare(b))
  }, [res])
  const ds = dsPick && names.includes(dsPick) ? dsPick : names[0] ?? null
  if (q.isLoading) return <LoadingBlock rows={3} />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  if (!res || !names.length) return <EmptyState title="No results saved for this run yet" description={res?.note ?? "Results appear when the run finishes."} />
  const multi = Object.keys(res.macro_average ?? {}).length > 0 && names.length > 1
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={ds ?? names[0]} onValueChange={setDs} className="min-w-0 max-w-full"><TabsList className="max-w-full justify-start overflow-x-auto">{names.map((n) => <TabsTrigger key={n} value={n}>{n}</TabsTrigger>)}</TabsList></Tabs>
        <div className="flex gap-2">
          <Button asChild variant="outline" size="sm"><a href={benchmarkUrl(`/runs/${runId}/export?format=json`)} download><Download className="size-3.5" /> JSON</a></Button>
          <Button asChild variant="outline" size="sm"><a href={benchmarkUrl(`/runs/${runId}/export?format=csv`)} download><Download className="size-3.5" /> CSV</a></Button>
        </div>
      </div>
      {ds && res.datasets[ds] && <DatasetResult runId={runId} name={ds} d={res.datasets[ds]} />}
      {multi && (
        <Section title="Macro-average across datasets" description={`${res.macro_note ?? "equal weight per dataset"}. Relevance judgments are never pooled across datasets.`}>
          <div className="overflow-x-auto rounded-xl border"><Table aria-label="Macro average">
            <TableHeader><TableRow><TableHead>Method</TableHead>{QUALITY.map((x) => <TableHead key={x.key} className="text-right">{x.label}</TableHead>)}<TableHead className="text-right">datasets</TableHead></TableRow></TableHeader>
            <TableBody>{METHODS.filter((m) => res.macro_average[m]).map((m) => <TableRow key={m}><TableCell className="font-medium">{METHOD_LABEL[m]}</TableCell>{QUALITY.map((x) => <TableCell key={x.key} className="text-right tabular-nums">{f4(res.macro_average[m][x.key] as number)}</TableCell>)}<TableCell className="text-right">{res.macro_average[m].datasets}</TableCell></TableRow>)}</TableBody></Table></div>
        </Section>
      )}
    </div>
  )
}

export default function Benchmarks() {
  const runs = useBenchmarkRuns()
  const status = useBenchmarkStatus()
  const [picked, setSelected] = useState<string | null>(null)
  const items = runs.data?.items ?? []
  const active = status.data?.active_run?.run_id
  const selected = picked && items.some((r) => r.run_id === picked) ? picked : (active ?? items[0]?.run_id ?? null)
  return (
    <Tabs defaultValue="beir" className="gap-6">
      <PageHeader title="Benchmark & Evaluation" description="BEIR-based evaluation: the original comparison of BM25, global dense, fixed-graph and adaptive-graph MORPHE, and the follow-up comparison of the retrieval strategies (progressive expansion and soft graph routing)."
        actions={<TabsList><TabsTrigger value="beir">BEIR benchmark</TabsTrigger><TabsTrigger value="strategies">Retrieval strategies</TabsTrigger></TabsList>} />
      <TabsContent value="strategies"><StrategyBenchmarks /></TabsContent>
      <TabsContent value="beir" className="space-y-6">
      <RunSetup />
      <LiveStatus />
      <section className="space-y-3" aria-label="Benchmark runs">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><FlaskConical className="size-5 text-primary" /> Results</h2>
          {items.length > 0 && (
            <Select value={selected ?? undefined} onValueChange={setSelected}>
              <SelectTrigger className="h-9 w-full max-w-md text-xs" aria-label="Select benchmark run"><SelectValue placeholder="Select a run" /></SelectTrigger>
              <SelectContent>{items.map((r) => <SelectItem key={r.run_id} value={r.run_id}>{r.run_id} · {r.status}{r.official === false ? " · smoke" : ""} · {Object.keys(r.datasets).join(", ")}</SelectItem>)}</SelectContent>
            </Select>)}
        </div>
        {runs.isLoading ? <LoadingBlock rows={2} /> : runs.isError ? <ErrorState error={runs.error} onRetry={() => runs.refetch()} /> : items.length === 0 ? (
          <EmptyState icon={<FlaskConical className="size-5" />} title="No benchmark has been run yet" description="Start one above, or from a terminal: python -m app.evaluation.run_beir --datasets scifact. Results appear here as soon as they are saved." />
        ) : selected ? (
          <>
            {items.find((r) => r.run_id === selected) && (() => { const j = items.find((r) => r.run_id === selected)!; return (
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><StatusBadge status={j.status} /><MonoId id={j.run_id} short={28} /><span>{dateTime(j.created_at)}</span>
                {j.error && <span className="text-destructive">{j.error}</span>}</div>) })()}
            <Results runId={selected} />
          </>
        ) : null}
      </section>
      </TabsContent>
    </Tabs>
  )
}
