import { useState } from "react"
import { Link } from "react-router-dom"
import { ChevronLeft, ChevronRight, Search, UserCheck, UserX, Users as UsersIcon } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock, errorMessage } from "@/components/app/states"
import { ActionBadge, EventStatus, metaSummary } from "@/components/app/ActivityParts"
import { ConfirmDialog, KeyValue, MonoId, PageHeader } from "@/components/app/ui-bits"
import { useAdminUser, useAdminUserQueries, useAdminUsers, useSetUserStatus } from "@/api/hooks"
import { useDebounced } from "@/hooks/useDebounced"
import { dateTime, timeAgo } from "@/lib/format"
import type { AdminUser } from "@/api/types"

const PAGE = 15

function StatusPill({ status }: { status: AdminUser["status"] }) {
  return <Badge variant={status === "active" ? "secondary" : "destructive"} className="capitalize">{status}</Badge>
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "bad" }) {
  return (
    <div className="rounded-xl border p-3">
      <p className={`text-xl font-semibold tabular-nums ${tone === "bad" && value > 0 ? "text-destructive" : ""}`}>{value.toLocaleString()}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  )
}

function UserDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useAdminUser(id)
  const questions = useAdminUserQueries(id)
  const setStatus = useSetUserStatus()
  const [confirm, setConfirm] = useState(false)
  const [reason, setReason] = useState("")
  const u = q.data
  const disabling = u?.status === "active"
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b p-4 pr-12 text-left">
          <SheetTitle>{u?.full_name ?? "User"}</SheetTitle>
          <SheetDescription>{u?.email ?? "Account details"}</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 p-4">
          {q.isLoading && <LoadingBlock />}
          {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {u && (
            <>
              <div className="flex flex-wrap items-center gap-2"><StatusPill status={u.status} /><span className="text-xs text-muted-foreground">Regular user account</span></div>
              {u.status === "disabled" && <p role="note" className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm">Disabled {u.disabled_at ? timeAgo(u.disabled_at) : ""}{u.disabled_reason ? ` — ${u.disabled_reason}` : ""}. Sign-in and every request are refused.</p>}
              <KeyValue items={[
                { k: "User ID", v: <MonoId id={u.user_id} short={28} /> },
                { k: "Created", v: dateTime(u.created_at) },
                { k: "Last sign-in", v: u.last_login_at ? <>{dateTime(u.last_login_at)} <span className="text-muted-foreground">({timeAgo(u.last_login_at)})</span></> : "never" },
                { k: "Last activity", v: u.last_activity_at ? timeAgo(u.last_activity_at) : "never" },
              ]} />
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Activity summary">
                <Stat label="Private memories" value={u.stats.private_memories} />
                <Stat label="Personal modules" value={u.stats.personal_modules} />
                <Stat label="Successful saves" value={u.stats.memory_saves} />
                <Stat label="Queries submitted" value={u.stats.queries_submitted} />
                <Stat label="Queries failed" value={u.stats.queries_failed} tone="bad" />
                <Stat label="Failed operations" value={u.stats.failed_operations} tone="bad" />
              </div>
              <section className="space-y-2">
                <div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Recent activity</h3>
                  <Link to={`/activity?user=${u.user_id}`} className="text-xs font-medium text-primary hover:underline">View all activity</Link></div>
                {u.recent_activity && u.recent_activity.length > 0 ? (
                  <ul className="space-y-1.5">
                    {u.recent_activity.map((e) => (
                      <li key={e.event_id} className="rounded-xl border p-2.5 text-xs">
                        <div className="flex flex-wrap items-center justify-between gap-2"><ActionBadge action={e.action} /><EventStatus status={e.status} /></div>
                        {metaSummary(e) && <p className="mt-1 break-words text-muted-foreground">{metaSummary(e)}</p>}
                        <p className="mt-0.5 text-muted-foreground">{timeAgo(e.timestamp)}</p>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">No activity recorded.</p>}
              </section>
              <section className="space-y-2">
                <h3 className="text-sm font-semibold">Questions asked</h3>
                <p className="text-xs text-muted-foreground">Questions are retained for operation and auditing, as disclosed to users. The text of retrieved memories is not shown.</p>
                {questions.data && questions.data.items.length > 0 ? (
                  <ul className="space-y-1.5">{questions.data.items.map((x) => (
                    <li key={x.query_id} className="rounded-xl border p-2.5 text-sm"><p className="break-words">{x.query}</p><p className="mt-0.5 text-xs text-muted-foreground">{x.mode} · {x.result_count} results · {timeAgo(x.created_at)}</p></li>
                  ))}</ul>
                ) : <p className="text-sm text-muted-foreground">No questions yet.</p>}
              </section>
              <Button variant={disabling ? "destructive" : "default"} className="w-full gap-2" onClick={() => { setReason(""); setConfirm(true) }}>
                {disabling ? <><UserX className="size-4" />Disable account</> : <><UserCheck className="size-4" />Reactivate account</>}
              </Button>
              <ConfirmDialog open={confirm} onOpenChange={setConfirm} destructive={disabling} busy={setStatus.isPending} confirmLabel={disabling ? "Disable account" : "Reactivate account"}
                title={disabling ? `Disable ${u.full_name}?` : `Reactivate ${u.full_name}?`}
                description={disabling ? "The user is signed out immediately and cannot sign in, ask questions or save memories until you reactivate the account. Their data is kept." : "The user will be able to sign in again."}
                onConfirm={() => setStatus.mutate({ id: u.user_id, status: disabling ? "disabled" : "active", reason: reason.trim() || undefined }, {
                  onSuccess: () => { toast.success(disabling ? "Account disabled" : "Account reactivated"); setConfirm(false) },
                  onError: (e) => { toast.error(errorMessage(e).message); setConfirm(false) },
                })}>
                {disabling && (
                  <div className="space-y-1.5"><Label htmlFor="disable-reason">Reason <span className="text-muted-foreground">(recorded in the audit log)</span></Label>
                    <Input id="disable-reason" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} /></div>
                )}
              </ConfirmDialog>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export default function Users() {
  const [search, setSearch] = useState("")
  const [status, setStatus] = useState("all")
  const [activeAfter, setActiveAfter] = useState("")
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const debounced = useDebounced(search)
  const list = useAdminUsers({ limit: PAGE, offset, search: debounced || undefined, status: status === "all" ? undefined : status, active_after: activeAfter ? new Date(`${activeAfter}T00:00:00`).toISOString() : undefined })
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0) }
  const total = list.data?.total ?? 0
  const rows = list.data?.items ?? []
  const filtered = !!debounced || status !== "all" || !!activeAfter

  return (
    <div className="space-y-5">
      <PageHeader title="Users" description="Registered accounts, how much they use MORPHE, and their private-memory footprint. Memory content is never shown here." />
      <div className="surface space-y-3 p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input aria-label="Search users" className="h-10 pl-9" placeholder="Search by name, email or user ID…" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
          </div>
          <div className="flex items-center gap-2"><Label htmlFor="active-after" className="whitespace-nowrap text-xs text-muted-foreground">Active since</Label>
            <Input id="active-after" type="date" className="h-10 w-40" value={activeAfter} onChange={(e) => reset(setActiveAfter)(e.target.value)} /></div>
        </div>
        <Tabs value={status} onValueChange={reset(setStatus)}>
          <TabsList>{["all", "active", "disabled"].map((s) => <TabsTrigger key={s} value={s} className="capitalize">{s}</TabsTrigger>)}</TabsList>
        </Tabs>
      </div>

      {list.isLoading ? <LoadingBlock rows={5} /> : list.isError ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : rows.length === 0 ? (
        <EmptyState title={filtered ? "No users match these filters" : "No registered users yet"} description={filtered ? "Try clearing a filter." : "Accounts appear here as people create them on the sign-in page."} icon={<UsersIcon className="size-5" />} />
      ) : (
        <>
          <div className="surface hidden overflow-hidden lg:block">
            <Table aria-label="Registered users">
              <TableHeader><TableRow>
                <TableHead>User</TableHead><TableHead>Status</TableHead><TableHead>Created</TableHead><TableHead>Last sign-in</TableHead><TableHead>Last activity</TableHead>
                <TableHead className="text-right">Queries</TableHead><TableHead className="text-right">Memories</TableHead><TableHead className="text-right">Modules</TableHead>
                <TableHead className="text-right">Saves</TableHead><TableHead className="text-right">Failed ops</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {rows.map((u) => (
                  <TableRow key={u.user_id} tabIndex={0} role="button" className="cursor-pointer" onClick={() => setSelected(u.user_id)} onKeyDown={(e) => e.key === "Enter" && setSelected(u.user_id)}>
                    <TableCell className="max-w-56"><p className="truncate text-sm font-medium">{u.full_name}</p><p className="truncate text-xs text-muted-foreground">{u.email}</p><MonoId plain id={u.user_id} short={22} /></TableCell>
                    <TableCell><StatusPill status={u.status} /></TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{timeAgo(u.created_at)}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{u.last_login_at ? timeAgo(u.last_login_at) : "never"}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{u.last_activity_at ? timeAgo(u.last_activity_at) : "never"}</TableCell>
                    <TableCell className="text-right tabular-nums">{u.stats.queries_submitted}{u.stats.queries_failed > 0 && <span className="text-destructive"> ({u.stats.queries_failed} failed)</span>}</TableCell>
                    <TableCell className="text-right tabular-nums">{u.stats.private_memories}</TableCell>
                    <TableCell className="text-right tabular-nums">{u.stats.personal_modules}</TableCell>
                    <TableCell className="text-right tabular-nums">{u.stats.memory_saves}</TableCell>
                    <TableCell className={`text-right tabular-nums ${u.stats.failed_operations ? "text-destructive" : ""}`}>{u.stats.failed_operations}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="space-y-3 lg:hidden" aria-label="Registered users">
            {rows.map((u) => (
              <li key={u.user_id}>
                <button type="button" onClick={() => setSelected(u.user_id)} className="surface w-full space-y-2 p-3 text-left">
                  <div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="truncate text-sm font-medium">{u.full_name}</p><p className="truncate text-xs text-muted-foreground">{u.email}</p></div><StatusPill status={u.status} /></div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    <span>{u.stats.queries_submitted} queries</span><span>{u.stats.private_memories} memories</span><span>{u.stats.personal_modules} modules</span>
                    {u.stats.failed_operations > 0 && <span className="text-destructive">{u.stats.failed_operations} failed</span>}
                    <span>active {u.last_activity_at ? timeAgo(u.last_activity_at) : "never"}</span>
                  </div>
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
      <UserDetail id={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
