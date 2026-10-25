import { useEffect, useRef, useState, type FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { ArrowRight, Loader2, Network, Send, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Progress } from "@/components/ui/progress"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { EmptyState, ErrorState } from "@/components/app/states"
import { MonoId, PageHeader, Section } from "@/components/app/ui-bits"
import { GlobalTrace, SoftBreakdown, StageProgression, TimingBar } from "@/components/app/TraceViews"
import { StrategyCompare } from "@/components/app/StrategyCompare"
import { useQuery_ } from "@/api/hooks"
import type { QueryMode, QueryResponse, Trace } from "@/api/types"
import { DEMO_QUERIES } from "@/lib/examples"
import { routeFromResponse, setRoute } from "@/lib/route-store"
import { MODE_ORDER, STRATEGY_HINT, STRATEGY_LABEL } from "@/lib/strategies"
import { ms } from "@/lib/format"
import { cn } from "@/lib/utils"

const SUGGESTIONS = DEMO_QUERIES

const ORIGIN: Record<string, { label: string; cls: string }> = {
  seed: { label: "seed (profile match)", cls: "border-primary/50 bg-violet-soft text-accent-foreground" },
  co_access: { label: "co-access shortcut", cls: "border-warning/50 bg-warning-soft text-warning" },
  sibling: { label: "hierarchy sibling", cls: "border-info/50 bg-info-soft text-info" },
  hierarchy: { label: "hierarchy child", cls: "border-info/50 bg-info-soft text-info" },
}

/** Visual explanation of how the graph shaped the search: query → seed modules → graph expansion → results. */
export function RoutingTrace({ trace, results }: { trace: Trace; results: QueryResponse["results"] }) {
  const resultModules = new Set(results.map((r) => r.module_id))
  const seeds = trace.candidate_modules.filter((c) => c.origin === "seed")
  const expanded = trace.candidate_modules.filter((c) => c.origin !== "seed")
  const chip = (c: Trace["candidate_modules"][number]) => (
    <li key={c.module_id} data-origin={c.origin} className={cn("rounded-xl border px-3 py-2 text-xs", ORIGIN[c.origin]?.cls)}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{c.name}</span>
        {resultModules.has(c.module_id) && <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">returned</Badge>}
      </div>
      <div className="mt-0.5 opacity-80">{ORIGIN[c.origin]?.label ?? c.origin}{c.via_name ? ` via ${c.via_name}` : ""} · {c.record_count} records</div>
    </li>
  )
  return (
    <div className="space-y-4" data-testid="routing-trace">
      <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-start">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">1 · Seed modules</h4>
          <ul className="space-y-2">{seeds.length ? seeds.map(chip) : <li className="text-xs text-muted-foreground">none</li>}</ul>
          <p className="mt-2 text-[11px] text-muted-foreground">Nearest module profile vectors to the query ({trace.seed_modules.map((s) => `${s.name} ${s.score.toFixed(2)}`).join(", ")}).</p>
        </div>
        <ArrowRight className="mx-auto hidden size-4 text-muted-foreground md:mt-8 md:block" />
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">2 · Graph expansion</h4>
          <ul className="space-y-2">{expanded.length ? expanded.map(chip) : <li className="text-xs text-muted-foreground">No neighbouring modules were added (no shortcut or sibling edges from the seeds).</li>}</ul>
        </div>
        <ArrowRight className="mx-auto hidden size-4 text-muted-foreground md:mt-8 md:block" />
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">3 · Filtered search</h4>
          <div className="rounded-xl border p-3 text-xs">
            <div><b className="text-base tabular-nums">{trace.candidate_count.toLocaleString()}</b> candidate records</div>
            <div className="mt-1 text-muted-foreground">in {trace.candidate_modules.length} module{trace.candidate_modules.length === 1 ? "" : "s"}</div>
            <div className="mt-2 flex items-center gap-1.5">
              {trace.fallback_used ? <Badge className="bg-warning-soft text-warning" variant="outline"><Zap className="size-3" /> global fallback used</Badge> : <Badge variant="outline" className="text-success">graph-guided only</Badge>}
            </div>
            {trace.fallback_reason && <p className="mt-2 text-muted-foreground">{trace.fallback_reason}</p>}
          </div>
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">Candidate count = {trace.candidate_count_definition}.</p>
    </div>
  )
}

export default function QueryPlayground() {
  const [params, setParams] = useSearchParams()
  const [text, setText] = useState(params.get("q") ?? "")
  const [topK, setTopK] = useState("5")
  const [debug, setDebug] = useState(true)
  const [mode, setMode] = useState<QueryMode>("graph")
  const [tab, setTab] = useState("single")
  const nav = useNavigate()
  const m = useQuery_()
  const ran = useRef(false)

  const run = (q = text) => {
    if (!q.trim() || m.isPending) return
    const body = { query: q.trim(), top_k: Number(topK), debug_trace: debug, mode }
    m.mutate(body, { onSuccess: (r) => setRoute(routeFromResponse(body.query, body.mode, r)) })  // the Graph Explorer can highlight this exact route
  }
  useEffect(() => {
    const q = params.get("q")
    if (q && !ran.current) {
      ran.current = true
      setText(q)
      m.mutate({ query: q, top_k: 5, debug_trace: true, mode: "graph" }, { onSuccess: (r) => setRoute(routeFromResponse(q, "graph", r)) })
      setParams({}, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const onSubmit = (e: FormEvent) => { e.preventDefault(); run() }
  const data = m.data

  return (
    <div className="space-y-6">
      <PageHeader title="Query Playground" description="Ask a question and see exactly how the module graph routed the search." />
      <form onSubmit={onSubmit} className="surface space-y-4 p-4" aria-label="Query form">
        <Label htmlFor="query" className="sr-only">Query</Label>
        <Textarea id="query" value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={2000} placeholder="Ask MORPHE anything about your ingested memories…" className="resize-none border-0 bg-transparent px-1 text-base shadow-none focus-visible:ring-0"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run() } }} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-t pt-3">
          <div className="flex items-center gap-2">
            <Label htmlFor="topk" className="text-xs text-muted-foreground">top_k</Label>
            <Select value={topK} onValueChange={setTopK}>
              <SelectTrigger id="topk" className="h-9 w-20 rounded-full" aria-label="top_k"><SelectValue /></SelectTrigger>
              <SelectContent>{["1", "3", "5", "8", "10", "20"].map((k) => <SelectItem key={k} value={k}>{k}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="mode" className="text-xs text-muted-foreground">retrieval</Label>
            <Select value={mode} onValueChange={(v) => setMode(v as QueryMode)}>
              <SelectTrigger id="mode" className="h-9 w-52 rounded-full" aria-label="retrieval mode"><SelectValue /></SelectTrigger>
              <SelectContent>
                {MODE_ORDER.map((k) => <SelectItem key={k} value={k}>{STRATEGY_LABEL[k]}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground"><Switch checked={debug} onCheckedChange={setDebug} aria-label="Debug trace" /> Debug trace</label>
          <Button type="submit" disabled={!text.trim() || m.isPending} className="ml-auto h-10 min-w-28 rounded-xl">
            {m.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />} Run query
          </Button>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="mode-hint"><b className="text-foreground">{STRATEGY_LABEL[mode]}:</b> {STRATEGY_HINT[mode]}</p>
      </form>
      <Tabs value={tab} onValueChange={setTab} className="gap-5">
        <TabsList className="max-w-full justify-start overflow-x-auto"><TabsTrigger value="single">Single run</TabsTrigger><TabsTrigger value="compare">Compare all four</TabsTrigger></TabsList>
        <TabsContent value="compare"><StrategyCompare text={text} topK={Number(topK)} /></TabsContent>
        <TabsContent value="single" className="space-y-6">

      {!data && !m.isPending && !m.isError && (
        <div className="grid gap-3 sm:grid-cols-2">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" onClick={() => { setText(s); run(s) }} className="surface flex items-center justify-between gap-3 p-4 text-left text-sm transition hover:border-primary/50">
              <span>{s}</span><ArrowRight className="size-4 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </div>
      )}
      {m.isError && <ErrorState error={m.error} onRetry={() => run()} />}
      {data && (
        <div className="space-y-6" aria-live="polite">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
            <span>Latency <b className="text-foreground">{ms(data.latency_ms)}</b></span>
            <span>Results <b className="text-foreground">{data.results.length}</b></span>
            <span className="flex items-center gap-1">Query <MonoId id={data.query_id} short={14} /></span>
            <span className="flex items-center gap-1">Architecture <MonoId id={data.architecture_version} short={14} /></span>
            <Badge variant="outline" data-testid="active-strategy">{STRATEGY_LABEL[data.mode as QueryMode] ?? data.mode}</Badge>
          </div>
          {data.trace && (
            <Section
              title={{ graph: "How the graph shaped this search", global: "Global dense search", progressive: "Progressive expansion, stage by stage", soft: "Soft routing: how module information changed the ranking" }[data.mode] ?? "Retrieval trace"}
              actions={<Button size="sm" variant="outline" className="h-9 gap-1.5" onClick={() => nav("/graph")}><Network className="size-3.5" />Show route on graph</Button>}>
              <div className="space-y-4">
                {data.mode === "graph" && <RoutingTrace trace={data.trace} results={data.results} />}
                {data.mode === "global" && <GlobalTrace trace={data.trace} />}
                {data.mode === "progressive" && <StageProgression trace={data.trace} />}
                {data.mode === "soft" && <SoftBreakdown trace={data.trace} />}
                <TimingBar trace={data.trace} totalMs={data.latency_ms} />
              </div>
            </Section>
          )}
          {data.results.length === 0 ? (
            <EmptyState title="No memories matched" description="Ingest content first, or rephrase the question." />
          ) : (
            <ol className="space-y-3" aria-label="Retrieved memories">
              {data.results.map((r, i) => (
                <li key={r.memory_id} className="surface p-4">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="grid size-6 place-items-center rounded-full bg-violet-soft font-semibold text-accent-foreground">{i + 1}</span>
                    <Badge variant="secondary">{r.module_name ?? r.module_id}</Badge>
                    <span className="text-muted-foreground">{r.source_name}{r.section ? ` · ${r.section}` : ""}</span>
                    <span className="ml-auto flex items-center gap-2 tabular-nums"><Progress value={Math.max(0, r.score) * 100} className="h-1.5 w-16" aria-hidden /> <b>{r.score.toFixed(3)}</b></span>
                  </div>
                  <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed">{r.text}</p>
                  <div className="mt-2"><MonoId id={r.memory_id} short={26} /></div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
        </TabsContent>
      </Tabs>
    </div>
  )
}