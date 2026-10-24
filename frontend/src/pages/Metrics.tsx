import { useState } from "react"
import { toast } from "sonner"
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts"
import { BarChart3, FlaskConical, GitCompare, RefreshCw } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { DecisionChecks, MetricsCompare } from "@/components/app/MetricsCompare"
import { EmptyState, ErrorState, LoadingBlock, errorMessage } from "@/components/app/states"
import { MonoId, PageHeader, Section, StatCard } from "@/components/app/ui-bits"
import {
  useCompareModes, useCompareVersions, useEvalDataset, useEvalRuns, useMetricsHistory, useReloadDataset, useRunEvaluation, useSummary, useVersions,
} from "@/api/hooks"
import { NO_DATA, mb, ms, num, ratioPct, score, timeAgo } from "@/lib/format"
import type { ModeComparison } from "@/api/types"

const cfg = (entries: Record<string, string>): ChartConfig => Object.fromEntries(Object.entries(entries).map(([k, c], i) => [k, { label: k.replace(/_/g, " "), color: c || `var(--chart-${(i % 5) + 1})` }]))
const tick = (v: string) => (v.length > 11 ? v.slice(11, 16) : v.slice(5))

function ChartCard({ title, description, empty, children }: { title: string; description?: string; empty: boolean; children: React.ReactNode }) {
  return (
    <Section title={title} description={description} className="min-w-0">
      {empty ? <EmptyState className="py-8" title={NO_DATA} description="This chart fills in as the system records measurements." /> : children}
    </Section>
  )
}

function ModeTable({ cmp }: { cmp: ModeComparison }) {
  const rows: [string, keyof ModeComparison["modes"]][] = [["Global vector search", "global"], [`Fixed graph (${cmp.fixed_graph_version ?? "initial"})`, "fixed_graph"], [`Adaptive graph (${cmp.adaptive_graph_version ?? "active"})`, "adaptive_graph"]]
  const data = rows.map(([label, k]) => ({ mode: label.split(" (")[0], recall: cmp.modes[k].metrics.recall_at_5, mrr: cmp.modes[k].metrics.mrr, candidates: cmp.modes[k].metrics.mean_candidate_count }))
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto rounded-xl border">
        <Table aria-label="Retrieval mode comparison">
          <TableHeader><TableRow><TableHead>Mode</TableHead><TableHead className="text-right">Recall@5</TableHead><TableHead className="text-right">MRR</TableHead><TableHead className="text-right">p95</TableHead><TableHead className="text-right">Mean candidates</TableHead><TableHead className="text-right">n</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(([label, k]) => { const m = cmp.modes[k].metrics; return (
            <TableRow key={k}><TableCell className="font-medium">{label}</TableCell><TableCell className="text-right tabular-nums">{score(m.recall_at_5)}</TableCell><TableCell className="text-right tabular-nums">{score(m.mrr)}</TableCell><TableCell className="text-right tabular-nums">{ms(m.p95_latency_ms)}</TableCell><TableCell className="text-right tabular-nums">{m.mean_candidate_count?.toFixed(1) ?? NO_DATA}</TableCell><TableCell className="text-right tabular-nums">{m.sample_size}</TableCell></TableRow>) })}</TableBody>
        </Table>
      </div>
      <ChartContainer config={cfg({ recall: "var(--chart-1)", mrr: "var(--chart-2)" })} className="aspect-auto h-52 w-full">
        <BarChart data={data} margin={{ left: -16, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="mode" tickLine={false} axisLine={false} fontSize={11} /><YAxis domain={[0, 1]} tickLine={false} axisLine={false} fontSize={11} />
          <ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Bar dataKey="recall" fill="var(--color-recall)" radius={4} /><Bar dataKey="mrr" fill="var(--color-mrr)" radius={4} /></BarChart>
      </ChartContainer>
      <p className="text-xs text-muted-foreground">{cmp.note} Latency here excludes query embedding and is measured in isolated in-memory stores, so differences of a fraction of a millisecond are noise.</p>
    </div>
  )
}

