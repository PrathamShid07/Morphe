import { useState } from "react"
import { useSearchParams } from "react-router-dom"
import { ChevronLeft, ChevronRight, ScrollText, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { EmptyState, ErrorState, LoadingBlock } from "@/components/app/states"
import { ActionBadge, EventStatus, metaSummary, who } from "@/components/app/ActivityParts"
import { KeyValue, MonoId, PageHeader } from "@/components/app/ui-bits"
import { useActivity, useActivityActions } from "@/api/hooks"
import { useDebounced } from "@/hooks/useDebounced"
import { dateTime, timeAgo } from "@/lib/format"
import type { AuditEvent } from "@/api/types"

const PAGE = 25
const ALL = "__all__"

/** A `YYYY-MM-DD` from <input type=date> as the start / end of that local day, in ISO form. */
const dayStart = (d: string) => (d ? new Date(`${d}T00:00:00`).toISOString() : undefined)
const dayEnd = (d: string) => (d ? new Date(`${d}T23:59:59.999`).toISOString() : undefined)

function EventDetail({ event, onClose }: { event: AuditEvent | null; onClose: () => void }) {
  return (
    <Sheet open={!!event} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b p-4 pr-12 text-left">
          <SheetTitle>Event</SheetTitle>
          <SheetDescription asChild><div><MonoId id={event?.event_id} short={30} /></div></SheetDescription>
        </SheetHeader>
        {event && (
          <div className="space-y-4 p-4">
            <div className="flex flex-wrap items-center gap-2"><ActionBadge action={event.action} /><EventStatus status={event.status} /></div>
            <KeyValue items={[
              { k: "When", v: dateTime(event.timestamp) },
              { k: "Who", v: who(event) },
              { k: "Role", v: event.actor_role },
              ...(event.subject_user_id ? [{ k: "Account", v: <MonoId id={event.subject_user_id} short={26} /> }] : []),
              { k: "Target", v: event.target_type ? <>{event.target_type}{event.target_id ? <> · <MonoId id={event.target_id} short={26} /></> : null}</> : "—" },
              { k: "Request", v: <MonoId id={event.request_id} short={20} /> },
            ]} />
            <div className="space-y-1.5">
              <h3 className="text-sm font-semibold">Details</h3>
              <pre className="scroll-thin max-h-80 overflow-auto rounded-xl border bg-muted/40 p-3 text-xs" data-testid="event-metadata">{JSON.stringify(event.metadata, null, 2)}</pre>
              <p className="text-xs text-muted-foreground">The audit log never stores passwords, tokens or the text of saved memories.</p>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}

export default function ActivityLog() {
  const [params, setParams] = useSearchParams()
  const userId = params.get("user") ?? ""
  const [search, setSearch] = useState("")
  const [action, setAction] = useState(ALL)
  const [status, setStatus] = useState(ALL)
  const [role, setRole] = useState(ALL)
  const [since, setSince] = useState("")
  const [until, setUntil] = useState("")
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<AuditEvent | null>(null)
  const debounced = useDebounced(search)
  const actions = useActivityActions()
  const list = useActivity({
    limit: PAGE, offset, user_id: userId || undefined, search: debounced || undefined, action: action === ALL ? undefined : action,
    status: status === ALL ? undefined : status, role: role === ALL ? undefined : role, since: dayStart(since), until: dayEnd(until),
  })
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0) }
  const total = list.data?.total ?? 0
  const rows = list.data?.items ?? []
  const filtered = !!(userId || debounced || action !== ALL || status !== ALL || role !== ALL || since || until)
  const clear = () => { setSearch(""); setAction(ALL); setStatus(ALL); setRole(ALL); setSince(""); setUntil(""); setOffset(0); setParams({}) }

  return (
    <div className="space-y-5">
      <PageHeader title="Activity" description="A durable audit trail of sign-ins, questions, memory changes and administrator actions, with whether each one succeeded." />
      <div className="surface space-y-3 p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="relative sm:col-span-2">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input aria-label="Search activity" className="h-10 pl-9" placeholder="Search actions, ids, questions…" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
          </div>
          <Select value={action} onValueChange={reset(setAction)}>
            <SelectTrigger className="h-10" aria-label="Filter by action"><SelectValue placeholder="All actions" /></SelectTrigger>
            <SelectContent><SelectItem value={ALL}>All actions</SelectItem>{actions.data?.items.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={status} onValueChange={reset(setStatus)}>
            <SelectTrigger className="h-10" aria-label="Filter by outcome"><SelectValue placeholder="Any outcome" /></SelectTrigger>
            <SelectContent>{[[ALL, "Any outcome"], ["success", "Success"], ["failure", "Failed"], ["denied", "Denied"]].map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={role} onValueChange={reset(setRole)}>
            <SelectTrigger className="h-10" aria-label="Filter by actor"><SelectValue placeholder="Any actor" /></SelectTrigger>
            <SelectContent>{[[ALL, "Users and admins"], ["user", "Users only"], ["admin", "Administrators only"]].map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}</SelectContent>
          </Select>
          <div className="space-y-1"><Label htmlFor="act-since" className="text-xs text-muted-foreground">From</Label><Input id="act-since" type="date" className="h-10" value={since} onChange={(e) => reset(setSince)(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="act-until" className="text-xs text-muted-foreground">To</Label><Input id="act-until" type="date" className="h-10" value={until} onChange={(e) => reset(setUntil)(e.target.value)} /></div>
        </div>
        {userId && (
          <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="user-filter">Showing one account: <MonoId id={userId} short={26} />
            <Button size="sm" variant="outline" className="h-8 gap-1" onClick={() => setParams({})}><X className="size-3.5" />Show everyone</Button></p>
        )}
      </div>

      {list.isLoading ? <LoadingBlock rows={6} /> : list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : rows.length === 0 ? (
        <EmptyState title={filtered ? "No events match these filters" : "No activity recorded yet"} description={filtered ? "Try widening the date range or clearing a filter." : "Events appear here as people sign in, ask questions and save memories."}
          icon={<ScrollText className="size-5" />} action={filtered ? <Button variant="outline" onClick={clear}>Clear filters</Button> : undefined} />
      ) : (
        <>
          <div className="surface hidden overflow-hidden md:block">
            <Table aria-label="Activity log">
              <TableHeader><TableRow><TableHead className="w-40">When</TableHead><TableHead>Who</TableHead><TableHead>Action</TableHead><TableHead>Details</TableHead><TableHead>Outcome</TableHead></TableRow></TableHeader>
              <TableBody>
                {rows.map((e) => (
                  <TableRow key={e.event_id} tabIndex={0} role="button" className="cursor-pointer" onClick={() => setSelected(e)} onKeyDown={(ev) => ev.key === "Enter" && setSelected(e)}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={dateTime(e.timestamp)}>{timeAgo(e.timestamp)}</TableCell>
                    <TableCell className="max-w-48 truncate text-sm">{who(e)}</TableCell>
                    <TableCell><ActionBadge action={e.action} /></TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">{metaSummary(e) || "—"}</TableCell>
                    <TableCell><EventStatus status={e.status} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="space-y-3 md:hidden" aria-label="Activity events">
            {rows.map((e) => (
              <li key={e.event_id}>
                <button type="button" onClick={() => setSelected(e)} className="surface w-full space-y-1.5 p-3 text-left">
                  <div className="flex items-center justify-between gap-2"><ActionBadge action={e.action} /><EventStatus status={e.status} /></div>
                  <p className="truncate text-sm">{who(e)}</p>
                  {metaSummary(e) && <p className="line-clamp-2 break-words text-xs text-muted-foreground">{metaSummary(e)}</p>}
                  <p className="text-xs text-muted-foreground">{timeAgo(e.timestamp)}</p>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between gap-3 text-sm text-muted-foreground">
            <span>Showing {offset + 1}–{Math.min(offset + PAGE, total)} of {total.toLocaleString()}</span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}><ChevronLeft className="size-4" />Previous</Button>
              <Button variant="outline" size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next<ChevronRight className="size-4" /></Button>
            </div>
          </div>
        </>
      )}
      <EventDetail event={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
