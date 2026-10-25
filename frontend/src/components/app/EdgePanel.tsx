import { ArrowRight, ArrowRightLeft, Layers } from "lucide-react"
import { Link } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { KeyValue, MonoId } from "./ui-bits"
import { dateTime, num } from "@/lib/format"
import type { GraphData } from "@/api/types"

/** One selected edge with its real attributes. A shortcut existing is NOT evidence that it helps retrieval, and the panel says so. */
export function EdgePanel({ edge, graph, onSelectModule }: { edge: GraphData["edges"][number]; graph: GraphData; onSelectModule: (id: string) => void }) {
  const name = (id: string) => graph.nodes.find((n) => n.id === id)?.data.label ?? id
  const co = edge.data.relationType === "co_access"
  const ev = Object.entries(edge.data.evidence ?? {})
  return (
    <div className="space-y-4" data-testid="edge-panel">
      <div className="flex flex-wrap items-center gap-2">
        {co ? <ArrowRightLeft className="size-5 text-warning" /> : <Layers className="size-5 text-muted-foreground" />}
        <h3 className="text-lg font-semibold">{co ? "Co-access shortcut" : "Hierarchy edge"}</h3>
        <Badge variant="outline">{co ? "shortcut" : "parent → child"}</Badge>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button className="rounded-lg border px-2.5 py-1.5 font-medium hover:bg-muted" onClick={() => onSelectModule(edge.source)}>{name(edge.source)}</button>
        <ArrowRight className="size-4 text-muted-foreground" />
        <button className="rounded-lg border px-2.5 py-1.5 font-medium hover:bg-muted" onClick={() => onSelectModule(edge.target)}>{name(edge.target)}</button>
      </div>
      <KeyValue items={[
        { k: "Edge id", v: <MonoId id={edge.id} short={28} /> },
        ...(co ? [{ k: "Observed together", v: <><b>{num(edge.data.coAccessCount)}</b> times</> }, { k: "Weight", v: edge.data.weight.toFixed(3) }] : []),
        { k: "Created", v: dateTime(edge.data.createdAt) },
        { k: "Created by operation", v: edge.data.operationId ? <MonoId id={edge.data.operationId} short={24} /> : "initial architecture" },
      ]} />
      {co && (
        <section>
          <h4 className="mb-2 text-sm font-semibold">Evidence recorded when it was created</h4>
          {ev.length === 0 ? <p className="text-sm text-muted-foreground">No evidence was recorded for this edge.</p> : (
            <dl className="space-y-1 rounded-xl border p-3 text-xs">{ev.map(([k, v]) => <div key={k} className="flex justify-between gap-3"><dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt><dd className="break-all text-right font-medium">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>)}</dl>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">A shortcut only means these modules were used together often enough to pass the detector. Whether it actually helps retrieval was decided by the validation of the adaptation that created it. <Link to="/adaptation" className="font-medium text-accent-foreground hover:underline">See the Adaptation Center</Link>.</p>
        </section>
      )}
    </div>
  )
}
