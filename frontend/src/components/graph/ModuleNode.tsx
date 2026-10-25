import { memo } from "react"
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react"
import { Boxes, Check, Layers, Sparkles } from "lucide-react"
import { cn } from "@/lib/utils"
import { num } from "@/lib/format"
import type { RouteRole } from "@/lib/route-store"

export interface ModuleNodeData extends Record<string, unknown> {
  label: string
  recordCount: number
  isContainer: boolean
  childCount: number
  utilization?: number | null
  lastChangeOp: string | null
  match?: boolean
  dim?: boolean
  neighbor?: boolean
  route?: { role: RouteRole | null; stage: number | null; results: number } | null
}
export type ModuleFlowNode = Node<ModuleNodeData, "moduleNode">

const ROLE: Record<RouteRole, { label: string; ring: string; chip: string }> = {
  seed: { label: "Seed", ring: "ring-2 ring-primary shadow-[0_0_0_4px_var(--glow)]", chip: "bg-primary text-primary-foreground" },
  expanded: { label: "Expanded", ring: "ring-2 ring-info/80", chip: "bg-info text-white dark:text-black" },
  searched: { label: "Searched", ring: "ring-1 ring-muted-foreground/60", chip: "bg-muted-foreground text-background" },
}

function Hidden() {
  const c = "!size-1 !min-h-0 !min-w-0 !border-0 !bg-transparent !opacity-0"
  return (
    <>
      <Handle id="t" type="source" position={Position.Top} className={c} />
      <Handle id="l" type="source" position={Position.Left} className={c} />
      <Handle id="r" type="source" position={Position.Right} className={c} />
      <Handle id="b" type="source" position={Position.Bottom} className={c} />
    </>
  )
}

function ModuleNodeImpl({ data, selected }: NodeProps<ModuleFlowNode>) {
  const role = data.route?.role ?? null
  const util = data.utilization
  return (
    <div
      data-testid={`module-node-${data.label}`}
      aria-label={`${data.label}, ${data.isContainer ? `group of ${data.childCount} modules` : `${data.recordCount} records`}`}
      title={data.label}
      className={cn(
        "relative w-[184px] rounded-2xl border px-3 py-2.5 text-left shadow-sm transition-[opacity,box-shadow,transform] duration-200",
        data.isContainer ? "border-primary/25 bg-gradient-to-br from-violet-soft via-card to-card" : "border-border bg-card hover:-translate-y-0.5 hover:shadow-md",
        selected && "border-primary ring-2 ring-primary/50",
        role && ROLE[role].ring,
        data.match && "ring-2 ring-warning",
        data.neighbor && !selected && "border-primary/40",
        data.dim && "opacity-30",
      )}
    >
      <Hidden />
      <div className="flex items-start gap-2">
        <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg", data.isContainer ? "bg-primary text-primary-foreground" : "bg-violet-soft text-accent-foreground")}>
          {data.isContainer ? <Layers className="size-3.5" /> : <Boxes className="size-3.5" />}
        </span>
        <span className="line-clamp-2 min-w-0 flex-1 text-[13px] font-semibold leading-tight">{data.label}</span>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>
          {data.isContainer
            ? <><b className="font-semibold text-foreground">group</b> · {data.childCount} module{data.childCount === 1 ? "" : "s"}</>
            : <><b className="font-semibold tabular-nums text-foreground">{num(data.recordCount)}</b> records</>}
        </span>
        {data.lastChangeOp && data.lastChangeOp !== "created" && (
          <span className="inline-flex items-center gap-0.5 rounded-full border bg-card px-1.5 py-px text-[9px] font-medium text-foreground"><Sparkles className="size-2.5 text-primary" />{data.lastChangeOp}</span>
        )}
      </div>
      {typeof util === "number" && (
        <div className="mt-1.5" title={`${(util * 100).toFixed(1)}% of the split capacity threshold`}>
          <div className="h-1 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", util >= 1 ? "bg-destructive" : util >= 0.75 ? "bg-warning" : "bg-primary/70")} style={{ width: `${Math.max(2, Math.min(100, util * 100))}%` }} /></div>
        </div>
      )}
      {(role || (data.route?.results ?? 0) > 0) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {role && <span className={cn("rounded-full px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide", ROLE[role].chip)}>{ROLE[role].label}{data.route?.stage ? ` · stage ${data.route.stage}` : ""}</span>}
          {(data.route?.results ?? 0) > 0 && <span className="inline-flex items-center gap-0.5 rounded-full bg-success-soft px-1.5 py-px text-[9px] font-semibold text-success"><Check className="size-2.5" />{data.route!.results} returned</span>}
        </div>
      )}
    </div>
  )
}
export const ModuleNode = memo(ModuleNodeImpl)

export interface ClusterData extends Record<string, unknown> { label: string | null; w: number; h: number }
export type ClusterFlowNode = Node<ClusterData, "clusterNode">
export function ClusterNode({ data }: NodeProps<ClusterFlowNode>) {
  return (
    <div aria-hidden style={{ width: data.w, height: data.h }} className="pointer-events-none rounded-[28px] border border-dashed border-border/80 bg-muted/30">
      {data.label && <span className="m-3 inline-block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{data.label}</span>}
    </div>
  )
}
