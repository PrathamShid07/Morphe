import { useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
  Archive, ArrowRight, ArrowUp, Boxes, Clock, Cpu, Database, GitBranch, HardDrive, Layers, Network, Plus, Search, Target, Workflow,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Orb } from "@/components/app/Orb"
import { StatusBadge } from "@/components/app/StatusBadge"
import { EmptyState, ErrorState, QueryBoundary } from "@/components/app/states"
import { MonoId, Section, StatCard, TokenGauge } from "@/components/app/ui-bits"
import { useIngestDialog } from "@/components/app/IngestDialog"
import { useAdaptationEvents, useAdaptationStatus, useCompressionEvents, useCompressionStatus, useSummary } from "@/api/hooks"
import { mb, ms, num, opLabel, ratioPct, score, timeAgo } from "@/lib/format"
import { Progress } from "@/components/ui/progress"

function Hero() {
  const nav = useNavigate()
  const [q, setQ] = useState("")
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (q.trim()) nav(`/query?q=${encodeURIComponent(q.trim())}`)
  }
  return (
    <section className="relative flex flex-col items-center px-2 pb-8 pt-4 text-center sm:pt-8">
      <Orb className="size-24 sm:size-32" />
      <h1 className="mt-6 text-3xl font-bold tracking-tight sm:text-5xl">Memory that reshapes itself</h1>
      <p className="mt-3 max-w-xl text-balance text-sm text-muted-foreground sm:text-base">
        MORPHE routes queries through a module graph, then splits, merges and links modules from real usage — and only keeps a change when measured retrieval quality allows it.
      </p>
      <form onSubmit={submit} className="mt-6 flex w-full max-w-2xl items-center gap-2 rounded-2xl border bg-card p-2 pl-4 shadow-sm focus-within:ring-2 focus-within:ring-ring/40">
        <Search className="size-4 shrink-0 text-muted-foreground" />
        <Input aria-label="Ask the memory" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask your memory anything…" className="h-10 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0" />
        <Button type="submit" size="icon" className="size-10 shrink-0 rounded-xl" disabled={!q.trim()} aria-label="Run query"><ArrowUp className="size-4" /></Button>
      </form>
    </section>
  )
}

function QuickActions() {
  const { open } = useIngestDialog()
  const tiles = [
    { title: "Ingest content", text: "Chunk, embed and route new text into modules", icon: Plus, onClick: open },
    { title: "Run a query", text: "See which modules the graph selects and why", icon: Search, to: "/query" },
    { title: "Explore the graph", text: "Modules, hierarchy and co-access shortcuts", icon: Network, to: "/graph" },
    { title: "Evaluate adaptation", text: "Review proposals and measured before/after", icon: Workflow, to: "/adaptation" },
    { title: "Architecture history", text: "Versions, comparisons and rollback", icon: GitBranch, to: "/versions" },
  ]
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {tiles.map((t) => {
        const body = (
          <>
            <span className="tile-icon"><t.icon className="size-[18px]" /></span>
            <span className="min-w-0 text-left">
              <span className="block text-sm font-semibold">{t.title}</span>
              <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{t.text}</span>
            </span>
          </>
        )
        const cls = "surface flex min-h-[4.5rem] items-start gap-3 p-4 text-left transition hover:border-primary/50 hover:shadow-[0_0_0_1px_color-mix(in_oklab,var(--primary)_30%,transparent)] focus-visible:outline-2"
        return t.to ? <Link key={t.title} to={t.to} className={cls}>{body}</Link> : <button key={t.title} type="button" onClick={t.onClick} className={cls}>{body}</button>
      })}
    </div>
  )
}

