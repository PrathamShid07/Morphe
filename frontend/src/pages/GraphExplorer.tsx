import { useCallback, useEffect, useMemo, useState } from "react"
import {
  Background, BackgroundVariant, ConnectionMode, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Boxes, ChevronRight, Layers, Maximize2, RefreshCw } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock } from "@/components/app/states"
import { ModulePanel } from "@/components/app/ModulePanel"
import { MonoId, PageHeader } from "@/components/app/ui-bits"
import { useGraph, useModules } from "@/api/hooks"
import { useIngestDialog } from "@/components/app/IngestDialog"
import { useMediaQuery } from "@/hooks/useMediaQuery"
import { cn } from "@/lib/utils"
import { timeAgo } from "@/lib/format"
import type { GraphEdge, GraphNode } from "@/api/types"

type ModuleData = GraphNode["data"] & { selected?: boolean }

function ModuleNode({ data, selected }: NodeProps<Node<ModuleData>>) {
  return (
    <div
      data-testid={`module-node-${data.label}`}
      className={cn(
        "w-44 rounded-2xl border bg-card px-3 py-2.5 shadow-sm transition",
        data.isContainer ? "border-dashed bg-muted/60" : "border-border",
        selected && "border-primary ring-2 ring-primary/40",
      )}
    >
      <Handle id="t" type="source" position={Position.Top} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
      <Handle id="l" type="source" position={Position.Left} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
      <Handle id="r" type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
      <div className="flex items-center gap-2">
        <span className={cn("grid size-6 shrink-0 place-items-center rounded-lg", data.isContainer ? "bg-muted text-muted-foreground" : "bg-violet-soft text-accent-foreground")}>
          {data.isContainer ? <Layers className="size-3.5" /> : <Boxes className="size-3.5" />}
        </span>
        <span className="min-w-0 truncate text-sm font-semibold">{data.label}</span>
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{data.isContainer ? "group" : `${data.recordCount} records`}</span>
        {data.lastChangeOp && data.lastChangeOp !== "created" && <Badge variant="outline" className="h-4 px-1.5 py-0 text-[9px]">{data.lastChangeOp}</Badge>}
      </div>
      <Handle id="b" type="source" position={Position.Bottom} className="!size-1.5 !border-0 !bg-muted-foreground/50" />
    </div>
  )
}
const nodeTypes = { moduleNode: ModuleNode }

function Legend() {
  return (
    <div className="surface flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-xs" aria-label="Graph legend">
      <span className="flex items-center gap-2"><svg width="30" height="8" aria-hidden><line x1="0" y1="4" x2="30" y2="4" stroke="var(--muted-foreground)" strokeWidth="2" /></svg> hierarchy (parent → child)</span>
      <span className="flex items-center gap-2"><svg width="30" height="8" aria-hidden><line x1="0" y1="4" x2="30" y2="4" stroke="var(--chart-3)" strokeWidth="3" strokeDasharray="5 3" /></svg> co-access shortcut (label = times used together)</span>
      <span className="flex items-center gap-2"><span className="size-3 rounded border bg-violet-soft" /> content module</span>
      <span className="flex items-center gap-2"><span className="size-3 rounded border border-dashed bg-muted" /> group / container</span>
    </div>
  )
}

