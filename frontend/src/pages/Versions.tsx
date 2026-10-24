import { useState } from "react"
import { toast } from "sonner"
import { GitBranch, History, Loader2, RotateCcw, Save } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { MetricsCompare } from "@/components/app/MetricsCompare"
import { EventDetailBody } from "@/components/app/EventDetail"
import { EmptyState, ErrorState, LoadingBlock, QueryBoundary, errorMessage } from "@/components/app/states"
import { StatusBadge } from "@/components/app/StatusBadge"
import { KeyValue, MonoId, PageHeader } from "@/components/app/ui-bits"
import { useCheckpoint, useRestore, useRestorePreview, useVersion, useVersions } from "@/api/hooks"
import { dateTime, num, opLabel, timeAgo } from "@/lib/format"
import type { Diff, VersionManifest } from "@/api/types"

function DiffSummary({ d }: { d: Diff }) {
  const rows = [
    ["Modules added", d.modules_added.map((m) => `${m.name} (${m.records})`).join(", ") || "none"],
    ["Modules removed", d.modules_removed.map((m) => `${m.name} (${m.records})`).join(", ") || "none"],
    ["Memories reassigned", num(d.memories_moved)],
    ["Edges added", d.edges_added.length ? d.edges_added.map((e) => `${e.relation_type}: ${e.source} ↔ ${e.target}`).join("; ") : "none"],
    ["Edges removed", d.edges_removed.length ? d.edges_removed.map((e) => `${e.relation_type}: ${e.source} ↔ ${e.target}`).join("; ") : "none"],
    ["Counts (modules / edges / memories)", `${d.counts.old.modules} / ${d.counts.old.edges} / ${d.counts.old.memories}  →  ${d.counts.new.modules} / ${d.counts.new.edges} / ${d.counts.new.memories}`],
  ] as const
  return <KeyValue items={rows.map(([k, v]) => ({ k, v }))} />
}