function Processes() {
  const comp = useCompressionStatus()
  const adapt = useAdaptationStatus()
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Section title="Memory compression" description="Reduces redundant active text when the token budget is under pressure." actions={<Link to="/compression" className="text-xs font-medium text-accent-foreground hover:underline">Open <ArrowRight className="inline size-3" /></Link>}>
        <QueryBoundary query={comp} rows={2}>
          {(c) => (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={c.state} />
                <span className="text-xs text-muted-foreground">{c.band === "normal" ? "Below the compression threshold" : c.band === "pressure" ? "Compression threshold reached" : "Emergency threshold reached"}</span>
              </div>
              <div className="text-sm text-muted-foreground">
                {c.last_run ? <>Last run: <StatusBadge status={c.last_run.status} className="ml-1" /> reclaimed <b className="text-foreground">{num(c.last_run.tokens_reclaimed ?? 0)}</b> tokens{c.last_run.at ? ` · ${timeAgo(c.last_run.at)}` : ""}</> : "No compression has run yet."}
              </div>
            </div>
          )}
        </QueryBoundary>
      </Section>
      <Section title="Structural adaptation" description="Reorganises modules and links based on measured usage, validated before commit." actions={<Link to="/adaptation" className="text-xs font-medium text-accent-foreground hover:underline">Open <ArrowRight className="inline size-3" /></Link>}>
        <QueryBoundary query={adapt} rows={2}>
          {(a) => (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={a.running ? "running" : "idle"} />
                <span className="text-xs text-muted-foreground">
                  {a.queries_in_window} / {a.queries_per_window} queries in window · next check after {a.next_run_after_queries} more
                </span>
              </div>
              <Progress value={(a.queries_in_window / a.queries_per_window) * 100} className="h-1.5" aria-label="adaptation window progress" />
              <div className="text-sm text-muted-foreground">
                {a.last_operation ? <>Last: <b className="text-foreground">{opLabel(a.last_operation)}</b> {a.last_operation_status && <StatusBadge status={a.last_operation_status} className="ml-1" />}</> : "No structural change has been attempted yet."}
              </div>
            </div>
          )}
        </QueryBoundary>
      </Section>
    </div>
  )
}

