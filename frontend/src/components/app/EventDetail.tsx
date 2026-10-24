import { useState } from "react"
import { toast } from "sonner"
import { Info, ShieldCheck, ShieldX, Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { StatusBadge } from "./StatusBadge"
import { DecisionChecks, MetricsCompare } from "./MetricsCompare"
import { ErrorState, errorMessage } from "./states"
import { KeyValue, MonoId } from "./ui-bits"
import { useApplyEvent } from "@/api/hooks"
import { dateTime, duration, opLabel } from "@/lib/format"
import type { AdaptationEvent, Decision, Metrics } from "@/api/types"

function isCandidateEval(e: AdaptationEvent["evaluation"]): e is { baseline: Metrics; candidate: Metrics; decision: Decision } {
  return !!e && "candidate" in e && "decision" in e
}

/** Distinguishes rejection BEFORE commit (nothing changed) from rollback AFTER commit (change was reverted). */
export function Outcome({ ev }: { ev: AdaptationEvent }) {
  const map: Record<string, { icon: typeof Info; text: string; tone: string }> = {
    rejected: { icon: ShieldX, tone: "border-warning/40 bg-warning-soft", text: ev.rejection_stage === "pre_commit" ? "Rejected before commit. The candidate failed the acceptance policy; the active architecture was not modified." : `Rejected during ${ev.rejection_stage ?? "planning"}. Nothing was changed.` },
    rolled_back: { icon: Undo2, tone: "border-primary/40 bg-violet-soft", text: `Rolled back after commit. The change was applied, then reverted${ev.rolled_back_to ? ` to ${ev.rolled_back_to}` : ""}${ev.error ? ` (${ev.error})` : ""}. History is retained.` },
    failed: { icon: ShieldX, tone: "border-destructive/40 bg-danger-soft", text: `Failed${ev.error ? `: ${ev.error}` : ""}.${ev.recovered === false ? " Automatic recovery did not complete; it will be retried at the next startup." : ev.recovered ? " Stores were restored to the previous architecture." : ""}` },
    completed: { icon: ShieldCheck, tone: "border-success/40 bg-success-soft", text: ev.operation === "restore_version" ? `Restored from ${ev.to_version}; new architecture ${ev.new_version}.` : `Committed${ev.new_version ? ` as ${ev.new_version}` : ""}. Integrity: ${ev.integrity_check ?? "passed"}.` },
    proposed: { icon: Info, tone: "border-primary/40 bg-violet-soft", text: "Evaluated and acceptable, but not applied. Applying re-plans and re-evaluates against the current architecture." },
    running: { icon: Info, tone: "border-info/40 bg-info-soft", text: `In progress${ev.stage ? ` (${ev.stage})` : ""}…` },
  }
  const o = map[ev.status]
  if (!o) return null
  return <div role="note" className={`flex items-start gap-2 rounded-xl border p-3 text-sm ${o.tone}`}><o.icon className="mt-0.5 size-4 shrink-0" /><span>{o.text}</span></div>
}

export function EventDetailBody({ ev }: { ev: AdaptationEvent }) {
  const evaluation = isCandidateEval(ev.evaluation) ? ev.evaluation : null
  const apply = useApplyEvent()
  const [result, setResult] = useState<AdaptationEvent | null>(null)
  const shown = result ?? ev
  const evalShown = isCandidateEval(shown.evaluation) ? shown.evaluation : evaluation
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2"><StatusBadge status={shown.status} /><span className="text-sm font-medium">{opLabel(shown.operation)}</span><span className="text-xs text-muted-foreground">trigger: {shown.trigger}</span></div>
      <Outcome ev={shown} />
      <p className="text-sm leading-relaxed">{shown.reason}</p>
      {evalShown && (
        <>
          <section><h4 className="mb-2 text-sm font-semibold">Before / after on the labeled dataset</h4>
            <MetricsCompare before={evalShown.baseline} after={evalShown.candidate} /></section>
          <section><h4 className="mb-2 text-sm font-semibold">Decision: {evalShown.decision.accepted ? "accept" : "reject"}</h4>
            <p className="mb-2 text-xs text-muted-foreground">{evalShown.decision.reason}</p><DecisionChecks checks={evalShown.decision.checks} />
            <p className="mt-2 text-[11px] text-muted-foreground">Thresholds: Recall/MRR regression ≤ {String(evalShown.decision.thresholds.max_recall_regression)} / {String(evalShown.decision.thresholds.max_mrr_regression)} absolute, p95 ≤ +{String(evalShown.decision.thresholds.max_p95_latency_regression_pct)}%, ≥ {String(evalShown.decision.thresholds.min_eval_queries)} labeled queries.</p></section>
        </>
      )}
      <KeyValue items={[
        { k: "Affected modules", v: (shown.affected_modules ?? []).join(", ") || "—" },
        { k: "Affected records", v: shown.affected_record_count ?? "—" },
        ...(shown.structural_summary ? [{ k: "Structural change", v: shown.structural_summary }] : []),
        { k: "Integrity check", v: shown.integrity_check ?? "—" },
        ...(shown.post_commit_verification ? [{ k: "Post-commit verification", v: shown.post_commit_verification.status }] : []),
        { k: "Version before", v: <MonoId id={shown.before_version} short={16} /> },
        { k: "Version created", v: <MonoId id={shown.new_version} short={16} /> },
        { k: "Started", v: dateTime(shown.started_at) },
        { k: "Duration", v: duration(shown.duration_ms) },
        { k: "Event", v: <MonoId id={shown.event_id} short={24} /> },
      ]} />
      <Accordion type="multiple" className="rounded-xl border px-3">
        {shown.evidence && Object.keys(shown.evidence).length > 0 && <AccordionItem value="evidence"><AccordionTrigger className="text-sm">Evidence</AccordionTrigger><AccordionContent><pre className="scroll-thin max-h-64 overflow-auto rounded-lg bg-muted/50 p-3 text-[11px] leading-relaxed">{JSON.stringify(shown.evidence, null, 2)}</pre></AccordionContent></AccordionItem>}
        {shown.timeline && <AccordionItem value="timeline"><AccordionTrigger className="text-sm">Timeline</AccordionTrigger><AccordionContent><ol className="space-y-1 text-xs">{shown.timeline.map((t, i) => <li key={i} className="flex gap-2"><span className="shrink-0 text-muted-foreground">{new Date(t.at).toLocaleTimeString()}</span><span>{t.note ?? t.status}</span></li>)}</ol></AccordionContent></AccordionItem>}
      </Accordion>
      {shown.error && shown.status !== "completed" && <ErrorState error={new Error(shown.error)} />}
      {shown.status === "proposed" && (
        <Button className="w-full" disabled={apply.isPending}
          onClick={() => apply.mutate(shown.event_id, {
            onSuccess: (r) => { setResult(r); toast[r.status === "completed" ? "success" : "info"](`${opLabel(r.operation)}: ${r.status.replace("_", " ")}`) },
            onError: (e) => toast.error(errorMessage(e).message),
          })}>
          {apply.isPending ? "Applying…" : "Apply (re-evaluate and commit if accepted)"}
        </Button>
      )}
    </div>
  )
}

export function EventDetailDialog({ ev, onOpenChange }: { ev: AdaptationEvent | null; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={!!ev} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92dvh] max-w-[calc(100vw-1.5rem)] gap-3 overflow-y-auto rounded-2xl sm:max-w-2xl">
        <DialogHeader><DialogTitle>Adaptation event</DialogTitle><DialogDescription>Reason, evidence, measured before/after and the acceptance decision.</DialogDescription></DialogHeader>
        {ev && <EventDetailBody ev={ev} />}
      </DialogContent>
    </Dialog>
  )
}
