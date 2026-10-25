import { useMemo, useState } from "react"
import { ReactFlowProvider } from "@xyflow/react"
import { Boxes } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState, LoadingBlock } from "@/components/app/states"
import { ModulePanel } from "@/components/app/ModulePanel"
import { EdgePanel } from "@/components/app/EdgePanel"
import { MonoId, PageHeader } from "@/components/app/ui-bits"
import { GraphWorkspace, DEFAULT_FILTERS, type GraphFilters } from "@/components/graph/GraphWorkspace"
import { GraphLegend } from "@/components/graph/GraphLegend"
import { ModuleList } from "@/components/graph/ModuleList"
import { RouteProbe } from "@/components/graph/RouteProbe"
import { useGraph, useModules } from "@/api/hooks"
import { useIngestDialog } from "@/components/app/IngestDialog"
import { useMediaQuery } from "@/hooks/useMediaQuery"
import { routeStates, useRoute } from "@/lib/route-store"

export default function GraphExplorer() {
  const [selected, setSelected] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [filters, setFilters] = useState<GraphFilters>(DEFAULT_FILTERS)
  const [view, setView] = useState("graph")
  const [routeOn, setRouteOn] = useState(true)
  const [focusRequest, setFocusRequest] = useState<{ id: string; n: number } | null>(null)
  const g = useGraph()
  const modules = useModules()
  const route = useRoute()
  const desktop = useMediaQuery("(min-width: 1024px)")
  const { open } = useIngestDialog()
  const empty = g.data && g.data.nodes.length === 0
  const routeActive = routeOn && routeStates(route).modules.size > 0
  const edge = useMemo(() => g.data?.edges.find((e) => e.id === selectedEdge) ?? null, [g.data, selectedEdge])
  const patch = (f: Partial<GraphFilters>) => setFilters((x) => ({ ...x, ...f }))
  const pick = (id: string | null) => { setSelected(id); if (id) setSelectedEdge(null) }
  const pickEdge = (id: string | null) => { setSelectedEdge(id); if (id) setSelected(null) }
  const showInGraph = (id: string) => { setView("graph"); setSelected(id); setSelectedEdge(null); setFocusRequest({ id, n: Date.now() }) }

  const details = edge && g.data ? <EdgePanel edge={edge} graph={g.data} onSelectModule={(id) => pick(id)} />
    : selected ? <ModulePanel moduleId={selected} onSelect={pick} /> : null
  const emptyDetails = <EmptyState title="Select a module" description="Click a node in the list to see its capacity, memories, relationships and adaptation history." icon={<Boxes className="size-5" />} className="border-0" />

  return (
    <Tabs value={view} onValueChange={setView} className="gap-0 space-y-4">
      <PageHeader title="Graph Explorer" description="Search, filter and select modules, or trace a real query to see the route it took."
        actions={g.data && !empty ? <TabsList><TabsTrigger value="graph">Graph</TabsTrigger><TabsTrigger value="list">Module list</TabsTrigger></TabsList> : undefined} />
      {g.data && (
        <div className="-mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span><b className="text-foreground">{g.data.counts.nodes}</b> nodes</span><span><b className="text-foreground">{g.data.counts.edges}</b> edges</span>
          <span className="flex items-center gap-1">architecture <MonoId id={g.data.architecture_version} short={14} /></span>
          {g.data.capacity_threshold ? <span>split capacity threshold <b className="text-foreground">{g.data.capacity_threshold.toLocaleString()}</b> records</span> : null}
        </div>
      )}
      {g.isLoading && <LoadingBlock rows={4} />}
      {g.isError && <ErrorState error={g.error} onRetry={() => g.refetch()} />}
      {empty && <EmptyState title="The graph is empty" description="Modules appear as soon as content is ingested. Load the demo corpus with `python backend/scripts/demo.py seed`, or ingest your own text." action={<Button onClick={open}>Ingest content</Button>} />}
      {g.data && !empty && (
        <>
          <RouteProbe route={route} routeOn={routeOn} onRouteOn={setRouteOn} />
          <TabsContent value="graph" className="space-y-3">
            <ReactFlowProvider>
              <GraphWorkspace graph={g.data} selectedId={selected} selectedEdgeId={selectedEdge} onSelect={pick} onSelectEdge={pickEdge} filters={filters} onFilters={patch}
                route={route} routeOn={routeOn} focusRequest={focusRequest} onReset={() => { setSelected(null); setSelectedEdge(null); setFilters(DEFAULT_FILTERS) }} onRefresh={() => g.refetch()} refreshing={g.isFetching}
                side={desktop && details ? <aside className="surface scroll-thin max-h-[calc(100dvh-8rem)] self-start overflow-y-auto p-4 lg:sticky lg:top-4" aria-label="Details">{details}</aside> : undefined} />
            </ReactFlowProvider>
            <GraphLegend route={routeActive} />
          </TabsContent>
          <TabsContent value="list">
            {modules.isLoading ? <LoadingBlock /> : modules.isError ? <ErrorState error={modules.error} onRetry={() => modules.refetch()} /> : (
              <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
                <ModuleList modules={modules.data?.items ?? []} graph={g.data} filters={filters} selectedId={selected} onSelect={pick} onShowInGraph={showInGraph} />
                {desktop && <aside className="surface scroll-thin max-h-[calc(100dvh-8rem)] self-start overflow-y-auto p-4 lg:sticky lg:top-4" aria-label="Details">{details ?? emptyDetails}</aside>}
              </div>
            )}
          </TabsContent>
        </>
      )}
      {!desktop && (
        <Sheet open={!!details} onOpenChange={(o) => { if (!o) { setSelected(null); setSelectedEdge(null) } }}>
          <SheetContent side="bottom" className="max-h-[82dvh] gap-0 overflow-y-auto rounded-t-3xl p-0">
            <SheetHeader className="border-b p-4 text-left"><SheetTitle>{edge ? "Edge details" : "Module details"}</SheetTitle><SheetDescription className="sr-only">Selected {edge ? "edge" : "module"}</SheetDescription></SheetHeader>
            <div className="p-4">{details}</div>
          </SheetContent>
        </Sheet>
      )}
    </Tabs>
  )
}
