import { useState } from "react"
import { toast } from "sonner"
import { ArrowRightLeft, Eye, GitMerge, Play, ScanSearch, Scissors, ShieldCheck, Sparkles, Workflow } from "lucide-react"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EventDetailDialog } from "@/components/app/EventDetail"
import { EmptyState, ErrorState, LoadingBlock, QueryBoundary, errorMessage } from "@/components/app/states"
import { StatusBadge } from "@/components/app/StatusBadge"
import { ConfirmDialog, MonoId, PageHeader, Section } from "@/components/app/ui-bits"
import {
  useAdaptationAction, useAdaptationEvents, useAdaptationStatus, useCandidates, useConfig, useDemo, useModules, useMonitor, useRunWindow, useScenarios,
} from "@/api/hooks"
import { opLabel, timeAgo } from "@/lib/format"
import type { AdaptationEvent, Proposal } from "@/api/types"

const ICON = { split_module: Scissors, merge_modules: GitMerge, add_shortcut_edge: ArrowRightLeft, restore_version: ShieldCheck } as const
const TABS = [["all", "All"], ["proposed", "Proposed"], ["completed", "Accepted"], ["rejected", "Rejected"], ["failed", "Failed"], ["rolled_back", "Rolled back"]] as const

function ProposalCard({ p, names, pending, pendingKind, onRun }: { p: Proposal; names: Record<string, string>; pending: boolean; pendingKind?: string; onRun: (p: Proposal, kind: "evaluate" | "apply") => void }) {
  const [confirm, setConfirm] = useState(false)
  const Icon = ICON[p.operation]
  const label = p.module_ids.map((m) => names[m] ?? m).join(" ↔ ")
  return (
    <li className="surface space-y-3 p-4" data-testid="proposal">
      <div className="flex flex-wrap items-center gap-2"><span className="tile-icon"><Icon className="size-[18px]" /></span><div className="min-w-0 flex-1"><div className="font-semibold">{opLabel(p.operation)}</div><div className="truncate text-xs text-muted-foreground">{label}</div></div>
        <Badge variant="outline">{p.affected_record_count ? `${p.affected_record_count} records` : "edge only"}</Badge></div>
      <p className="text-sm leading-relaxed">{p.reason}</p>
      <Accordion type="single" collapsible><AccordionItem value="e" className="border-0"><AccordionTrigger className="py-1 text-xs text-muted-foreground">Evidence and thresholds</AccordionTrigger>
        <AccordionContent><pre className="scroll-thin max-h-56 overflow-auto rounded-lg bg-muted/50 p-3 text-[11px]">{JSON.stringify({ evidence: p.evidence, thresholds: p.thresholds }, null, 2)}</pre></AccordionContent></AccordionItem></Accordion>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={pending} onClick={() => onRun(p, "evaluate")}><Eye className="size-4" /> {pending && pendingKind === "evaluate" ? "Evaluating…" : "Evaluate only"}</Button>
        <Button disabled={pending} onClick={() => setConfirm(true)}><Play className="size-4" /> Evaluate &amp; apply</Button>
      </div>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} busy={pending} confirmLabel="Evaluate & apply" title={`${opLabel(p.operation)}: ${label}`}
        description="The candidate architecture is built in isolation and measured against the same labeled queries as the current one. It is committed only if no metric regresses beyond the configured thresholds; otherwise nothing changes."
        onConfirm={() => { onRun(p, "apply"); setConfirm(false) }} />
    </li>
  )
}

