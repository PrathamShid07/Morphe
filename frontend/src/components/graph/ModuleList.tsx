import { useMemo, useState } from "react"
import { Boxes, ChevronRight, Layers, Network } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { EmptyState } from "@/components/app/states"
import { num, opLabel, timeAgo } from "@/lib/format"
import { useMediaQuery } from "@/hooks/useMediaQuery"
import { cn } from "@/lib/utils"
import type { GraphData, ModuleInfo } from "@/api/types"
import type { GraphFilters } from "./GraphWorkspace"

type Sort = "tree" | "name" | "records" | "reads"
/** Visitors receive only structural fields, so every operational field is optional here. */
type ListModule = Pick<ModuleInfo, "module_id" | "name" | "parent_id" | "children" | "record_count" | "is_container" | "keywords"> & Partial<ModuleInfo>
interface Row extends ListModule { neighbours: number; hier: number; co: number; parentName: string | null; depth: number }

export function ModuleList({ modules, graph, filters, selectedId, onSelect, onShowInGraph, readOnly = false }: {
  modules: ListModule[]; graph: GraphData; filters: GraphFilters; selectedId: string | null; onSelect: (id: string) => void; onShowInGraph: (id: string) => void; readOnly?: boolean
}) {
  const wide = useMediaQuery("(min-width: 768px)")
  const [sort, setSort] = useState<Sort>("tree")
  const [kind, setKind] = useState<"all" | "groups" | "modules">("all")
  const rows = useMemo<Row[]>(() => {
    const byId = new Map(modules.map((m) => [m.module_id, m]))
    const edge = new Map<string, { hier: number; co: number }>()
    for (const e of graph.edges) for (const id of [e.source, e.target]) {
      const x = edge.get(id) ?? { hier: 0, co: 0 }
      if (e.data.relationType === "co_access") x.co += 1; else x.hier += 1
      edge.set(id, x)
    }
    const depthOf = (m: ListModule): number => { let d = 0, cur: ListModule | undefined = m; const seen = new Set<string>(); while (cur?.parent_id && !seen.has(cur.module_id)) { seen.add(cur.module_id); cur = byId.get(cur.parent_id); d += 1 } return d }
    const make = (m: ListModule): Row => { const x = edge.get(m.module_id) ?? { hier: 0, co: 0 }; return { ...m, hier: x.hier, co: x.co, neighbours: x.hier + x.co, parentName: m.parent_id ? byId.get(m.parent_id)?.name ?? m.parent_id : null, depth: depthOf(m) } }
    const all = modules.map(make)
    // tree order: each group followed by its children
    const kids = (id: string) => all.filter((r) => r.parent_id === id).sort((a, b) => a.name.localeCompare(b.name))
    const walk = (r: Row, seen: Set<string>): Row[] => seen.has(r.module_id) ? [] : [r, ...kids(r.module_id).flatMap((c) => walk(c, new Set(seen).add(r.module_id)))]
    const tree = all.filter((r) => !r.parent_id || !byId.has(r.parent_id)).sort((a, b) => a.name.localeCompare(b.name)).flatMap((r) => walk(r, new Set()))
    const sorted = sort === "tree" ? tree : [...all].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) : sort === "records" ? b.record_count - a.record_count : (b.total_access_count ?? 0) - (a.total_access_count ?? 0))
    const q = filters.search.trim().toLowerCase()
    return sorted.filter((r) =>
      (kind === "all" || (kind === "groups" ? r.is_container : !r.is_container)) && (r.is_container || r.record_count >= filters.minRecords) &&
      (!q || r.name.toLowerCase().includes(q) || r.keywords.some((k) => k.toLowerCase().includes(q))))
  }, [modules, graph.edges, filters.search, filters.minRecords, sort, kind])

  const typeIcon = (r: Row) => (
    <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", r.is_container ? "bg-primary text-primary-foreground" : "bg-violet-soft text-accent-foreground")}>{r.is_container ? <Layers className="size-4" /> : <Boxes className="size-4" />}</span>
  )
  const rel = (r: Row) => (
    <span className="flex flex-wrap gap-1">
      {r.hier > 0 && <Badge variant="outline" className="font-normal">hierarchy ×{r.hier}</Badge>}
      {r.co > 0 && <Badge variant="outline" className="border-warning/50 font-normal text-warning">shortcut ×{r.co}</Badge>}
      {r.neighbours === 0 && <span className="text-xs text-muted-foreground">no edges</span>}
    </span>
  )
  const change = (r: Row) => r.last_change_op && r.last_change_op !== "created" ? <>{opLabel(r.last_change_op)} · {timeAgo(r.last_adapted_at)}</> : <span className="text-muted-foreground">none since creation</span>
  const reads = (r: Row) => r.is_container ? <span className="text-muted-foreground">—</span> : <>{num(r.total_access_count)} reads · {r.last_accessed_at ? timeAgo(r.last_accessed_at) : "never accessed"}</>

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <ToggleGroup type="single" variant="outline" size="sm" value={kind} onValueChange={(v) => v && setKind(v as typeof kind)} aria-label="Module type">
          <ToggleGroupItem value="all" className="h-10 px-3">All</ToggleGroupItem><ToggleGroupItem value="groups" className="h-10 px-3">Groups</ToggleGroupItem><ToggleGroupItem value="modules" className="h-10 px-3">Modules</ToggleGroupItem>
        </ToggleGroup>
        <Select value={sort} onValueChange={(v) => setSort(v as Sort)}>
          <SelectTrigger className="h-10 w-48 rounded-xl" aria-label="Sort modules"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="tree">Sort: hierarchy</SelectItem><SelectItem value="name">Sort: name</SelectItem><SelectItem value="records">Sort: records</SelectItem>{!readOnly && <SelectItem value="reads">Sort: reads</SelectItem>}</SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground" aria-live="polite">{rows.length} of {modules.length} modules (search and size filters are shared with the graph)</span>
      </div>
      {rows.length === 0 ? <EmptyState title="No modules match" description="Clear the search or lower the minimum record count." /> : (
        <>
          {wide ? (
          <div>
            <Table aria-label="Modules">
              <TableHeader><TableRow><TableHead>Module</TableHead><TableHead className="text-right">Records</TableHead><TableHead>Parent</TableHead><TableHead className="text-right">Neighbours</TableHead><TableHead>Relationships</TableHead>{!readOnly && <TableHead>Last structural change</TableHead>}{!readOnly && <TableHead>Recent access</TableHead>}<TableHead /></TableRow></TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.module_id} data-state={selectedId === r.module_id ? "selected" : undefined} className="cursor-pointer" onClick={() => onSelect(r.module_id)}>
                    <TableCell><div className="flex items-center gap-2.5" style={{ paddingLeft: filters.search || sort !== "tree" ? 0 : r.depth * 14 }}>{typeIcon(r)}<div className="min-w-0"><button type="button" className="block max-w-[16rem] truncate text-left font-medium hover:underline" onClick={(e) => { e.stopPropagation(); onSelect(r.module_id) }} aria-pressed={selectedId === r.module_id}>{r.name}</button><span className="text-xs text-muted-foreground">{r.is_container ? `group · ${r.children.length} modules` : "content module"}</span></div></div></TableCell>
                    <TableCell className="text-right tabular-nums">{r.is_container ? <span className="text-muted-foreground">—</span> : num(r.record_count)}</TableCell>
                    <TableCell className="max-w-[10rem] truncate">{r.parentName ?? <span className="text-muted-foreground">top level</span>}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.neighbours}</TableCell>
                    <TableCell>{rel(r)}</TableCell>
                    {!readOnly && <TableCell className="text-xs">{change(r)}</TableCell>}
                    {!readOnly && <TableCell className="text-xs">{reads(r)}</TableCell>}
                    <TableCell><Button variant="ghost" size="icon" className="size-9" aria-label={`Show ${r.name} in graph`} onClick={(e) => { e.stopPropagation(); onShowInGraph(r.module_id) }}><Network className="size-4" /></Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          ) : (
          <ul className="space-y-2" aria-label="Modules">
            {rows.map((r) => (
              <li key={r.module_id}>
                <div className={cn("surface p-3", selectedId === r.module_id && "border-primary bg-violet-soft")}>
                  <button type="button" onClick={() => onSelect(r.module_id)} aria-pressed={selectedId === r.module_id} className="flex min-h-12 w-full items-center gap-3 text-left">
                    {typeIcon(r)}
                    <span className="min-w-0 flex-1"><span className="block truncate font-medium">{r.name}</span><span className="block text-xs text-muted-foreground">{r.is_container ? `group · ${r.children.length} modules` : `${num(r.record_count)} records`}{r.parentName ? ` · in ${r.parentName}` : ""}</span></span>
                    <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                  </button>
                  <div className="mt-2 space-y-1.5 border-t pt-2 text-xs"><div>{rel(r)}</div>{!readOnly && <div className="text-muted-foreground">Change: {change(r)}</div>}{!readOnly && <div className="text-muted-foreground">Access: {reads(r)}</div>}</div>
                  <Button variant="outline" size="sm" className="mt-2 h-10 w-full gap-2" onClick={() => onShowInGraph(r.module_id)}><Network className="size-4" />Show in graph</Button>
                </div>
              </li>
            ))}
          </ul>
          )}
        </>
      )}
    </div>
  )
}