function Canvas({ selectedId, onSelect, showShortcuts }: { selectedId: string | null; onSelect: (id: string | null) => void; showShortcuts: boolean }) {
  const g = useGraph()
  const rf = useReactFlow()
  const touch = useMediaQuery("(max-width: 1023px)")
  const nodes: Node<ModuleData>[] = useMemo(
    () => (g.data?.nodes ?? []).map((n) => ({ id: n.id, type: "moduleNode", position: n.position, data: n.data, selected: n.id === selectedId })),
    [g.data, selectedId],
  )
  const edges: Edge[] = useMemo(
    () => {
      const at = new Map((g.data?.nodes ?? []).map((n) => [n.id, n.position]))
      // pick the connection sides that face each other so shortcuts do not loop back over their own nodes
      const sides = (a: string, b: string): [string, string] => {
        const pa = at.get(a), pb = at.get(b)
        if (!pa || !pb) return ["b", "t"]
        const dx = pb.x - pa.x, dy = pb.y - pa.y
        if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["r", "l"] : ["l", "r"]
        return dy >= 0 ? ["b", "t"] : ["t", "b"]
      }
      return (g.data?.edges ?? [])
        .filter((e: GraphEdge) => showShortcuts || e.data.relationType === "hierarchy")
        .map((e) => {
          const co = e.data.relationType === "co_access"
          const [sh, th] = co ? sides(e.source, e.target) : ["b", "t"]
          return {
            id: e.id, source: e.source, target: e.target, sourceHandle: sh, targetHandle: th,
            type: co ? "default" : "smoothstep",
            label: co ? `${e.data.coAccessCount}×` : undefined,
            labelStyle: { fontSize: 11, fill: "var(--foreground)" }, labelBgStyle: { fill: "var(--card)" }, labelBgPadding: [4, 2] as [number, number],
            style: co
              ? { stroke: "var(--chart-3)", strokeWidth: 1.5 + Math.min(4, e.data.weight * 8), strokeDasharray: "6 4" }
              : { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
            markerEnd: co ? undefined : { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
            data: e.data,
          }
        })
    },
    [g.data, showShortcuts],
  )
  const key = `${g.data?.nodes.length}-${g.data?.edges.length}-${g.data?.architecture_version}`
  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.15, duration: 250 }), 60)
    return () => clearTimeout(t)
  }, [key, rf])
  const onNodeClick = useCallback((_: unknown, n: Node) => onSelect(n.id), [onSelect])

  if (g.isLoading) return <LoadingBlock rows={4} />
  if (g.isError) return <ErrorState error={g.error} onRetry={() => g.refetch()} />
  return (
    <div className="relative h-[60dvh] min-h-[22rem] w-full overflow-hidden rounded-2xl border bg-card lg:h-[calc(100dvh-17rem)]" data-testid="graph-canvas">
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodeClick={onNodeClick} onPaneClick={() => onSelect(null)}
        fitView minZoom={0.15} maxZoom={1.8} nodesDraggable={!touch} nodesConnectable={false} elementsSelectable
        connectionMode={ConnectionMode.Loose} zoomOnPinch panOnDrag proOptions={{ hideAttribution: true }} aria-label="Module graph"
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
        <Controls showInteractive={false} position="bottom-left" className="!shadow-none" />
      </ReactFlow>
      <Button variant="secondary" size="sm" className="absolute right-3 top-3 gap-1.5 rounded-full" onClick={() => rf.fitView({ padding: 0.15, duration: 300 })} aria-label="Fit graph to view">
        <Maximize2 className="size-3.5" /> Fit
      </Button>
    </div>
  )
}

function ModuleTree({ onSelect, selectedId }: { onSelect: (id: string) => void; selectedId: string | null }) {
  const q = useModules()
  if (q.isLoading) return <LoadingBlock />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />
  const items = q.data?.items ?? []
  if (!items.length) return <EmptyState title="No modules yet" description="Ingest content to create memory modules." />
  const roots = items.filter((m) => !m.parent_id)
  const row = (m: (typeof items)[number], depth: number) => (
    <li key={m.module_id}>
      <button
        type="button" onClick={() => onSelect(m.module_id)} aria-pressed={selectedId === m.module_id}
        className={cn("flex min-h-12 w-full items-center gap-3 rounded-xl border px-3 py-2 text-left hover:bg-muted", selectedId === m.module_id && "border-primary bg-violet-soft")}
        style={{ marginLeft: depth * 16, width: `calc(100% - ${depth * 16}px)` }}
      >
        <span className={cn("grid size-7 shrink-0 place-items-center rounded-lg", m.is_container ? "bg-muted" : "bg-violet-soft text-accent-foreground")}>{m.is_container ? <Layers className="size-4" /> : <Boxes className="size-4" />}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{m.name}</span>
          <span className="block truncate text-xs text-muted-foreground">{m.is_container ? `${m.children.length} child modules` : `${m.record_count} records`}{m.shortcut_edges ? ` · ${m.shortcut_edges} shortcut${m.shortcut_edges > 1 ? "s" : ""}` : ""}{m.last_change_op && m.last_change_op !== "created" ? ` · ${m.last_change_op} ${timeAgo(m.last_adapted_at)}` : ""}</span>
        </span>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
      </button>
    </li>
  )
  const walk = (m: (typeof items)[number], depth: number): React.ReactNode[] => [row(m, depth), ...items.filter((c) => c.parent_id === m.module_id).flatMap((c) => walk(c, depth + 1))]
  return <ul className="space-y-1.5" aria-label="Modules">{roots.flatMap((m) => walk(m, 0))}</ul>
}

