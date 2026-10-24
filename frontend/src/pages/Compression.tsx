import { useState } from "react"
import { AlertTriangle, Archive, FlaskConical, Play, RotateCcw, Siren } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock, QueryBoundary, errorMessage } from "@/components/app/states"
import { StatusBadge } from "@/components/app/StatusBadge"
import { ConfirmDialog, PageHeader, Section, StatCard, TokenGauge } from "@/components/app/ui-bits"
import { useCompressionEvents, useCompressionStatus, useConfig, useDemo, useRunCompression, useSetBudget, useSummary } from "@/api/hooks"
import { mb, num, opLabel, timeAgo } from "@/lib/format"
import type { CompressionEvent, CompressionRun } from "@/api/types"

function Totals({ events }: { events: CompressionEvent[] }) {
  const ops = events.filter((e) => e.scope === "operation")
  const done = ops.filter((e) => e.status === "completed")
  const sum = (f: (e: CompressionEvent) => number) => done.reduce((a, e) => a + f(e), 0)
  const dup = done.filter((e) => e.operation.includes("duplicate")).reduce((a, e) => a + (e.source_count ?? 0), 0)
  return (
    <section className="grid grid-cols-2 gap-3 lg:grid-cols-5" aria-label="Compression totals">
      <StatCard label="Tokens reclaimed" value={num(sum((e) => e.tokens_reclaimed ?? 0))} hint="completed operations" />
      <StatCard label="Duplicates removed" value={num(dup)} />
      <StatCard label="Summaries created" value={num(done.filter((e) => e.operation === "summarize_group").length)} />
      <StatCard label="Failed operations" value={num(ops.filter((e) => e.status === "failed").length)} hint="originals stay active" />
      <StatCard label="Skipped" value={num(ops.filter((e) => e.status === "skipped").length)} hint="with reasons below" />
    </section>
  )
}

function RunResult({ run }: { run: CompressionRun }) {
  return (
    <Section title={run.dry_run ? "Simulation result (nothing was changed)" : "Last run result"} description={`${run.mode} mode · ${run.usage_before_pct.toFixed(1)}% → ${run.usage_after_pct.toFixed(1)}% · ${run.target_reached ? "target reached" : "target not reached"}`}>
      <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <span>Before <b className="tabular-nums">{num(run.tokens_before)}</b></span><span>After <b className="tabular-nums">{num(run.tokens_after)}</b></span>
        <span>Reclaimed <b className="tabular-nums text-success">{num(run.tokens_reclaimed)}</b></span>
        <span>Duplicates <b>{run.duplicates_removed}</b></span><span>Summaries <b>{run.summaries_created}</b></span><span>Deleted <b>{run.records_deleted}</b></span>
      </div>
      {run.operations.length === 0 ? <p className="text-sm text-muted-foreground">No eligible redundancy was found, so no operation was needed.</p> : (
        <ul className="space-y-1.5 text-sm">
          {run.operations.map((o) => <li key={o.event_id} className="flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2"><span className="font-medium">{opLabel(o.operation)}</span><StatusBadge status={o.status} /><span className="text-muted-foreground tabular-nums">−{num(o.tokens_reclaimed)} tokens</span>{o.reason && <span className="w-full text-xs text-muted-foreground">{o.reason}</span>}</li>)}
        </ul>
      )}
    </Section>
  )
}

