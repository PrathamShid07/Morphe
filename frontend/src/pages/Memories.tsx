import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { ChevronLeft, ChevronRight, Pin, Plus, Search, Trash2 } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock } from "@/components/app/states"
import { StatusBadge } from "@/components/app/StatusBadge"
import { ConfirmDialog, KeyValue, MonoId, PageHeader } from "@/components/app/ui-bits"
import { useIngestDialog } from "@/components/app/IngestDialog"
import { useDeleteMemory, useMemories, useMemory, useModules, usePatchMemory, useSources } from "@/api/hooks"
import { useDebounced } from "@/hooks/useDebounced"
import { dateTime, num, timeAgo } from "@/lib/format"
import type { Memory } from "@/api/types"
import { errorMessage } from "@/components/app/states"

const PAGE = 15
const ALL = "__all__"

function MemoryDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useMemory(id)
  const patch = usePatchMemory()
  const del = useDeleteMemory()
  const [confirm, setConfirm] = useState(false)
  const m = q.data
  const protectedRec = !!m && (m.pinned || m.importance === "critical")
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b p-4 pr-12 text-left">
          <SheetTitle>Memory</SheetTitle>
          <SheetDescription asChild><div><MonoId id={id} short={34} /></div></SheetDescription>
        </SheetHeader>
        <div className="space-y-5 p-4">
          {q.isLoading && <LoadingBlock />}
          {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {m && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={m.status} />
                {m.kind && m.kind !== "chunk" && <Badge variant="outline">{m.kind}</Badge>}
                {m.pinned && <Badge variant="secondary"><Pin className="size-3" /> pinned</Badge>}
                <Badge variant="outline">{m.importance}</Badge>
              </div>
              <div className="max-h-72 overflow-y-auto rounded-xl border bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap break-words scroll-thin" data-testid="memory-text">
                {m.text ?? <span className="text-muted-foreground">Full text is no longer stored for this record ({m.status}).</span>}
              </div>
              <KeyValue items={[
                { k: "Module", v: m.module_name ?? m.module_id },
                { k: "Source", v: <>{m.source_name}{m.section ? ` · ${m.section}` : ""}</> },
                { k: "Tokens", v: num(m.token_count) },
                { k: "Accessed", v: <>{num(m.access_count)}× · last {m.last_accessed_at ? timeAgo(m.last_accessed_at) : "never"}</> },
                { k: "Created", v: dateTime(m.created_at) },
                ...(m.superseded_by ? [{ k: "Superseded by", v: <MonoId id={m.superseded_by} short={22} /> }] : []),
                ...(m.merged_source_ids?.length ? [{ k: "Merged sources", v: m.merged_source_ids.length }] : []),
              ]} />
              {m.summary_sources && m.summary_sources.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold">Summarised from {m.summary_sources.length} memories</h4>
                  <ul className="space-y-2">
                    {m.summary_sources.map((s) => (
                      <li key={s.memory_id} className="rounded-xl border p-2.5 text-xs">
                        <div className="flex items-center justify-between gap-2"><MonoId id={s.memory_id} short={20} /><StatusBadge status={s.status} /></div>
                        <p className="mt-1 line-clamp-3 text-muted-foreground">{s.text_preview}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {m.status === "active" && (
                <div className="space-y-3 rounded-xl border p-3">
                  <label className="flex items-center justify-between gap-3 text-sm">
                    <span>Pinned <span className="block text-xs text-muted-foreground">Protected from automatic deletion</span></span>
                    <Switch checked={m.pinned} disabled={patch.isPending} aria-label="Pinned"
                      onCheckedChange={(v) => patch.mutate({ id: m.memory_id, pinned: v }, { onError: (e) => toast.error(errorMessage(e).message) })} />
                  </label>
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span>Importance</span>
                    <Select value={m.importance} onValueChange={(v) => patch.mutate({ id: m.memory_id, importance: v }, { onError: (e) => toast.error(errorMessage(e).message) })}>
                      <SelectTrigger className="h-9 w-36" aria-label="Importance"><SelectValue /></SelectTrigger>
                      <SelectContent>{["low", "normal", "high", "critical"].map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                </div>
              )}
              {m.status === "active" && (
                <Button variant="destructive" className="w-full" onClick={() => setConfirm(true)}><Trash2 className="size-4" /> Delete memory</Button>
              )}
              <ConfirmDialog
                open={confirm} onOpenChange={setConfirm} destructive busy={del.isPending} confirmLabel={protectedRec ? "Delete protected memory" : "Delete"}
                title="Delete this memory?"
                description={<>This permanently removes the vector and text from active retrieval and reclaims <b>{num(m.token_count)}</b> tokens.{protectedRec && <> <b className="text-destructive">This memory is pinned or critical.</b> Deleting it requires an explicit override.</>}</>}
                onConfirm={() => del.mutate({ id: m.memory_id, force: protectedRec }, {
                  onSuccess: (r) => { toast.success(`Deleted — ${num(r.active_tokens_reclaimed)} tokens reclaimed`); setConfirm(false); onClose() },
                  onError: (e) => { toast.error(errorMessage(e).message); setConfirm(false) },
                })}
              />
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export default function Memories() {
  const { open } = useIngestDialog()
  const [search, setSearch] = useState("")
  const [params] = useSearchParams()
  const [moduleId, setModuleId] = useState(params.get("module") ?? ALL)
  const [sourceId, setSourceId] = useState(ALL)
  const [status, setStatus] = useState("active")
  const [pinned, setPinned] = useState(false)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const debounced = useDebounced(search)
  const modules = useModules()
  const sources = useSources()
  const list = useMemories({
    limit: PAGE, offset, status, search: debounced || undefined, module_id: moduleId === ALL ? undefined : moduleId,
    source_id: sourceId === ALL ? undefined : sourceId, pinned: pinned || undefined,
  })
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0) }
  const total = list.data?.total ?? 0
  const rows: Memory[] = list.data?.items ?? []
  const filtered = !!debounced || moduleId !== ALL || sourceId !== ALL || pinned

  return (
    <div className="space-y-5">
      <PageHeader title="Memory Explorer" description="Browse stored memories with their provenance, usage and lifecycle status."
        actions={<Button onClick={open}><Plus className="size-4" /> Ingest content</Button>} />
      <div className="surface space-y-3 p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_1fr_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input aria-label="Search memories" className="h-10 pl-9" placeholder="Search text…" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
          </div>
          <Select value={moduleId} onValueChange={reset(setModuleId)}>
            <SelectTrigger className="h-10" aria-label="Filter by module"><SelectValue placeholder="All modules" /></SelectTrigger>
            <SelectContent><SelectItem value={ALL}>All modules</SelectItem>{modules.data?.items.filter((m) => !m.is_container).map((m) => <SelectItem key={m.module_id} value={m.module_id}>{m.name} ({m.record_count})</SelectItem>)}</SelectContent>
          </Select>
          <Select value={sourceId} onValueChange={reset(setSourceId)}>
            <SelectTrigger className="h-10" aria-label="Filter by source"><SelectValue placeholder="All sources" /></SelectTrigger>
            <SelectContent><SelectItem value={ALL}>All sources</SelectItem>{sources.data?.items.map((s) => <SelectItem key={s.source_id} value={s.source_id}>{s.source_name}</SelectItem>)}</SelectContent>
          </Select>
          <label className="flex h-10 items-center gap-2 text-sm"><Switch checked={pinned} onCheckedChange={reset(setPinned)} aria-label="Pinned only" /> Pinned only</label>
        </div>
        <Tabs value={status} onValueChange={reset(setStatus)}>
          <TabsList className="max-w-full justify-start overflow-x-auto">
            {["active", "superseded", "deleted", "all"].map((s) => <TabsTrigger key={s} value={s} className="capitalize">{s}</TabsTrigger>)}
          </TabsList>
        </Tabs>
      </div>

      {list.isLoading ? <LoadingBlock rows={5} /> : list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : rows.length === 0 ? (
        <EmptyState title={filtered || status !== "active" ? "No memories match these filters" : "No memories yet"} description={filtered ? "Try clearing a filter." : "Ingest some text and its chunks will appear here."}
          action={!filtered && status === "active" ? <Button onClick={open}><Plus className="size-4" /> Ingest content</Button> : undefined} />
      ) : (
        <>
          <div className="surface hidden overflow-hidden md:block">
            <Table>
              <TableHeader><TableRow><TableHead className="w-[40%]">Memory</TableHead><TableHead>Module</TableHead><TableHead>Source</TableHead><TableHead className="text-right">Tokens</TableHead><TableHead className="text-right">Accessed</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
              <TableBody>
                {rows.map((m) => (
                  <TableRow key={m.memory_id} tabIndex={0} role="button" className="cursor-pointer" onClick={() => setSelected(m.memory_id)} onKeyDown={(e) => e.key === "Enter" && setSelected(m.memory_id)}>
                    <TableCell className="max-w-0">
                      <div className="flex items-center gap-1.5">{m.pinned && <Pin className="size-3 shrink-0 text-primary" />}<span className="line-clamp-2 whitespace-normal text-sm">{m.text_preview || "—"}</span></div>
                      <MonoId plain id={m.memory_id} short={24} />
                    </TableCell>
                    <TableCell className="text-sm">{m.module_name ?? m.module_id}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">{m.source_name}{m.section ? ` · ${m.section}` : ""}</TableCell>
                    <TableCell className="text-right tabular-nums">{num(m.token_count)}</TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground tabular-nums">{m.access_count}× · {m.last_accessed_at ? timeAgo(m.last_accessed_at) : "never"}</TableCell>
                    <TableCell><div className="flex flex-col items-start gap-1"><StatusBadge status={m.status} />{m.importance !== "normal" && <Badge variant="outline" className="text-[10px]">{m.importance}</Badge>}</div></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="space-y-3 md:hidden" aria-label="Memories">
            {rows.map((m) => (
              <li key={m.memory_id}>
                <button type="button" onClick={() => setSelected(m.memory_id)} className="surface w-full space-y-2 p-3 text-left">
                  <div className="flex items-center justify-between gap-2"><Badge variant="secondary">{m.module_name ?? m.module_id}</Badge><StatusBadge status={m.status} /></div>
                  <p className="line-clamp-3 text-sm">{m.text_preview || "—"}</p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {m.pinned && <span className="flex items-center gap-1 text-primary"><Pin className="size-3" />pinned</span>}
                    <span>{m.source_name}</span><span>{num(m.token_count)} tok</span><span>{m.access_count}× accessed</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
            <span aria-live="polite">{offset + 1}–{Math.min(offset + PAGE, total)} of {num(total)}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} aria-label="Previous page"><ChevronLeft className="size-4" /> Prev</Button>
              <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} aria-label="Next page">Next <ChevronRight className="size-4" /></Button>
            </div>
          </div>
        </>
      )}
      <MemoryDetail id={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