export default function GraphExplorer() {
  const [selected, setSelected] = useState<string | null>(null)
  const [shortcuts, setShortcuts] = useState(true)
  const g = useGraph()
  const desktop = useMediaQuery("(min-width: 1024px)")
  const { open } = useIngestDialog()
  const empty = g.data && g.data.nodes.length === 0

  const panel = selected ? <ModulePanel moduleId={selected} onSelect={setSelected} /> : null
  return (
    <div className="space-y-5">
      <PageHeader title="Graph Explorer" description="Nodes are memory modules, not individual chunks. Pan, zoom and select a module to inspect it."
        actions={
          <>
            <label className="flex items-center gap-2 text-sm"><Switch checked={shortcuts} onCheckedChange={setShortcuts} aria-label="Show shortcut edges" /> Shortcuts</label>
            <Button variant="outline" size="sm" onClick={() => g.refetch()} disabled={g.isFetching} aria-label="Refresh graph"><RefreshCw className={cn("size-4", g.isFetching && "animate-spin")} /> Refresh</Button>
          </>
        } />
      {g.data && <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground"><span><b className="text-foreground">{g.data.counts.nodes}</b> nodes</span><span><b className="text-foreground">{g.data.counts.edges}</b> edges</span><span className="flex items-center gap-1">architecture <MonoId id={g.data.architecture_version} short={14} /></span></div>}
      {empty ? (
        <EmptyState title="The graph is empty" description="Modules appear as soon as content is ingested." action={<Button onClick={open}>Ingest content</Button>} />
      ) : (
        <Tabs defaultValue="graph" className="gap-4">
          <TabsList><TabsTrigger value="graph">Graph</TabsTrigger><TabsTrigger value="list">Module list</TabsTrigger></TabsList>
          <TabsContent value="graph" className="space-y-3">
            <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
              <ReactFlowProvider><Canvas selectedId={selected} onSelect={setSelected} showShortcuts={shortcuts} /></ReactFlowProvider>
              {desktop && (
                <aside className="surface scroll-thin max-h-[calc(100dvh-17rem)] overflow-y-auto p-4" aria-label="Module details">
                  {panel ?? <EmptyState title="Select a module" description="Click a node to see its records, neighbours and adaptation history." icon={<Boxes className="size-5" />} className="border-0" />}
                </aside>
              )}
            </div>
            <Legend />
            {!desktop && <p className="text-xs text-muted-foreground">Touch: drag to pan, pinch to zoom, tap a module for details. Prefer a list? Use the “Module list” tab.</p>}
          </TabsContent>
          <TabsContent value="list">
            <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
              <ModuleTree onSelect={setSelected} selectedId={selected} />
              {desktop && <aside className="surface p-4" aria-label="Module details">{panel ?? <p className="text-sm text-muted-foreground">Select a module.</p>}</aside>}
            </div>
          </TabsContent>
        </Tabs>
      )}
      {!desktop && (
        <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
          <SheetContent side="bottom" className="max-h-[82dvh] gap-0 overflow-y-auto rounded-t-3xl p-0">
            <SheetHeader className="border-b p-4 text-left"><SheetTitle>Module details</SheetTitle><SheetDescription className="sr-only">Selected module</SheetDescription></SheetHeader>
            <div className="p-4">{panel}</div>
          </SheetContent>
        </Sheet>
      )}
    </div>
  )
}