function Activity() {
  const ad = useAdaptationEvents("all", 6)
  const cp = useCompressionEvents("job", 6)
  const items = [
    ...(ad.data?.items ?? []).map((e) => ({ id: e.event_id, kind: "Adaptation", title: opLabel(e.operation), text: e.decision_reason ?? e.reason, status: e.status, at: e.started_at, to: "/adaptation", icon: Workflow })),
    ...(cp.data?.items ?? []).map((e) => ({ id: e.event_id, kind: "Compression", title: opLabel(e.operation), text: `${e.reason}${e.tokens_reclaimed ? ` · reclaimed ${num(e.tokens_reclaimed)} tokens` : ""}`, status: e.status, at: e.timestamp, to: "/compression", icon: Archive })),
  ].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 8)
  const loading = ad.isLoading || cp.isLoading
  return (
    <Section title="Recent activity" description="Significant system events, newest first.">
      {loading ? <p className="text-sm text-muted-foreground">Loading…</p> : ad.isError && cp.isError ? <ErrorState error={ad.error} onRetry={() => { ad.refetch(); cp.refetch() }} /> : items.length === 0 ? (
        <EmptyState title="No activity yet" description="Ingest content and run queries — compression and adaptation events will appear here." />
      ) : (
        <ul className="divide-y">
          {items.map((i) => (
            <li key={i.id}>
              <Link to={i.to} className="flex items-start gap-3 py-3 hover:bg-muted/40">
                <span className="tile-icon mt-0.5 size-8 [&_svg]:size-4"><i.icon /></span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2 text-sm font-medium">{i.title}<StatusBadge status={i.status} /></span>
                  <span className="mt-0.5 line-clamp-2 block break-words text-xs text-muted-foreground">{i.text}</span>
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">{timeAgo(i.at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

export default function Overview() {
  const summary = useSummary()
  const s = summary.data
  const q = s?.retrieval_quality
  return (
    <div className="space-y-6">
      <Hero />
      <QuickActions />
      {summary.isError ? <ErrorState error={summary.error} onRetry={() => summary.refetch()} /> : null}
      <section aria-label="Key figures" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        <StatCard label="Active memories" icon={<Database />} loading={summary.isLoading} value={s ? num(s.active_memory_count) : null} hint={s ? `${num(s.source_count)} sources` : undefined} />
        <StatCard label="Active modules" icon={<Boxes />} loading={summary.isLoading} value={s ? num(s.module_count) : null} hint={s ? `${num(s.node_count)} graph nodes incl. groups` : undefined} />
        <StatCard label="Graph edges" icon={<Network />} loading={summary.isLoading} value={s ? num(s.edge_count) : null} hint="hierarchy + co-access shortcuts" />
        <StatCard label="Architecture" icon={<GitBranch />} loading={summary.isLoading} value={s?.architecture_version ? <MonoId id={s.architecture_version} short={20} className="text-lg font-semibold text-foreground" /> : null} hint={<Link to="/versions" className="hover:underline">view history</Link>} />
        <StatCard label="p95 query latency" icon={<Clock />} loading={summary.isLoading} value={s && s.p95_latency_ms !== null ? ms(s.p95_latency_ms) : null} hint={s ? `${s.latency_samples} recent queries` : undefined} />
        <StatCard label="Retrieval quality" icon={<Target />} loading={summary.isLoading} value={q ? `R@5 ${score(q.metrics.recall_at_5)}` : null} hint={q ? `MRR ${score(q.metrics.mrr)} · n=${q.sample_size}` : <Link to="/metrics" className="hover:underline">run an evaluation</Link>} />
        <StatCard label="Process RAM" icon={<Cpu />} loading={summary.isLoading} value={s ? mb(s.process_rss_mb) : null} hint="observed RSS (separate from tokens)" />
        <StatCard label="Qdrant storage" icon={<HardDrive />} loading={summary.isLoading} value={s ? mb(s.qdrant_storage_mb) : null} hint="local vector store on disk" />
      </section>
      <Section title="Active-text token budget" description="Tokens of active memory text versus the configured budget. This is not RAM or disk usage; those are shown separately above.">
        {s ? <TokenGauge usage={s.usage_percent} start={s.thresholds.compression_start_pct} target={82} emergency={s.thresholds.emergency_cleanup_pct} tokens={s.active_tokens} budget={s.budget_tokens} /> : <p className="text-sm text-muted-foreground">Loading…</p>}
      </Section>
      <Processes />
      <div className="grid gap-4 lg:grid-cols-2">
        <Activity />
        <Section title="Retrieval at a glance" description="Measured from tracked live queries.">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div><dt className="text-muted-foreground">Mean candidates / query</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{s?.average_candidate_count == null ? <span className="text-sm font-medium text-muted-foreground">Not enough data yet</span> : num(s.average_candidate_count, 1)}</dd></div>
            <div><dt className="text-muted-foreground">Global fallback rate</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{s?.fallback_rate == null ? <span className="text-sm font-medium text-muted-foreground">Not enough data yet</span> : ratioPct(s.fallback_rate)}</dd></div>
            <div><dt className="text-muted-foreground">p50 latency</dt><dd className="mt-1 text-xl font-semibold tabular-nums">{s?.p50_latency_ms == null ? <span className="text-sm font-medium text-muted-foreground">Not enough data yet</span> : ms(s.p50_latency_ms)}</dd></div>
            <div><dt className="text-muted-foreground">Evaluated at</dt><dd className="mt-1 text-sm font-medium">{q ? timeAgo(q.evaluated_at) : "never"}</dd></div>
          </dl>
          {s?.latency_note && <p className="mt-4 flex items-center gap-2 text-xs text-muted-foreground"><Layers className="size-3.5" />{s.latency_note}</p>}
        </Section>
      </div>
    </div>
  )
}