export default function Adaptation() {
  const status = useAdaptationStatus()
  const cands = useCandidates()
  const modules = useModules()
  const config = useConfig()
  const [tab, setTab] = useState<string>("all")
  const events = useAdaptationEvents(tab, 50)
  const runWindow = useRunWindow()
  const monitor = useMonitor()
  const demo = useDemo()
  const scenarios = useScenarios(!!config.data?.admin_endpoints)
  const [selected, setSelected] = useState<AdaptationEvent | null>(null)
  const act = useAdaptationAction()
  // the mutation lives on the page (not on a proposal card): a successful apply refreshes the list and removes the card
  const runProposal = (p: Proposal, kind: "evaluate" | "apply") =>
    act.mutate({ kind, operation: p.operation, module_ids: p.module_ids }, {
      onSuccess: (ev) => { setSelected(ev); toast[ev.status === "completed" ? "success" : ev.status === "rejected" || ev.status === "failed" ? "warning" : "info"](`${opLabel(ev.operation)}: ${ev.status.replace("_", " ")}`) },
      onError: (e) => toast.error(errorMessage(e).message),
    })
  const names = Object.fromEntries((modules.data?.items ?? []).map((m) => [m.module_id, m.name]))
  const s = status.data

  const scenario = (name: string, label: string) =>
    demo.mutate({ action: "scenario", name }, { onSuccess: () => toast.success(`${label} loaded`), onError: (e) => toast.error(errorMessage(e).message) })

  return (
    <div className="space-y-6">
      <PageHeader title="Adaptation Center" description="Detected structural weaknesses, evidence-backed proposals, measured before/after results and every decision."
        actions={
          <>
            <Button variant="outline" disabled={monitor.isPending} onClick={() => monitor.mutate(undefined, { onSuccess: (r) => toast.info(`Regression check: ${r.status.replace("_", " ")}`), onError: (e) => toast.error(errorMessage(e).message) })}><ScanSearch className="size-4" /> Check for regression</Button>
            <Button disabled={runWindow.isPending || s?.running} onClick={() => runWindow.mutate(undefined, { onSuccess: (r) => toast.success(`Window processed: ${r.proposals_detected} proposals, ${r.applied} applied`), onError: (e) => toast.error(errorMessage(e).message) })}><Workflow className="size-4" /> Run window now</Button>
          </>
        } />
      {status.isError && <ErrorState error={status.error} onRetry={() => status.refetch()} />}
      {s && (
        <Section title="Adaptation window" description="Signals are accumulated over a fixed number of completed queries; at most one operation is applied per window.">
          <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3"><StatusBadge status={s.running ? "running" : "idle"} />
                <span className="text-sm"><b className="tabular-nums">{s.queries_in_window}</b> / {s.queries_per_window} queries · <b className="tabular-nums">{s.next_run_after_queries}</b> until the next scheduled check</span></div>
              <Progress value={(s.queries_in_window / s.queries_per_window) * 100} className="h-2" aria-label="queries in current window" />
              <p className="text-xs text-muted-foreground">Auto-adaptation {s.auto_adaptation ? "on" : "off"} · auto-apply {s.auto_apply ? "on (validated changes only)" : "off (evaluate only)"} · Recall/MRR regression ≤ {String(s.thresholds.max_recall_regression)}/{String(s.thresholds.max_mrr_regression)}, p95 ≤ +{String(s.thresholds.max_p95_latency_regression_pct)}%</p>
              {s.last_window_run && <p className="text-xs text-muted-foreground">Last window: {s.last_window_run.queries} queries → {s.last_window_run.proposals_detected} proposals, {s.last_window_run.applied} applied · {timeAgo(s.last_window_run.at)} · monitoring {s.last_window_run.monitoring?.status ?? "n/a"}</p>}
            </div>
            <div><h4 className="mb-2 text-sm font-semibold">Co-access in this window</h4>
              {s.window_pairs.length === 0 ? <p className="text-sm text-muted-foreground">No module pairs have been used together yet.</p> : (
                <ul className="space-y-1.5">{s.window_pairs.slice(0, 5).map((p) => (
                  <li key={p.modules.join()} className="flex items-center justify-between gap-2 rounded-xl border px-3 py-1.5 text-xs"><span className="min-w-0 truncate">{p.modules.map((m) => names[m] ?? m).join(" ↔ ")}</span><span className="shrink-0 tabular-nums"><b>{p.count}</b>×{p.eligible && <Badge variant="outline" className="ml-2 text-success">eligible</Badge>}</span></li>))}</ul>)}
            </div>
          </div>
        </Section>
      )}

      <section className="space-y-3" aria-label="Detected proposals">
        <h2 className="flex items-center gap-2 text-lg font-semibold"><Sparkles className="size-5 text-primary" /> Detected proposals</h2>
        <QueryBoundary query={cands} rows={2} isEmpty={(d) => d.proposals.length === 0}
          empty={<EmptyState icon={<ShieldCheck className="size-5" />} title="No structural weakness detected" description="The current architecture shows no overloaded module, redundant pair or repeated co-access pattern above the configured thresholds." />}>
          {(d) => <ul className="grid gap-4 xl:grid-cols-2">{d.proposals.map((p) => <ProposalCard key={p.proposal_id} p={p} names={names} pending={act.isPending} pendingKind={act.variables?.kind} onRun={runProposal} />)}</ul>}
        </QueryBoundary>
        {cands.data && cands.data.not_proposed.length > 0 && (
          <Accordion type="single" collapsible className="rounded-xl border px-4"><AccordionItem value="n" className="border-0"><AccordionTrigger className="text-sm">Considered but not proposed ({cands.data.not_proposed.length})</AccordionTrigger>
            <AccordionContent><ul className="space-y-1.5 text-xs">{cands.data.not_proposed.map((n, i) => <li key={i}><b>{opLabel(n.operation)}</b> · {n.module_ids.map((m) => names[m] ?? m).join(" ↔ ")}: <span className="text-muted-foreground">{(n.blockers ?? []).join("; ")}</span></li>)}</ul></AccordionContent></AccordionItem></Accordion>)}
      </section>

      <section className="space-y-3" aria-label="Adaptation history">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">History</h2>
          <Tabs value={tab} onValueChange={setTab} className="min-w-0 max-w-full"><TabsList className="max-w-full justify-start overflow-x-auto">
            {TABS.map(([v, l]) => <TabsTrigger key={v} value={v} className="gap-1.5">{l}{events.data && v !== "all" && events.data.status_counts[v] ? <span className="rounded-full bg-muted px-1.5 text-[10px] tabular-nums">{events.data.status_counts[v]}</span> : null}</TabsTrigger>)}
          </TabsList></Tabs></div>
        {events.isLoading ? <LoadingBlock /> : events.isError ? <ErrorState error={events.error} onRetry={() => events.refetch()} /> : events.data!.items.length === 0 ? (
          <EmptyState title={tab === "all" ? "No adaptation events yet" : `No ${tab.replace("_", " ")} operations`} description="Events appear here as the system detects, evaluates and applies structural changes." />
        ) : (
          <ul className="space-y-2">{events.data!.items.map((e) => {
            const Icon = ICON[e.operation] ?? Workflow
            return (
              <li key={e.event_id}><button type="button" onClick={() => setSelected(e)} className="surface flex w-full items-start gap-3 p-3 text-left transition hover:border-primary/50">
                <span className="tile-icon mt-0.5"><Icon className="size-[18px]" /></span>
                <span className="min-w-0 flex-1"><span className="flex flex-wrap items-center gap-2 text-sm font-medium">{opLabel(e.operation)} <StatusBadge status={e.status} />{e.rejection_stage === "pre_commit" && <Badge variant="outline" className="text-[10px]">before commit</Badge>}</span>
                  <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">{e.decision_reason ?? e.error ?? e.reason}</span>
                  <span className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted-foreground"><span>{timeAgo(e.started_at)}</span>{e.new_version && <span>→ {e.new_version}</span>}{e.affected_record_count ? <span>{e.affected_record_count} records</span> : null}<MonoId plain id={e.event_id} short={16} /></span></span>
              </button></li>)
          })}</ul>
        )}
      </section>

      {config.data?.admin_endpoints && (
        <Section title="Controlled demonstration" description="Create the conditions deterministically — the engine still decides and validates on its own. Scenarios only add data or replay a fixed query sequence.">
          <div className="grid gap-3 sm:grid-cols-3">
            {(scenarios.data?.scenarios ?? []).filter((x) => ["merge", "mixed", "shortcut"].includes(x.name)).map((x) => (
              <div key={x.name} className="space-y-2 rounded-xl border p-3"><div className="flex items-center justify-between gap-2"><b className="text-sm">{x.title}</b>{x.loaded && <Badge variant="outline" className="text-success">loaded</Badge>}</div>
                <p className="text-xs text-muted-foreground">{x.description}</p>
                <Button size="sm" variant="secondary" disabled={demo.isPending} onClick={() => scenario(x.name, x.title)}>{x.name === "shortcut" ? "Replay 50 queries" : "Load scenario"}</Button></div>))}
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={demo.isPending} onClick={() => demo.mutate({ action: "seed" }, { onSuccess: () => toast.success("Core corpus seeded"), onError: (e) => toast.error(errorMessage(e).message) })}>Seed core corpus</Button>
            <ResetButton />
          </div>
        </Section>
      )}
      <EventDetailDialog ev={selected} onOpenChange={(o) => !o && setSelected(null)} />
    </div>
  )
}

function ResetButton() {
  const demo = useDemo()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" variant="ghost" className="text-destructive" onClick={() => setOpen(true)}>Reset demo data…</Button>
      <ConfirmDialog open={open} onOpenChange={setOpen} destructive busy={demo.isPending} confirmLabel="Reset demo data" title="Reset all demo data?"
        description="Removes MORPHE's Qdrant collections and its MongoDB database (memories, modules, versions, events). Nothing else on this machine is touched."
        onConfirm={() => demo.mutate({ action: "reset" }, { onSuccess: () => { toast.success("Demo data reset"); setOpen(false) }, onError: (e) => { toast.error(errorMessage(e).message); setOpen(false) } })} />
    </>
  )
}
