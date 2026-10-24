import { Link } from "react-router-dom"
import { ArrowRightLeft, Boxes, Clock, GitBranch, Layers } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { StatusBadge } from "./StatusBadge"
import { ErrorState, LoadingBlock } from "./states"
import { KeyValue, MonoId } from "./ui-bits"
import { useModule } from "@/api/hooks"
import { dateTime, num, opLabel, timeAgo } from "@/lib/format"

/** Details of one module: metadata, structure, usage, memories and its adaptation history. */
export function ModulePanel({ moduleId, onSelect }: { moduleId: string; onSelect: (id: string) => void }) {
  const q = useModule(moduleId)
  if (q.isLoading) return <LoadingBlock rows={4} />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const m = q.data
  if (!m) return null
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
      <KeyValue items={[
        { k: "Active records", v: <b>{num(m.record_count)}</b> },
        { k: "Tokens", v: num(m.token_count) },
        { k: "Parent", v: m.parent ? <button className="text-accent-foreground hover:underline" onClick={() => onSelect(m.parent!.module_id)}>{m.parent.name}</button> : "none (top level)" },
        { k: "Recent access", v: <>{num(m.total_access_count)} reads · last {m.last_accessed_at ? timeAgo(m.last_accessed_at) : "never"}</> },
        { k: "Queries touching", v: num(m.queries_touching_module) },
        { k: "Last structural change", v: m.last_change_op ? <>{m.last_change_op} · {timeAgo(m.last_adapted_at)}</> : "none" },
        { k: "Created", v: dateTime(m.created_at) },
      ]} />
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><GitBranch className="size-4" /> Neighbouring modules ({m.neighbors.length})</h4>
        {m.neighbors.length === 0 ? <p className="text-sm text-muted-foreground">No edges: this module is only reachable by profile similarity.</p> : (
          <ul className="space-y-1.5">
            {m.neighbors.map((n, i) => (
              <li key={`${n.module_id}-${n.relation_type}-${i}`}>
                <button className="flex w-full min-h-11 items-center gap-2 rounded-xl border px-3 text-left text-sm hover:bg-muted" onClick={() => onSelect(n.module_id)}>
                  {n.relation_type === "co_access" ? <ArrowRightLeft className="size-4 shrink-0 text-warning" /> : <Layers className="size-4 shrink-0 text-muted-foreground" />}
                  <span className="min-w-0 flex-1 truncate">{n.name ?? n.module_id}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{n.relation_type === "co_access" ? `shortcut · ${n.co_access_count}×` : n.direction}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold"><Boxes className="size-4" /> Memories ({m.record_count})</h4>
        {m.memories.length === 0 ? <p className="text-sm text-muted-foreground">This module holds no records directly.</p> : (
          <ul className="space-y-1.5">
            {m.memories.slice(0, 8).map((x) => (
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
          </ul>
        )}
      </section>
    </div>
  )
}
