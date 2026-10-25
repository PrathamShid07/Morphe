import { Link } from "react-router-dom"
import { ArrowDownToLine, ArrowRightLeft, ArrowUpFromLine, Boxes, Clock, Gauge, Layers } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { StatusBadge } from "./StatusBadge"
import { ErrorState, LoadingBlock } from "./states"
import { KeyValue, MonoId } from "./ui-bits"
import { useModule } from "@/api/hooks"
import { NO_DATA, dateTime, num, opLabel, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { ModuleDetail } from "@/api/types"

type Neighbor = ModuleDetail["neighbors"][number]

function Rel({ n, onSelect }: { n: Neighbor; onSelect: (id: string) => void }) {
  return (
    <li>
      <button className="flex min-h-11 w-full items-center gap-2 rounded-xl border px-3 text-left text-sm hover:bg-muted" onClick={() => onSelect(n.module_id)}>
        {n.relation_type === "co_access" ? <ArrowRightLeft className="size-4 shrink-0 text-warning" /> : <Layers className="size-4 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate">{n.name ?? n.module_id}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{n.relation_type === "co_access" ? `shortcut · ${n.co_access_count}×` : n.direction}</span>
      </button>
    </li>
  )
}

/** Details of one module: metadata, capacity, structure, usage, memories and its adaptation history. Missing telemetry is shown as such, never as 0. */
export function ModulePanel({ moduleId, onSelect }: { moduleId: string; onSelect: (id: string) => void }) {
  const q = useModule(moduleId)
  if (q.isLoading) return <LoadingBlock rows={4} />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const m = q.data
  if (!m) return null
  const incoming = m.neighbors.filter((n) => n.direction === "parent")  // edges that point at this module
  const outgoing = m.neighbors.filter((n) => n.direction !== "parent")
  const util = m.utilization
  const hasActivity = !m.is_container
  return (
    <div className="space-y-5" data-testid="module-panel">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold">{m.name}</h3>
          <Badge variant={m.is_container ? "outline" : "secondary"}>{m.is_container ? "group / container" : "content module"}</Badge>
        </div>
        <MonoId id={m.module_id} short={30} />
        {m.keywords.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{m.keywords.map((k) => <Badge key={k} variant="outline" className="font-normal">{k}</Badge>)}</div>}
      </div>
      {!m.is_container && (
        <div className="rounded-xl border p-3">
          <div className="mb-1.5 flex items-center justify-between text-xs"><span className="flex items-center gap-1.5 font-medium"><Gauge className="size-3.5" />Capacity</span>
            <span className="tabular-nums text-muted-foreground">{typeof util === "number" && m.capacity_threshold ? `${num(m.record_count)} / ${num(m.capacity_threshold)} records · ${(util * 100).toFixed(util < 0.01 ? 2 : 1)}%` : NO_DATA}</span></div>
          {typeof util === "number" && <Progress value={Math.min(100, util * 100)} className={cn("h-1.5", util >= 1 && "[&>div]:bg-destructive")} aria-label="Share of split capacity used" />}
          <p className="mt-1.5 text-[11px] text-muted-foreground">Capacity is the split detector’s threshold: above it the module is treated as overloaded. It is not a hard storage limit.</p>
        </div>
      )}
      <KeyValue items={[
        { k: "Active records", v: <b>{num(m.record_count)}</b> },
        { k: "Tokens", v: m.is_container ? "—" : num(m.token_count) },
        { k: "Parent", v: m.parent ? <button className="text-accent-foreground hover:underline" onClick={() => onSelect(m.parent!.module_id)}>{m.parent.name}</button> : "none (top level)" },
        { k: "Child modules", v: m.children.length },
        { k: "Retrieval activity", v: hasActivity ? <>{num(m.total_access_count)} reads · last {m.last_accessed_at ? timeAgo(m.last_accessed_at) : "never"}</> : "—" },
        { k: "Queries touching", v: hasActivity ? num(m.queries_touching_module) : "—" },
        { k: "Last structural change", v: m.last_change_op ? <>{opLabel(m.last_change_op)} · {timeAgo(m.last_adapted_at)}</> : "none" },
        { k: "Created", v: dateTime(m.created_at) },
        { k: "Architecture version", v: m.architecture_version ? <MonoId id={m.architecture_version} short={16} /> : NO_DATA },
      ]} />
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><ArrowDownToLine className="size-4" /> Incoming ({incoming.length})</h4>
        {incoming.length === 0 ? <p className="text-sm text-muted-foreground">No edge points at this module (it is a root).</p> : <ul className="space-y-1.5">{incoming.map((n, i) => <Rel key={`${n.module_id}-${i}`} n={n} onSelect={onSelect} />)}</ul>}
      </section>
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><ArrowUpFromLine className="size-4" /> Outgoing &amp; shortcuts ({outgoing.length})</h4>
        {outgoing.length === 0 ? <p className="text-sm text-muted-foreground">No children and no shortcuts: this module is reachable only by profile similarity.</p> : <ul className="space-y-1.5">{outgoing.map((n, i) => <Rel key={`${n.module_id}-${n.relation_type}-${i}`} n={n} onSelect={onSelect} />)}</ul>}
      </section>
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Boxes className="size-4" /> Related memories ({m.record_count})</h4>
        {m.memories.length === 0 ? <p className="text-sm text-muted-foreground">This module holds no records directly.</p> : (
          <ul className="space-y-1.5">
            {m.memories.slice(0, 6).map((x) => (
              <li key={x.memory_id} className="rounded-xl border p-2.5 text-xs">
                <p className="line-clamp-2 text-sm">{x.text_preview}</p>
                <span className="text-muted-foreground">{x.source_name} · {x.access_count}× accessed</span>
              </li>
            ))}
            <li><Link to={`/memories?module=${m.module_id}`} className="text-xs font-medium text-accent-foreground hover:underline">Open all in Memory Explorer →</Link></li>
          </ul>
        )}
      </section>
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Clock className="size-4" /> Adaptation history</h4>
        {m.adaptation_history.length === 0 ? <p className="text-sm text-muted-foreground">No structural operation has involved this module.</p> : (
          <ul className="space-y-1.5">
            {m.adaptation_history.map((e) => (
              <li key={e.event_id} className="flex items-center justify-between gap-2 rounded-xl border p-2.5 text-xs">
                <span className="font-medium">{opLabel(e.operation)}</span><StatusBadge status={e.status} /><span className="text-muted-foreground">{timeAgo(e.started_at)}</span>
              </li>
            ))}
            <li><Link to="/adaptation" className="text-xs font-medium text-accent-foreground hover:underline">Open the Adaptation Center →</Link></li>
          </ul>
        )}
      </section>
    </div>
  )
}
