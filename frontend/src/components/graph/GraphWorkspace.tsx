import { useCallback, useEffect, useMemo, useRef } from "react"
import {
  Background, BackgroundVariant, ConnectionMode, MarkerType, MiniMap, ReactFlow, useReactFlow, type Edge,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Maximize2, Minus, Plus, RefreshCw, RotateCcw, Search, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { ClusterNode, ModuleNode, type ClusterFlowNode, type ModuleFlowNode } from "./ModuleNode"
import { layoutGraph } from "./layout"
import { useElementSize } from "@/hooks/useElementSize"
import { useTheme } from "@/lib/theme"
import { useMediaQuery } from "@/hooks/useMediaQuery"
import { routeStates, type RouteSnapshot } from "@/lib/route-store"
import { cn } from "@/lib/utils"
import type { GraphData } from "@/api/types"

export interface GraphFilters { search: string; minRecords: number; showHierarchy: boolean; showShortcuts: boolean; focusNeighbors: boolean }
export const DEFAULT_FILTERS: GraphFilters = { search: "", minRecords: 0, showHierarchy: true, showShortcuts: true, focusNeighbors: true }
type AppNode = ModuleFlowNode | ClusterFlowNode
const nodeTypes = { moduleNode: ModuleNode, clusterNode: ClusterNode }

export interface WorkspaceProps {
  graph: GraphData
  selectedId: string | null
  selectedEdgeId: string | null
  onSelect: (id: string | null) => void
  onSelectEdge: (id: string | null) => void
  filters: GraphFilters
  onFilters: (f: Partial<GraphFilters>) => void
  route: RouteSnapshot | null
  routeOn: boolean
  focusRequest: { id: string; n: number } | null
  onReset: () => void
  onRefresh: () => void
  refreshing: boolean
  /** Details panel (desktop). The canvas uses the full width until something is selected, and the layout never depends on whether it is open. */
  side?: React.ReactNode
}

const RECORD_STEPS = [0, 1, 5, 10, 25, 50, 100]

export function GraphWorkspace(p: WorkspaceProps) {
  const rf = useReactFlow()
  const { graph, filters: f } = p
  const [outer, outerSize] = useElementSize<HTMLDivElement>()
  const [box, size] = useElementSize<HTMLDivElement>()
  const touch = useMediaQuery("(max-width: 1023px)")
  const searchRef = useRef<HTMLInputElement>(null)
  const { theme } = useTheme()
  const mini = useMemo(() => {  // SVG fills cannot use CSS variables, so resolve the theme colours once per theme change
    const cs = typeof document !== "undefined" ? getComputedStyle(document.documentElement) : null
    const v = (name: string, fallback: string) => cs?.getPropertyValue(name).trim() || fallback
    return { group: v("--primary", "#7c5cff"), leaf: v("--accent-foreground", "#c9b8ff"), mask: `${v("--muted", "#1a1a21")}cc` }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

  // ---------------------------------------------------------------- layout: from the real hierarchy only, stable per aspect bucket
  const aspect = outerSize.width && size.height ? Math.round((outerSize.width / size.height) * 4) / 4 : 1.6  // full width / canvas height: stable when the panel opens
  const layout = useMemo(() => layoutGraph(graph.nodes, graph.edges, aspect), [graph.nodes, graph.edges, aspect])
  const childCount = useMemo(() => {
    const m = new Map<string, number>()
    for (const e of graph.edges) if (e.data.relationType === "hierarchy") m.set(e.source, (m.get(e.source) ?? 0) + 1)
    return m
  }, [graph.edges])
  const maxRecords = useMemo(() => Math.max(0, ...graph.nodes.filter((n) => !n.data.isContainer).map((n) => n.data.recordCount)), [graph.nodes])
  const steps = RECORD_STEPS.filter((s) => s === 0 || s <= maxRecords)

  const hidden = useMemo(() => new Set(graph.nodes.filter((n) => !n.data.isContainer && n.data.recordCount < f.minRecords).map((n) => n.id)), [graph.nodes, f.minRecords])
  const q = f.search.trim().toLowerCase()
  const matches = useMemo(() => new Set(q ? graph.nodes.filter((n) => !hidden.has(n.id) && n.data.label.toLowerCase().includes(q)).map((n) => n.id) : []), [graph.nodes, hidden, q])
  const visibleCount = graph.nodes.length - hidden.size

  const adjacency = useMemo(() => {
    const a = new Map<string, Set<string>>()
    for (const e of graph.edges) {
      if (hidden.has(e.source) || hidden.has(e.target)) continue
      if (!a.has(e.source)) a.set(e.source, new Set())
      if (!a.has(e.target)) a.set(e.target, new Set())
      a.get(e.source)!.add(e.target)
      a.get(e.target)!.add(e.source)
    }
    return a
  }, [graph.edges, hidden])
  const parentOf = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.data.parentId])), [graph.nodes])

  const rs = useMemo(() => (p.routeOn ? routeStates(p.route) : null), [p.route, p.routeOn])
  const routeActive = !!rs && rs.modules.size > 0

  /** Which modules stay at full strength: route > search > selected-neighbourhood; null = nothing is being focused. */
  const focusSet = useMemo<Set<string> | null>(() => {
    let base: Set<string> | null = null
    if (routeActive) base = new Set([...rs!.modules.entries()].filter(([, s]) => s.role || s.results > 0).map(([id]) => id))
    else if (q) base = new Set(matches)
    else if (p.selectedId && f.focusNeighbors) base = new Set([p.selectedId, ...(adjacency.get(p.selectedId) ?? [])])
    if (!base) return null
    for (const id of [...base]) { const par = parentOf.get(id); if (par) base.add(par) }  // keep a focused module's group in context
    return base
  }, [routeActive, rs, q, matches, p.selectedId, f.focusNeighbors, adjacency, parentOf])

  const nodes = useMemo<AppNode[]>(() => {
    const cl: ClusterFlowNode[] = layout.clusters.map((c) => ({
      id: c.id, type: "clusterNode", position: { x: c.x, y: c.y }, data: { label: c.label, w: c.w, h: c.h }, draggable: false, selectable: false, focusable: false, zIndex: -1,
    }))
    const mods: ModuleFlowNode[] = graph.nodes.flatMap((n) => {
      const pos = layout.nodes.get(n.id)
      if (!pos) return []
      const st = rs?.modules.get(n.id)
      return [{
        id: n.id, type: "moduleNode" as const, position: { x: pos.x, y: pos.y }, hidden: hidden.has(n.id), selected: n.id === p.selectedId,
        data: {
          label: n.data.label, recordCount: n.data.recordCount, isContainer: n.data.isContainer, childCount: childCount.get(n.id) ?? 0, utilization: n.data.utilization,
          lastChangeOp: n.data.lastChangeOp, match: matches.has(n.id), dim: focusSet ? !focusSet.has(n.id) : false,
          neighbor: !!p.selectedId && (adjacency.get(p.selectedId)?.has(n.id) ?? false), route: st ? { role: st.role, stage: st.stage, results: st.results } : null,
        },
      }]
    })
    return [...cl, ...mods]
  }, [layout, graph.nodes, hidden, p.selectedId, childCount, matches, focusSet, adjacency, rs])

  const edges = useMemo<Edge[]>(() => {
    const at = layout.nodes
    const linkedByHierarchy = new Set(graph.edges.filter((e) => e.data.relationType === "hierarchy").map((e) => [e.source, e.target].sort().join("|")))
    const sides = (a: string, b: string, forceSide: boolean): [string, string] => {
      const pa = at.get(a), pb = at.get(b)
      if (!pa || !pb) return ["r", "l"]
      const dx = pb.x - pa.x, dy = pb.y - pa.y
      if (forceSide || Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["r", "l"] : ["l", "r"]
      return dy >= 0 ? ["b", "t"] : ["t", "b"]
    }
    return graph.edges.flatMap((e) => {
      const co = e.data.relationType === "co_access"
      if (co ? !f.showShortcuts : !f.showHierarchy) return []
      const isHidden = hidden.has(e.source) || hidden.has(e.target)
      const dim = focusSet ? !(focusSet.has(e.source) && focusSet.has(e.target)) : false
      const via = rs && (rs.viaEdges.has(`${e.source}|${e.target}`) || rs.viaEdges.has(`${e.target}|${e.source}`))
      const sel = e.id === p.selectedEdgeId
      const touchesSel = !!p.selectedId && (e.source === p.selectedId || e.target === p.selectedId)
      const [sh, th] = co ? sides(e.source, e.target, linkedByHierarchy.has([e.source, e.target].sort().join("|"))) : ["b", "t"]
      const color = via || sel ? "var(--primary)" : co ? "var(--chart-3)" : touchesSel ? "var(--primary)" : "var(--muted-foreground)"
      return [{
        id: e.id, source: e.source, target: e.target, sourceHandle: sh, targetHandle: th, hidden: isHidden, type: co ? "default" : "smoothstep", selected: sel, interactionWidth: 18,
        label: co ? `${e.data.coAccessCount}×` : undefined,
        labelStyle: { fontSize: 11, fontWeight: 600, fill: "var(--foreground)" }, labelBgStyle: { fill: "var(--card)", fillOpacity: 0.95 }, labelBgPadding: [5, 3] as [number, number], labelBgBorderRadius: 8,
        style: {
          stroke: color, opacity: dim ? 0.12 : co ? 0.95 : touchesSel || via ? 0.9 : 0.5,
          strokeWidth: co ? 1.6 + Math.min(3.4, e.data.weight * 6) : via || sel ? 2.4 : touchesSel ? 2 : 1.5, strokeDasharray: co ? "7 5" : undefined,
        },
        markerEnd: co ? undefined : { type: MarkerType.ArrowClosed, color, width: 14, height: 14 }, data: e.data,
      }]
    })
  }, [graph.edges, layout.nodes, f.showHierarchy, f.showShortcuts, hidden, focusSet, rs, p.selectedEdgeId, p.selectedId])

  // ------------------------------------------------------------------------------------------------------------ view control
  const fit = useCallback((duration = 300) => rf.fitView({ padding: 0.1, duration, maxZoom: 1.15 }), [rf])
  const layoutKey = `${graph.nodes.length}-${graph.edges.length}-${graph.architecture_version}-${aspect}`
  useEffect(() => {
    const t = setTimeout(() => fit(250), 80)
    return () => clearTimeout(t)
  }, [layoutKey, fit])
  // camera follows selection: zoom to the selected module's neighbourhood (or the two ends of an edge); fit everything again when selection clears
  const handled = useRef("")
  useEffect(() => {
    const key = `${p.selectedId ?? ""}|${p.selectedEdgeId ?? ""}`
    if (key === handled.current) return
    handled.current = key
    const edge = p.selectedEdgeId ? graph.edges.find((e) => e.id === p.selectedEdgeId) : null
    const ids = p.selectedId ? [p.selectedId, ...(adjacency.get(p.selectedId) ?? []), parentOf.get(p.selectedId)] : edge ? [edge.source, edge.target, parentOf.get(edge.source), parentOf.get(edge.target)] : []
    const t = setTimeout(() => {
      const nodesToFit = [...new Set(ids.filter((x): x is string => !!x))].filter((id) => !hidden.has(id)).map((id) => ({ id }))
      if (nodesToFit.length) rf.fitView({ nodes: nodesToFit, duration: 400, maxZoom: 1.05, padding: 0.3 })
      else fit(300)
    }, 220)  // wait for the canvas to finish resizing when the details panel opens or closes
    return () => clearTimeout(t)
  }, [p.selectedId, p.selectedEdgeId, graph.edges, adjacency, parentOf, hidden, rf, fit])
  useEffect(() => {
    if (!p.focusRequest) return
    const t = setTimeout(() => rf.fitView({ nodes: [{ id: p.focusRequest!.id }], duration: 450, maxZoom: 1.25, padding: 1.4 }), 120)
    return () => clearTimeout(t)
  }, [p.focusRequest, rf])
  const firstMatch = useMemo(() => graph.nodes.find((n) => matches.has(n.id))?.id ?? null, [graph.nodes, matches])
  const noMatches = !!q && matches.size === 0
  const nothingVisible = visibleCount === 0

  return (
    <div className="space-y-3" ref={outer}>
      <div className="surface flex flex-wrap items-center gap-x-3 gap-y-2.5 p-3" role="toolbar" aria-label="Graph controls">
        <div className="relative min-w-0 flex-[1_1_11rem] sm:max-w-[16rem]">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef} value={f.search} onChange={(e) => p.onFilters({ search: e.target.value })} placeholder="Search modules by name…" aria-label="Search modules"
            className="h-10 rounded-xl pl-9 pr-9" onKeyDown={(e) => { if (e.key === "Enter" && firstMatch) { p.onSelect(firstMatch); rf.fitView({ nodes: [{ id: firstMatch }], duration: 400, maxZoom: 1.2, padding: 1.4 }) } if (e.key === "Escape") p.onFilters({ search: "" }) }}
          />
          {f.search && (
            <button type="button" aria-label="Clear search" className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground hover:bg-muted" onClick={() => { p.onFilters({ search: "" }); searchRef.current?.focus() }}><X className="size-3.5" /></button>
          )}
        </div>
        <ToggleGroup
          type="multiple" variant="outline" size="sm" aria-label="Edge types" value={[f.showHierarchy && "hierarchy", f.showShortcuts && "shortcuts"].filter(Boolean) as string[]}
          onValueChange={(v) => p.onFilters({ showHierarchy: v.includes("hierarchy"), showShortcuts: v.includes("shortcuts") })}
        >
          <ToggleGroupItem value="hierarchy" aria-label="Show hierarchy edges" className="h-10 gap-1.5 px-3"><span className="h-0.5 w-4 rounded bg-muted-foreground" />Hierarchy</ToggleGroupItem>
          <ToggleGroupItem value="shortcuts" aria-label="Show shortcut edges" className="h-10 gap-1.5 px-3"><span className="w-4 border-t-2 border-dashed" style={{ borderColor: "var(--chart-3)" }} />Shortcuts</ToggleGroupItem>
        </ToggleGroup>
        <Select value={String(f.minRecords)} onValueChange={(v) => p.onFilters({ minRecords: Number(v) })}>
          <SelectTrigger className="h-10 w-40 rounded-xl" aria-label="Minimum records per module"><SelectValue /></SelectTrigger>
          <SelectContent>{steps.map((s) => <SelectItem key={s} value={String(s)}>{s === 0 ? "All sizes" : `≥ ${s} records`}</SelectItem>)}</SelectContent>
        </Select>
        <label className="flex items-center gap-2 text-sm"><Switch checked={f.focusNeighbors} onCheckedChange={(v) => p.onFilters({ focusNeighbors: v })} aria-label="Highlight neighbours of the selected module" />Neighbours</label>
        <div className="ml-auto flex items-center gap-1.5">
          <Button variant="outline" size="icon" className="size-10 rounded-xl" onClick={() => rf.zoomOut({ duration: 200 })} aria-label="Zoom out"><Minus className="size-4" /></Button>
          <Button variant="outline" size="icon" className="size-10 rounded-xl" onClick={() => rf.zoomIn({ duration: 200 })} aria-label="Zoom in"><Plus className="size-4" /></Button>
          <Button variant="outline" size="sm" className="h-10 gap-1.5 rounded-xl px-3" onClick={() => fit()} aria-label="Fit graph to view"><Maximize2 className="size-4" /><span className="hidden 2xl:inline">Fit</span></Button>
          <Button variant="outline" size="sm" className="h-10 gap-1.5 rounded-xl" onClick={() => { p.onReset(); setTimeout(() => fit(), 60) }} aria-label="Reset view"><RotateCcw className="size-4" /><span className="hidden 2xl:inline">Reset</span></Button>
          <Button variant="outline" size="sm" className="h-10 gap-1.5 rounded-xl" onClick={p.onRefresh} disabled={p.refreshing} aria-label="Refresh graph"><RefreshCw className={cn("size-4", p.refreshing && "animate-spin")} /><span className="hidden 2xl:inline">Refresh</span></Button>
        </div>
      </div>
      <div className="px-1 text-xs text-muted-foreground" aria-live="polite">
        Showing <b className="text-foreground">{visibleCount}</b> of {graph.counts.nodes} modules
        {q && <> · <b className="text-foreground">{matches.size}</b> match “{f.search.trim()}”</>}
        {" · "}{graph.edges.filter((e) => !hidden.has(e.source) && !hidden.has(e.target) && (e.data.relationType === "co_access" ? f.showShortcuts : f.showHierarchy)).length} of {graph.counts.edges} edges
        {touch && " · drag to pan, pinch to zoom, tap a module"}
      </div>
      <div className={cn("grid gap-4", p.side && "lg:grid-cols-[minmax(0,1fr)_24rem]")}>
      <div ref={box} className="relative h-[64dvh] min-h-[24rem] w-full min-w-0 overflow-hidden rounded-2xl border bg-card lg:h-[calc(100dvh-24rem)] lg:min-h-[28rem]" data-testid="graph-canvas">
        <ReactFlow<AppNode, Edge>
          nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodeClick={(_, n) => { if (n.type === "moduleNode") { p.onSelect(n.id); p.onSelectEdge(null) } }}
          onEdgeClick={(_, e) => { p.onSelectEdge(e.id); p.onSelect(null) }} onPaneClick={() => { p.onSelect(null); p.onSelectEdge(null) }}
          fitView minZoom={0.1} maxZoom={1.6} nodesDraggable={!touch} nodesConnectable={false} elementsSelectable connectionMode={ConnectionMode.Loose}
          zoomOnPinch panOnDrag proOptions={{ hideAttribution: true }} aria-label="Module graph" defaultEdgeOptions={{ zIndex: 0 }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} />
          {graph.nodes.length > 24 && (
            <MiniMap pannable zoomable ariaLabel="Graph overview" className="!hidden !rounded-xl !border !bg-card md:!block" maskColor={mini.mask} nodeStrokeWidth={0}
              nodeColor={(n) => (n.type === "clusterNode" ? "transparent" : (n.data as { isContainer?: boolean }).isContainer ? mini.group : mini.leaf)} />
          )}
        </ReactFlow>
        {(noMatches || nothingVisible) && (
          <div className="absolute inset-0 grid place-items-center bg-card/70 p-6 backdrop-blur-[1px]">
            <div className="surface max-w-sm space-y-3 p-5 text-center" role="status">
              <p className="font-medium">{noMatches ? `No module matches “${f.search.trim()}”` : "No modules match the current filters"}</p>
              <p className="text-sm text-muted-foreground">{noMatches ? "Check the spelling, or search for a keyword from the module name." : "Lower the minimum record count to see small modules again."}</p>
              <Button size="sm" variant="outline" onClick={() => p.onFilters({ search: "", minRecords: 0 })}>Clear search and filters</Button>
            </div>
          </div>
        )}
      </div>
      {p.side}
      </div>
    </div>
  )
}