export function RestoreDialog({ versionId, onOpenChange }: { versionId: string | null; onOpenChange: (o: boolean) => void }) {
  const preview = useRestorePreview(versionId)
  const restore = useRestore()
  const [ack, setAck] = useState(false)
  const close = (o: boolean) => { if (!restore.isPending) { setAck(false); restore.reset(); onOpenChange(o) } }
  const p = preview.data
  const canRestore = !!p && p.restorable && !p.is_already_active
  return (
    <Dialog open={!!versionId} onOpenChange={close}>
      <DialogContent className="max-h-[92dvh] max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-2xl sm:max-w-xl">
        <DialogHeader><DialogTitle>Restore architecture {versionId}?</DialogTitle>
          <DialogDescription>A real cross-store rollback: vector payloads and routing profiles, MongoDB documents and the live graph are converged to this version, then verified.</DialogDescription></DialogHeader>
        {preview.isLoading && <LoadingBlock rows={3} />}
        {preview.isError && <ErrorState error={preview.error} onRetry={() => preview.refetch()} />}
        {p && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border p-3 text-sm"><div className="text-xs text-muted-foreground">Will be restored</div><MonoId id={p.target_version.version_id} short={20} /><div className="mt-1">{opLabel(p.target_version.operation_type)} · {timeAgo(p.target_version.created_at)}</div></div>
              <div className="rounded-xl border p-3 text-sm"><div className="text-xs text-muted-foreground">Will be replaced (checkpointed first)</div>{p.current_version ? <><MonoId id={p.current_version.version_id} short={20} /><div className="mt-1">{opLabel(p.current_version.operation_type)} · {timeAgo(p.current_version.created_at)}</div></> : "—"}</div>
            </div>
            {!p.restorable && <ErrorState error={new Error(`This version cannot be restored: ${p.problems.join("; ")}`)} />}
            {p.is_already_active && <p role="status" className="rounded-xl border border-info/40 bg-info-soft p-3 text-sm">This version is already active — nothing to restore.</p>}
            {p.diff && (<><h4 className="text-sm font-semibold">Expected changes</h4>{p.diff.identical ? <p className="text-sm text-muted-foreground">The restored architecture is identical to the current one.</p> : <DiffSummary d={p.diff} />}
              {p.reconciliation && <p className="text-xs text-muted-foreground">Compressed or deleted records are not revived ({p.reconciliation.inactive_memories_not_revived ?? 0} skipped); {(p.reconciliation.new_memories_kept_in_current_module ?? 0) + (p.reconciliation.new_memories_placed_by_similarity ?? 0)} newer memories are kept.</p>}</>)}
            {restore.isError && <ErrorState error={restore.error} />}
            {canRestore && <div className="flex items-start gap-2"><Checkbox id="ack" checked={ack} onCheckedChange={(v) => setAck(v === true)} /><Label htmlFor="ack" className="text-sm font-normal leading-snug">I understand this changes the active architecture. History is retained and this restore can itself be undone.</Label></div>}
          </div>
        )}
        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={() => close(false)} disabled={restore.isPending}>Cancel</Button>
          <Button disabled={!canRestore || !ack || restore.isPending}
            onClick={() => versionId && restore.mutate({ id: versionId }, {
              onSuccess: (r) => { toast.success(`Restored — new active version ${r.new_version.version_id}`); setAck(false); onOpenChange(false) },
              onError: (e) => toast.error(errorMessage(e).message),
            })}>
            {restore.isPending ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />} Restore this version
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function VersionDetail({ id, onClose, onRestore }: { id: string | null; onClose: () => void; onRestore: (id: string) => void }) {
  const q = useVersion(id)
  const v = q.data
  const ev = v?.evaluation
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-2xl">
        <SheetHeader className="border-b p-4 pr-12 text-left"><SheetTitle>Architecture version</SheetTitle><SheetDescription asChild><div><MonoId id={id} short={30} /></div></SheetDescription></SheetHeader>
        <div className="space-y-5 p-4">
          {q.isLoading && <LoadingBlock />}{q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {v && (<>
            <div className="flex flex-wrap items-center gap-2">{v.is_active && <Badge className="bg-success-soft text-success" variant="outline">active</Badge>}<Badge variant="outline">{opLabel(v.operation_type)}</Badge>{v.status === "rolled_back" && <StatusBadge status="rolled_back" />}{v.restorable ? <Badge variant="outline" className="text-success">restorable</Badge> : <Badge variant="outline" className="text-destructive">not restorable</Badge>}</div>
            <p className="text-sm leading-relaxed">{v.reason}</p>
            <KeyValue items={[
              { k: "Created", v: dateTime(v.created_at) }, { k: "Parent", v: <MonoId id={v.parent_version_id} short={16} /> },
              { k: "Graph", v: `${v.node_count} nodes · ${v.edge_count} edges (${v.co_access_edge_count} shortcuts)` }, { k: "Memories", v: num(v.memory_count) },
              { k: "Change", v: v.summary.diff ?? v.summary.note ?? "—" },
              { k: "Integrity", v: v.validation.integrity_check ?? "—" }, { k: "Post-commit verification", v: v.validation.post_commit_verification ?? "—" },
              ...(v.restored_from ? [{ k: "Restored from", v: <MonoId id={v.restored_from} short={16} /> }] : []), { k: "Checksum", v: <MonoId id={v.checksum} short={20} /> },
            ]} />
            {v.snapshot_problems.length > 0 && <ErrorState error={new Error(v.snapshot_problems.join("; "))} />}
            <section><h4 className="mb-2 text-sm font-semibold">Evaluation recorded with this version</h4>
              {ev?.baseline && ev.candidate ? (<><MetricsCompare before={ev.baseline} after={ev.candidate} beforeLabel="Before" afterLabel="This version" /><p className="mt-2 text-xs text-muted-foreground">Decision: <b>{ev.decision}</b>. {ev.decision_reason}</p></>)
                : ev && "before" in ev && (ev.before?.metrics || ev.after?.metrics) ? <MetricsCompare before={ev.before?.metrics} after={ev.after?.metrics} beforeLabel="Before restore" afterLabel="After restore" />
                : <p className="text-sm text-muted-foreground">No evaluation is attached to this version (baselines and checkpoints are not evaluated).</p>}</section>
            <section><h4 className="mb-2 text-sm font-semibold">Modules ({v.modules.length})</h4>
              <div className="overflow-x-auto rounded-xl border"><Table><TableHeader><TableRow><TableHead>Module</TableHead><TableHead>Parent</TableHead><TableHead className="text-right">Records</TableHead></TableRow></TableHeader>
                <TableBody>{v.modules.map((m) => <TableRow key={m.module_id}><TableCell>{m.name}</TableCell><TableCell className="mono-id">{m.parent_id ?? "—"}</TableCell><TableCell className="text-right tabular-nums">{m.records}</TableCell></TableRow>)}</TableBody></Table></div></section>
            {v.event && <section><h4 className="mb-2 text-sm font-semibold">Operation that created it</h4><div className="rounded-xl border p-3"><EventDetailBody ev={v.event} /></div></section>}
            {!v.is_active && v.restorable && <Button className="w-full" onClick={() => onRestore(v.version_id)}><RotateCcw className="size-4" /> Restore this version…</Button>}
          </>)}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export default function Versions() {
  const q = useVersions()
  const checkpoint = useCheckpoint()
  const [detail, setDetail] = useState<string | null>(null)
  const [restoring, setRestoring] = useState<string | null>(null)
  const row = (v: VersionManifest) => (
    <>
      <div className="flex flex-wrap items-center gap-2"><MonoId id={v.version_id} short={14} className="text-sm font-medium text-foreground" />{v.is_active && <Badge variant="outline" className="bg-success-soft text-success">active</Badge>}{v.status === "rolled_back" && <StatusBadge status="rolled_back" />}</div>
    </>
  )
  return (
    <div className="space-y-6">
      <PageHeader title="Architecture Versions" description="Every structural change is a durable, checksummed version. Restoring one is a real backend rollback across Qdrant, MongoDB and the live graph."
        actions={<Button variant="outline" disabled={checkpoint.isPending} onClick={() => checkpoint.mutate(undefined, { onSuccess: (m) => toast.success(`Checkpoint ${m.version_id} saved`), onError: (e) => toast.error(errorMessage(e).message) })}><Save className="size-4" /> Save checkpoint</Button>} />
      <QueryBoundary query={q} rows={5} isEmpty={(d) => d.items.length === 0} empty={<EmptyState icon={<GitBranch className="size-5" />} title="No versions yet" description="A baseline version is created automatically." />}>
        {(d) => (<>
          <div className="surface hidden overflow-hidden lg:block"><Table>
            <TableHeader><TableRow><TableHead>Version</TableHead><TableHead>Created</TableHead><TableHead>Operation</TableHead><TableHead className="w-[28%]">Reason / change</TableHead><TableHead className="text-right">Graph</TableHead><TableHead>Validation</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
            <TableBody>{d.items.map((v) => (
              <TableRow key={v.version_id} data-active={v.is_active}>
                <TableCell>{row(v)}<div className="mono-id mt-0.5">parent {v.parent_version_id ?? "—"}</div></TableCell>
                <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{timeAgo(v.created_at)}</TableCell>
                <TableCell>{opLabel(v.operation_type)}{v.restored_from && <div className="text-xs text-muted-foreground">of {v.restored_from}</div>}</TableCell>
                <TableCell className="whitespace-normal text-xs"><span className="line-clamp-2">{v.summary.diff ?? v.reason}</span></TableCell>
                <TableCell className="text-right text-xs tabular-nums">{v.node_count}n · {v.edge_count}e</TableCell>
                <TableCell className="text-xs">{v.validation.decision ? <span className="capitalize">{String(v.validation.decision)}</span> : "—"} · {v.validation.integrity_check ?? "—"}</TableCell>
                <TableCell className="text-right"><div className="flex justify-end gap-1.5"><Button size="sm" variant="ghost" onClick={() => setDetail(v.version_id)}>Details</Button>
                  <Button size="sm" variant="outline" disabled={v.is_active || !v.restorable} onClick={() => setRestoring(v.version_id)} title={!v.restorable ? "Snapshot failed its integrity check" : undefined}><RotateCcw className="size-3.5" /> Restore</Button></div></TableCell>
              </TableRow>))}</TableBody></Table></div>
          <ul className="space-y-3 lg:hidden" aria-label="Versions">{d.items.map((v) => (
            <li key={v.version_id} className="surface space-y-2 p-3"><div className="flex items-start justify-between gap-2">{row(v)}<span className="shrink-0 text-xs text-muted-foreground">{timeAgo(v.created_at)}</span></div>
              <div className="text-sm font-medium">{opLabel(v.operation_type)}</div><p className="line-clamp-2 text-xs text-muted-foreground">{v.summary.diff ?? v.reason}</p>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>{v.node_count} nodes · {v.edge_count} edges · {v.memory_count} memories</span><span>{v.restorable ? "restorable" : "not restorable"}</span></div>
              <div className="flex gap-2"><Button size="sm" variant="secondary" className="flex-1" onClick={() => setDetail(v.version_id)}><History className="size-3.5" /> Details</Button><Button size="sm" variant="outline" className="flex-1" disabled={v.is_active || !v.restorable} onClick={() => setRestoring(v.version_id)}><RotateCcw className="size-3.5" /> Restore</Button></div></li>))}</ul>
        </>)}
      </QueryBoundary>
      <VersionDetail id={detail} onClose={() => setDetail(null)} onRestore={(id) => { setDetail(null); setRestoring(id) }} />
      <RestoreDialog versionId={restoring} onOpenChange={(o) => !o && setRestoring(null)} />
    </div>
  )
}