function EventsTable({ scope }: { scope: "job" | "operation" }) {
  const q = useCompressionEvents(scope, 50)
  return (
    <QueryBoundary query={q} rows={4} isEmpty={(d) => d.items.length === 0} empty={<EmptyState icon={<Archive className="size-5" />} title={scope === "job" ? "No compression jobs yet" : "No operations yet"} description="Compression starts automatically at the configured threshold, or run it manually above." />}>
      {(d) => (
        <>
          <div className="surface hidden overflow-hidden md:block">
            <Table>
              <TableHeader><TableRow><TableHead>When</TableHead><TableHead>Operation</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Tokens before → after</TableHead><TableHead className="text-right">Reclaimed</TableHead><TableHead className="w-[30%]">Reason</TableHead></TableRow></TableHeader>
              <TableBody>
                {d.items.map((e) => (
                  <TableRow key={e.event_id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">{timeAgo(e.timestamp)}</TableCell>
                    <TableCell className="font-medium">{opLabel(e.operation)}{e.mode ? <span className="ml-1 text-xs text-muted-foreground">({e.mode})</span> : null}</TableCell>
                    <TableCell><StatusBadge status={e.status} /></TableCell>
                    <TableCell className="text-right tabular-nums">{e.tokens_before === null ? "—" : `${num(e.tokens_before)} → ${num(e.tokens_after)}`}</TableCell>
                    <TableCell className="text-right tabular-nums">{e.tokens_reclaimed === null ? "—" : num(e.tokens_reclaimed)}</TableCell>
                    <TableCell className="whitespace-normal text-xs text-muted-foreground">{e.error ? <span className="text-destructive">{e.error}</span> : e.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="space-y-2 md:hidden">
            {d.items.map((e) => (
              <li key={e.event_id} className="surface space-y-1.5 p-3 text-sm">
                <div className="flex items-center justify-between gap-2"><span className="font-medium">{opLabel(e.operation)}</span><StatusBadge status={e.status} /></div>
                <p className="text-xs text-muted-foreground">{e.error ?? e.reason}</p>
                <div className="flex justify-between text-xs tabular-nums text-muted-foreground"><span>{e.tokens_before === null ? "—" : `${num(e.tokens_before)} → ${num(e.tokens_after)}`}</span><span>{e.tokens_reclaimed === null ? "" : `−${num(e.tokens_reclaimed)}`} · {timeAgo(e.timestamp)}</span></div>
              </li>
            ))}
          </ul>
        </>
      )}
    </QueryBoundary>
  )
}

export default function Compression() {
  const status = useCompressionStatus()
  const summary = useSummary()
  const config = useConfig()
  const events = useCompressionEvents(undefined, 100)
  const run = useRunCompression()
  const budget = useSetBudget()
  const demo = useDemo()
  const [last, setLast] = useState<CompressionRun | null>(null)
  const [confirmEmergency, setConfirmEmergency] = useState(false)
  const [budgetInput, setBudgetInput] = useState("")
  const [tab, setTab] = useState<"job" | "operation">("job")
  const s = status.data
  const busy = run.isPending || s?.state === "running"

  const doRun = (mode: "auto" | "normal" | "emergency", dry_run: boolean) =>
    run.mutate({ mode, dry_run }, {
      onSuccess: (r) => { setLast(r); toast.success(dry_run ? `Simulated: would reclaim ${num(r.tokens_reclaimed)} tokens` : `Reclaimed ${num(r.tokens_reclaimed)} tokens`) },
      onError: (e) => toast.error(errorMessage(e).message),
    })

  return (
    <div className="space-y-6">
      <PageHeader title="Compression Center" description="Adaptive memory compression reduces redundant active text under token-budget pressure. It is independent of structural adaptation."
        actions={
          <>
            <Button variant="outline" disabled={busy} onClick={() => doRun("auto", true)}><FlaskConical className="size-4" /> Simulate</Button>
            <Button disabled={busy} onClick={() => doRun("normal", false)}><Play className="size-4" /> Run compression</Button>
            <Button variant="destructive" disabled={busy} onClick={() => setConfirmEmergency(true)}><Siren className="size-4" /> Emergency cleanup</Button>
          </>
        } />
      {status.isError && <ErrorState error={status.error} onRetry={() => status.refetch()} />}
      {s && (
        <Section title="Active-text token budget" description="Thresholds come from configuration. Token reduction is not RAM or disk reduction — those are measured separately below.">
          <div className="space-y-5">
            <TokenGauge usage={s.usage_percent} start={s.start_threshold_percent} target={s.cleanup_target_percent} emergency={s.emergency_threshold_percent} tokens={s.active_tokens} budget={s.budget_tokens} />
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="flex items-center gap-2">State <StatusBadge status={s.state} /></span>
              <span className="text-muted-foreground">{s.band === "normal" ? "Below the compression threshold" : s.band === "pressure" ? `Above ${s.start_threshold_percent}% — normal compression applies` : `Above ${s.emergency_threshold_percent}% — emergency cleanup applies`}</span>
              <span className="text-muted-foreground">Normal target ≤ {s.normal_target_percent}% · emergency target ≤ {s.cleanup_target_percent}%</span>
              {s.cooldown_remaining_seconds > 0 && <span className="text-muted-foreground">cooldown {Math.ceil(s.cooldown_remaining_seconds)}s</span>}
              {s.budget_overridden && <span className="rounded-full border border-warning/40 bg-warning-soft px-2 py-0.5 text-xs text-warning">demo budget override active</span>}
            </div>
            {s.last_run?.error && <div role="alert" className="flex items-start gap-2 rounded-xl border border-destructive/40 bg-danger-soft p-3 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />Last run failed: {s.last_run.error}</div>}
          </div>
        </Section>
      )}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3" aria-label="Resource measurements">
        <StatCard label="Active tokens" value={s ? num(s.active_tokens) : null} loading={status.isLoading} hint="budgeted resource" />
        <StatCard label="Process RAM" value={summary.data ? mb(summary.data.process_rss_mb) : null} loading={summary.isLoading} hint="measured RSS, independent" />
        <StatCard label="Qdrant disk" value={summary.data ? mb(summary.data.qdrant_storage_mb) : null} loading={summary.isLoading} hint="measured, independent" />
      </section>
      {last && <RunResult run={last} />}
      {events.data && <Totals events={events.data.items} />}
      {config.data?.admin_endpoints && (
        <Section title="Demo controls" description="Development only: drop the token budget so thresholds are crossed with a small sample corpus.">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1 space-y-1.5"><label htmlFor="budget" className="text-sm font-medium">Budget override (tokens)</label>
              <Input id="budget" inputMode="numeric" placeholder={s ? String(s.budget_tokens) : "e.g. 4500"} value={budgetInput} onChange={(e) => setBudgetInput(e.target.value.replace(/\D/g, ""))} /></div>
            <Button variant="outline" disabled={!budgetInput || budget.isPending} onClick={() => budget.mutate(Number(budgetInput), { onSuccess: () => { toast.success("Budget override applied"); setBudgetInput("") }, onError: (e) => toast.error(errorMessage(e).message) })}>Apply</Button>
            <Button variant="ghost" disabled={budget.isPending || !s?.budget_overridden} onClick={() => budget.mutate(null, { onSuccess: () => toast.success("Configured budget restored") })}><RotateCcw className="size-4" /> Restore</Button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant="secondary" disabled={demo.isPending} onClick={() => demo.mutate({ action: "scenario", name: "compression" }, { onSuccess: () => toast.success("Loaded overlapping runbooks at ~80% usage"), onError: (e) => toast.error(errorMessage(e).message) })}>Load pressure scenario (~80%)</Button>
            <Button variant="secondary" disabled={demo.isPending} onClick={() => demo.mutate({ action: "scenario", name: "emergency" }, { onSuccess: () => toast.success("Loaded overlapping runbooks at ~93% usage"), onError: (e) => toast.error(errorMessage(e).message) })}>Load emergency scenario (~93%)</Button>
          </div>
        </Section>
      )}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3"><h2 className="text-lg font-semibold">Compression history</h2>
          <Tabs value={tab} onValueChange={(v) => setTab(v as "job" | "operation")}><TabsList><TabsTrigger value="job">Jobs</TabsTrigger><TabsTrigger value="operation">Operations</TabsTrigger></TabsList></Tabs></div>
        {events.isLoading ? <LoadingBlock /> : <EventsTable scope={tab} />}
      </section>
      <ConfirmDialog open={confirmEmergency} onOpenChange={setConfirmEmergency} destructive busy={run.isPending} confirmLabel="Run emergency cleanup"
        title="Run emergency cleanup?" description={<>After deduplication and summarisation, eligible low-value memories (not pinned, not critical) may be <b>permanently deleted</b> until usage is at most {s?.cleanup_target_percent ?? 82}%. Use “Simulate” first to preview.</>}
        onConfirm={() => { doRun("emergency", false); setConfirmEmergency(false) }} />
    </div>
  )
}