export default function Metrics() {
  const [win, setWin] = useState("1h")
  const summary = useSummary()
  const history = useMetricsHistory(win)
  const dataset = useEvalDataset()
  const runs = useEvalRuns()
  const versions = useVersions()
  const runEval = useRunEvaluation()
  const compareModes = useCompareModes()
  const compareVersions = useCompareVersions()
  const reload = useReloadDataset()
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const s = summary.data
  const q = s?.retrieval_quality?.metrics
  const h = history.data
  const toast_ = (e: unknown) => toast.error(errorMessage(e).message)
  const vs = versions.data?.items ?? []

  return (
    <div className="space-y-6">
      <PageHeader title="Metrics & Evaluation" description="Retrieval quality is measured on a fixed labeled dataset; operational figures come from tracked live queries. Anything unmeasured says so."
        actions={
          <>
            <Button variant="outline" disabled={compareModes.isPending} onClick={() => compareModes.mutate(undefined, { onError: toast_ })}><GitCompare className="size-4" /> Compare modes</Button>
            <Button disabled={runEval.isPending} onClick={() => runEval.mutate({ mode: "graph" }, { onSuccess: (r) => (r.valid ? toast.success(`Recall@5 ${score(r.metrics.recall_at_5)} · MRR ${score(r.metrics.mrr)}`) : toast.warning(r.invalid_reason ?? "Evaluation is not valid")), onError: toast_ })}><FlaskConical className="size-4" /> {runEval.isPending ? "Evaluating…" : "Run evaluation"}</Button>
          </>
        } />
      {summary.isError && <ErrorState error={summary.error} onRetry={() => summary.refetch()} />}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" aria-label="Key metrics">
        <StatCard label="Recall@5" value={q ? score(q.recall_at_5) : null} loading={summary.isLoading} hint={q ? `n=${q.sample_size} · ${timeAgo(s!.retrieval_quality!.evaluated_at)}` : "run an evaluation"} />
        <StatCard label="MRR" value={q ? score(q.mrr) : null} loading={summary.isLoading} />
        <StatCard label="p95 latency (live)" value={s?.p95_latency_ms == null ? null : ms(s.p95_latency_ms)} loading={summary.isLoading} hint={`${s?.latency_samples ?? 0} queries`} />
        <StatCard label="Mean candidates" value={s?.average_candidate_count == null ? null : s.average_candidate_count.toFixed(1)} loading={summary.isLoading} hint="records in searched modules" />
        <StatCard label="Fallback rate" value={s?.fallback_rate == null ? null : ratioPct(s.fallback_rate)} loading={summary.isLoading} hint="global search used" />
        <StatCard label="Active tokens" value={s ? `${num(s.active_tokens)} (${s.usage_percent.toFixed(1)}%)` : null} loading={summary.isLoading} />
        <StatCard label="Process RAM" value={s ? mb(s.process_rss_mb) : null} loading={summary.isLoading} />
        <StatCard label="Qdrant disk" value={s ? mb(s.qdrant_storage_mb) : null} loading={summary.isLoading} />
        <StatCard label="Graph nodes" value={s ? num(s.node_count) : null} loading={summary.isLoading} />
        <StatCard label="Graph edges" value={s ? num(s.edge_count) : null} loading={summary.isLoading} />
      </section>

      <Section title="Evaluation dataset" description="Fixed, labeled queries used for every comparison and every accept/reject decision."
        actions={<Button size="sm" variant="ghost" disabled={reload.isPending} onClick={() => reload.mutate(undefined, { onSuccess: () => toast.success("Fixture reloaded"), onError: toast_ })}><RefreshCw className="size-3.5" /> Reload fixture</Button>}>
        {dataset.isLoading ? <LoadingBlock rows={1} /> : dataset.isError ? <ErrorState error={dataset.error} onRetry={() => dataset.refetch()} /> : dataset.data && (
          <div className="grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div><div className="text-xs text-muted-foreground">Labeled queries</div><b className="text-xl tabular-nums">{dataset.data.query_count}</b> <span className="text-xs text-muted-foreground">({Object.entries(dataset.data.splits).map(([k, v]) => `${v} ${k}`).join(", ") || "none"})</span></div>
            <div><div className="text-xs text-muted-foreground">Usable now</div><b className="text-xl tabular-nums">{dataset.data.usable_queries}</b> <span className="text-xs text-muted-foreground">{dataset.data.skipped_queries} skipped (labeled memories not active)</span></div>
            <div><div className="text-xs text-muted-foreground">Status</div>{dataset.data.valid ? <Badge variant="outline" className="text-success">valid</Badge> : <Badge variant="outline" className="text-destructive">invalid</Badge>} <span className="text-xs text-muted-foreground">min {dataset.data.min_eval_queries} required</span></div>
            <div><div className="text-xs text-muted-foreground">Dataset hash</div><MonoId id={dataset.data.dataset_hash} short={16} /></div>
            {dataset.data.issues.length > 0 && <div className="sm:col-span-2 lg:col-span-4"><ErrorState error={new Error(dataset.data.issues.join("; "))} /></div>}
            <div className="sm:col-span-2 lg:col-span-4 rounded-xl bg-muted/50 p-3 text-xs text-muted-foreground">Acceptance thresholds: Recall@5 regression ≤ <b>{String(dataset.data.thresholds.max_recall_regression)}</b>, MRR ≤ <b>{String(dataset.data.thresholds.max_mrr_regression)}</b>, p95 latency ≤ <b>+{String(dataset.data.thresholds.max_p95_latency_regression_pct)}%</b> (noise floor {String(dataset.data.thresholds.latency_noise_floor_ms)} ms). Missing or insufficient data is never treated as success.</div>
          </div>
        )}
      </Section>

      {compareModes.isError && <ErrorState error={compareModes.error} />}
      {compareModes.data && <Section title="Global vs fixed graph vs adaptive graph" description="Identical dataset, labels, top-k and stored memories — only the retrieval path or architecture differs."><ModeTable cmp={compareModes.data} /></Section>}

      <Section title="Compare two architecture versions" description="Both are aligned to the currently active memories and evaluated with the same procedure.">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          {(["Baseline", "Candidate"] as const).map((label, i) => (
            <div key={label} className="flex-1 space-y-1.5"><span className="text-sm font-medium">{label}</span>
              <Select value={i === 0 ? from : to} onValueChange={i === 0 ? setFrom : setTo}><SelectTrigger aria-label={`${label} version`}><SelectValue placeholder="Select version" /></SelectTrigger>
                <SelectContent>{vs.map((v) => <SelectItem key={v.version_id} value={v.version_id}>{v.version_id} · {v.operation_type}{v.is_active ? " (active)" : ""}</SelectItem>)}</SelectContent></Select></div>))}
          <Button disabled={!from || !to || from === to || compareVersions.isPending} onClick={() => compareVersions.mutate({ from_version: from, to_version: to }, { onError: toast_ })}>{compareVersions.isPending ? "Comparing…" : "Compare"}</Button>
        </div>
        {compareVersions.isError && <ErrorState className="mt-4" error={compareVersions.error} />}
        {compareVersions.data?.evaluation && (
          <div className="mt-4 space-y-4"><p className="text-sm">{compareVersions.data.summary}</p>
            <MetricsCompare before={compareVersions.data.evaluation.from} after={compareVersions.data.evaluation.to} beforeLabel={compareVersions.data.from_version} afterLabel={compareVersions.data.to_version} />
            <p className="text-xs text-muted-foreground">Policy verdict for {compareVersions.data.from_version} → {compareVersions.data.to_version}: <b>{compareVersions.data.evaluation.decision.decision}</b>. {compareVersions.data.evaluation.decision.reason}</p>
            <DecisionChecks checks={compareVersions.data.evaluation.decision.checks} /></div>)}
      </Section>

      <div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-lg font-semibold"><BarChart3 className="size-5 text-primary" /> Trends</h2>
        <Tabs value={win} onValueChange={setWin} className="min-w-0"><TabsList>{["1h", "24h", "7d"].map((w) => <TabsTrigger key={w} value={w}>{w}</TabsTrigger>)}</TabsList></Tabs></div>
      {history.isLoading ? <LoadingBlock rows={2} /> : history.isError ? <ErrorState error={history.error} onRetry={() => history.refetch()} /> : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Retrieval quality over evaluations" description="Live evaluations on the labeled dataset" empty={!h?.evaluation_points.length}>
            <ChartContainer config={cfg({ recall_at_5: "var(--chart-1)", mrr: "var(--chart-2)" })} className="aspect-auto h-56 w-full"><LineChart data={h?.evaluation_points} margin={{ left: -16, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis domain={[0, 1]} tickLine={false} axisLine={false} fontSize={11} /><ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Line dataKey="recall_at_5" stroke="var(--color-recall_at_5)" dot strokeWidth={2} /><Line dataKey="mrr" stroke="var(--color-mrr)" dot strokeWidth={2} /></LineChart></ChartContainer>
          </ChartCard>
          <ChartCard title="p95 query latency (live)" description="From tracked queries, per time bucket" empty={!h?.query_points.length}>
            <ChartContainer config={cfg({ p95_latency_ms: "var(--chart-3)" })} className="aspect-auto h-56 w-full"><LineChart data={h?.query_points} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} unit=" ms" /><ChartTooltip content={<ChartTooltipContent />} /><Line dataKey="p95_latency_ms" stroke="var(--color-p95_latency_ms)" dot={false} strokeWidth={2} /></LineChart></ChartContainer>
          </ChartCard>
          <ChartCard title="Candidate count and fallback rate" description="Search-space size per query; share of queries that needed global fallback" empty={!h?.query_points.length}>
            <ChartContainer config={cfg({ mean_candidate_count: "var(--chart-1)", fallback_rate: "var(--chart-5)" })} className="aspect-auto h-56 w-full"><LineChart data={h?.query_points} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis yAxisId="l" tickLine={false} axisLine={false} fontSize={11} /><YAxis yAxisId="r" orientation="right" domain={[0, 1]} tickLine={false} axisLine={false} fontSize={11} /><ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Line yAxisId="l" dataKey="mean_candidate_count" stroke="var(--color-mean_candidate_count)" dot={false} strokeWidth={2} /><Line yAxisId="r" dataKey="fallback_rate" stroke="var(--color-fallback_rate)" dot={false} strokeWidth={2} /></LineChart></ChartContainer>
          </ChartCard>
          <ChartCard title="Active-text tokens" description="Against the budget thresholds" empty={!h?.points.length}>
            <ChartContainer config={cfg({ usage_percent: "var(--chart-1)" })} className="aspect-auto h-56 w-full"><AreaChart data={h?.points} margin={{ left: -16, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis domain={[0, 100]} unit="%" tickLine={false} axisLine={false} fontSize={11} /><ChartTooltip content={<ChartTooltipContent />} />
              <ReferenceLine y={s?.thresholds.compression_start_pct ?? 75} stroke="var(--chart-3)" strokeDasharray="4 4" /><ReferenceLine y={s?.thresholds.emergency_cleanup_pct ?? 90} stroke="var(--destructive)" strokeDasharray="4 4" />
              <Area dataKey="usage_percent" stroke="var(--color-usage_percent)" fill="var(--color-usage_percent)" fillOpacity={0.18} strokeWidth={2} /></AreaChart></ChartContainer>
          </ChartCard>
          <ChartCard title="Graph size" description="Nodes and edges over time" empty={!h?.points.length}>
            <ChartContainer config={cfg({ module_count: "var(--chart-1)", edge_count: "var(--chart-4)" })} className="aspect-auto h-56 w-full"><LineChart data={h?.points} margin={{ left: -16, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} allowDecimals={false} /><ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Line type="stepAfter" dataKey="module_count" stroke="var(--color-module_count)" dot={false} strokeWidth={2} /><Line type="stepAfter" dataKey="edge_count" stroke="var(--color-edge_count)" dot={false} strokeWidth={2} /></LineChart></ChartContainer>
          </ChartCard>
          <ChartCard title="Process RAM and Qdrant disk" description="Observed resources — independent of the token budget" empty={!h?.points.length}>
            <ChartContainer config={cfg({ process_rss_mb: "var(--chart-2)", qdrant_storage_mb: "var(--chart-5)" })} className="aspect-auto h-56 w-full"><LineChart data={h?.points} margin={{ left: -8, right: 8 }}><CartesianGrid vertical={false} /><XAxis dataKey="timestamp" tickFormatter={tick} tickLine={false} axisLine={false} fontSize={11} /><YAxis tickLine={false} axisLine={false} fontSize={11} unit=" MB" /><ChartTooltip content={<ChartTooltipContent />} /><ChartLegend content={<ChartLegendContent />} /><Line dataKey="process_rss_mb" stroke="var(--color-process_rss_mb)" dot={false} strokeWidth={2} /><Line dataKey="qdrant_storage_mb" stroke="var(--color-qdrant_storage_mb)" dot={false} strokeWidth={2} /></LineChart></ChartContainer>
          </ChartCard>
        </div>
      )}

      <Section title="Recent evaluation runs">
        {runs.isLoading ? <LoadingBlock rows={2} /> : !runs.data?.items.length ? <EmptyState title="No evaluation has run yet" description="Run an evaluation to record Recall@5, MRR and p95 latency." /> : (
          <div className="overflow-x-auto rounded-xl border"><Table>
            <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Label</TableHead><TableHead>Mode</TableHead><TableHead className="text-right">Recall@5</TableHead><TableHead className="text-right">MRR</TableHead><TableHead className="text-right">p95</TableHead><TableHead className="text-right">n</TableHead></TableRow></TableHeader>
            <TableBody>{runs.data.items.slice(0, 12).map((r) => <TableRow key={r.run_id}><TableCell className="whitespace-nowrap text-xs text-muted-foreground">{timeAgo(r.evaluated_at)}</TableCell><TableCell className="max-w-48 truncate text-sm">{r.label}</TableCell><TableCell>{r.mode}</TableCell><TableCell className="text-right tabular-nums">{score(r.metrics.recall_at_5)}</TableCell><TableCell className="text-right tabular-nums">{score(r.metrics.mrr)}</TableCell><TableCell className="text-right tabular-nums">{ms(r.metrics.p95_latency_ms)}</TableCell><TableCell className="text-right tabular-nums">{r.metrics.sample_size}{!r.valid && <Badge variant="outline" className="ml-1 text-warning">insufficient</Badge>}</TableCell></TableRow>)}</TableBody>
          </Table></div>)}
      </Section>
    </div>
  )
}
