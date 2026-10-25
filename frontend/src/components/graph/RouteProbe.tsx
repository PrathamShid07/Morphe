import { useState, type FormEvent } from "react"
import { Eye, EyeOff, Loader2, Route as RouteIcon, Trash2, Zap } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ErrorState } from "@/components/app/states"
import { useQuery_ } from "@/api/hooks"
import type { QueryMode } from "@/api/types"
import { DEMO_QUERIES } from "@/lib/examples"
import { routeFromResponse, setRoute, type RouteSnapshot } from "@/lib/route-store"
import { MODE_ORDER, STRATEGY_LABEL } from "@/lib/strategies"
import { ms } from "@/lib/format"

/** Run a real query and highlight the route the backend reports for it. The route is whatever the backend trace says; nothing is guessed here. */
export function RouteProbe({ route, routeOn, onRouteOn }: { route: RouteSnapshot | null; routeOn: boolean; onRouteOn: (v: boolean) => void }) {
  const [text, setText] = useState(route?.query ?? "")
  const [mode, setMode] = useState<QueryMode>(route?.mode ?? "progressive")
  const m = useQuery_()
  const run = (q = text) => {
    if (!q.trim() || m.isPending) return
    m.mutate({ query: q.trim(), top_k: 5, debug_trace: true, mode }, {
      onSuccess: (r) => { const s = routeFromResponse(q.trim(), mode, r); setRoute(s); onRouteOn(true) },
    })
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); run() }
  return (
    <div className="surface space-y-3 p-3" aria-label="Trace a query on the graph">
      <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2.5">
        <span className="flex items-center gap-2 text-sm font-medium"><RouteIcon className="size-4 text-primary" />Trace a query</span>
        <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} placeholder="Ask something to see which modules it routes through…" aria-label="Query to trace" className="h-10 min-w-0 flex-1 basis-60 rounded-xl" />
        <Select value={mode} onValueChange={(v) => setMode(v as QueryMode)}>
          <SelectTrigger className="h-10 w-52 rounded-xl" aria-label="Retrieval strategy for the trace"><SelectValue /></SelectTrigger>
          <SelectContent>{MODE_ORDER.map((k) => <SelectItem key={k} value={k}>{STRATEGY_LABEL[k]}</SelectItem>)}</SelectContent>
        </Select>
        <Select value="" onValueChange={(q) => { setText(q); run(q) }}>
          <SelectTrigger className="h-10 w-36 rounded-xl" aria-label="Example queries"><SelectValue placeholder="Examples" /></SelectTrigger>
          <SelectContent>{DEMO_QUERIES.map((q) => <SelectItem key={q} value={q}>{q}</SelectItem>)}</SelectContent>
        </Select>
        <Button type="submit" className="h-10 gap-2 rounded-xl" disabled={!text.trim() || m.isPending}>{m.isPending ? <Loader2 className="size-4 animate-spin" /> : <RouteIcon className="size-4" />}Trace</Button>
      </form>
      {m.isError && <ErrorState error={m.error} onRetry={() => run()} />}
      {route && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-muted/60 p-2.5 text-xs" data-testid="route-banner">
          <Badge>{STRATEGY_LABEL[route.mode]}</Badge>
          <span className="min-w-0 max-w-full flex-1 basis-48 truncate text-sm" title={route.query}>“{route.query}”</span>
          <span className="text-muted-foreground">{ms(route.latency_ms)} · {route.results.length} results · {route.trace.searched_modules?.length ? `${route.trace.searched_modules.length} modules searched` : "all modules searched"}</span>
          {route.trace.fallback_used && <Badge variant="outline" className="border-warning/50 bg-warning-soft text-warning"><Zap className="size-3" />global fallback</Badge>}
          <div className="ml-auto flex gap-1.5">
            <Button size="sm" variant="outline" className="h-9 gap-1.5" onClick={() => onRouteOn(!routeOn)} aria-pressed={routeOn}>{routeOn ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}{routeOn ? "Hide route" : "Show route"}</Button>
            <Button size="sm" variant="ghost" className="h-9 gap-1.5" onClick={() => { setRoute(null); onRouteOn(false) }} aria-label="Clear route"><Trash2 className="size-3.5" />Clear</Button>
          </div>
        </div>
      )}
    </div>
  )
}
