import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Check, Loader2, Network, Scale, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ErrorState } from "./states"
import { useCompareStrategies, useEvalDataset } from "@/api/hooks"
import type { CompareStrategy, QueryMode, StrategyComparison, Trace } from "@/api/types"
import { setRoute, type RouteSnapshot } from "@/lib/route-store"
import { MODE_ORDER, STRATEGY_LABEL } from "@/lib/strategies"
import { cn } from "@/lib/utils"
import { ms, num, ratioPct } from "@/lib/format"

/** Adapter: a comparison entry already carries the backend's seeds / searched modules / results, which is all the graph needs. */
function routeOf(c: StrategyComparison, e: CompareStrategy): RouteSnapshot {
  const trace: Trace = {
    mode: e.strategy, strategy: e.strategy, seed_modules: e.seed_modules, candidate_modules: [], expanded_modules: [], candidate_count: e.candidate_count,
    guided_candidate_count: e.eligible_candidates, candidate_count_definition: "", fallback_used: e.fallback_used, fallback_reason: e.fallback_reason, guided_result_ids: [],
    latency_ms: e.latency_ms, architecture_version: c.architecture_version, searched_modules: e.searched_modules.map((m) => ({ ...m, records: 0 })), result_modules: e.result_modules,
  }
  return { query: c.query, mode: e.strategy as QueryMode, query_id: c.query_id ?? "compare", architecture_version: c.architecture_version, latency_ms: e.latency_ms, trace,
    results: e.results.map((r) => ({ memory_id: r.memory_id, module_id: r.module_id, module_name: r.module_name, score: r.score })), at: Date.now() }
}

export function StrategyCompare({ text, topK }: { text: string; topK: number }) {
  const ds = useEvalDataset()
  const m = useCompareStrategies()
  const nav = useNavigate()
  const [exampleId, setExampleId] = useState<string>("")
  const examples = ds.data?.sample ?? []
  const run = () => {
    if (exampleId) m.mutate({ query_id: exampleId, top_k: topK })
    else if (text.trim()) m.mutate({ query: text.trim(), top_k: topK })
  }
  const c = m.data
  return (
    <div className="space-y-4" data-testid="strategy-compare">
      <div className="flex flex-wrap items-center gap-3">
        <Select value={exampleId || "__text"} onValueChange={(v) => setExampleId(v === "__text" ? "" : v)}>
          <SelectTrigger className="h-10 min-w-0 max-w-full flex-1 basis-72 rounded-xl" aria-label="Query to compare"><SelectValue /></SelectTrigger>
          <SelectContent className="max-w-[90vw]">
            <SelectItem value="__text">The text typed above (no labels)</SelectItem>
            {examples.slice(0, 40).map((q) => <SelectItem key={q.query_id} value={q.query_id}><span className="line-clamp-1">{q.query}</span></SelectItem>)}
          </SelectContent>
        </Select>
        <Button className="h-10 gap-2 rounded-xl" onClick={run} disabled={m.isPending || (!exampleId && !text.trim())}>
          {m.isPending ? <Loader2 className="size-4 animate-spin" /> : <Scale className="size-4" />}Compare four strategies
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">Labeled examples come from the evaluation set, so quality metrics and “relevant memories excluded by routing” can be shown. Free text has no labels, so only retrieval behaviour is compared.</p>
      {m.isError && <ErrorState error={m.error} onRetry={run} />}
      {c && <Result c={c} onGraph={(e) => { setRoute(routeOf(c, e)); nav("/graph") }} />}
    </div>
  )
}

const KEYS = MODE_ORDER

function Result({ c, onGraph }: { c: StrategyComparison; onGraph: (e: CompareStrategy) => void }) {
  const rows = KEYS.map((k) => c.strategies[k]).filter((x): x is CompareStrategy => !!x)
  const best = (f: (e: CompareStrategy) => number | undefined) => { const v = rows.map((r) => f(r)).filter((x): x is number => x !== undefined); return v.length ? Math.max(...v) : undefined }
  const bestN = best((e) => e.metrics?.ndcg_at_10)
  return (
    <div className="space-y-4" aria-live="polite">
      <p className="text-sm">“{c.query}” · top {c.top_k} · {num(c.active_records)} active memories {c.labeled ? <Badge variant="secondary" className="ml-1">{c.relevant_memories.length} labeled relevant</Badge> : <Badge variant="outline" className="ml-1">unlabeled</Badge>}</p>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[46rem] text-sm" aria-label="Strategy comparison">
          <thead><tr className="border-b text-left text-xs text-muted-foreground"><th className="p-2.5 font-medium">Strategy</th><th className="p-2.5 text-right font-medium">Relevant found</th><th className="p-2.5 text-right font-medium">nDCG@10</th><th className="p-2.5 text-right font-medium">Recall@{c.top_k}</th><th className="p-2.5 text-right font-medium">Records examined</th><th className="p-2.5 text-right font-medium">Pool cut</th><th className="p-2.5 font-medium">Fallback</th><th className="p-2.5 text-right font-medium">Latency</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.strategy} className="border-b last:border-0">
                <td className="p-2.5 font-medium">{STRATEGY_LABEL[e.strategy as QueryMode]}</td>
                <td className="p-2.5 text-right tabular-nums">{e.metrics ? `${e.relevant_retrieved?.length ?? 0} / ${c.relevant_memories.length}` : "—"}</td>
                <td className={cn("p-2.5 text-right tabular-nums", e.metrics && bestN !== undefined && Math.abs(e.metrics.ndcg_at_10 - bestN) < 1e-9 && "font-semibold text-success")}>{e.metrics ? e.metrics.ndcg_at_10.toFixed(3) : "—"}</td>
                <td className="p-2.5 text-right tabular-nums">{e.metrics ? e.metrics.recall_at_k.toFixed(3) : "—"}</td>
                <td className="p-2.5 text-right tabular-nums">{num(e.candidate_count)}</td>
                <td className="p-2.5 text-right tabular-nums">{e.strategy === "soft" ? <span title="full global search">0%</span> : ratioPct(e.candidate_pool_reduction, 0)}</td>
                <td className="p-2.5">{e.fallback_used ? <Badge variant="outline" className="border-warning/50 text-warning">used</Badge> : <span className="text-muted-foreground">no</span>}</td>
                <td className="p-2.5 text-right tabular-nums">{ms(e.latency_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Tabs defaultValue={rows[0]?.strategy} className="gap-3">
        <TabsList className="max-w-full justify-start overflow-x-auto">{rows.map((e) => <TabsTrigger key={e.strategy} value={e.strategy}>{STRATEGY_LABEL[e.strategy as QueryMode]}</TabsTrigger>)}</TabsList>
        {rows.map((e) => (
          <TabsContent key={e.strategy} value={e.strategy} className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>{e.searched_modules.length ? `${e.searched_modules.length} modules searched: ${e.searched_modules.map((m) => m.name).join(", ")}` : "Whole corpus searched"}{e.fallback_used ? ` · global fallback: ${e.fallback_reason}` : ""}</span>
              <Button size="sm" variant="outline" className="h-9 gap-1.5" onClick={() => onGraph(e)}><Network className="size-3.5" />Show route on graph</Button>
            </div>
            {!!e.relevant_excluded_by_routing?.length && (
              <div className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-xs" role="alert">
                <b>{e.relevant_excluded_by_routing.length} labeled-relevant {e.relevant_excluded_by_routing.length === 1 ? "memory was" : "memories were"} in modules this strategy never searched:</b>
                <ul className="mt-1.5 space-y-1">{e.relevant_excluded_by_routing.map((x) => <li key={x.memory_id}><b>{x.module_name}</b> · {x.text_preview}</li>)}</ul>
              </div>
            )}
            <ol className="space-y-2" aria-label={`${e.label} results`}>
              {e.results.map((r) => (
                <li key={r.memory_id} className={cn("rounded-xl border p-3 text-sm", r.relevant && "border-success/40 bg-success-soft")}>
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                    <span className="grid size-5 place-items-center rounded-full bg-violet-soft font-semibold text-accent-foreground">{r.rank}</span>
                    <Badge variant="secondary">{r.module_name}</Badge>
                    {r.relevant === true && <span className="inline-flex items-center gap-1 font-medium text-success"><Check className="size-3.5" />labeled relevant</span>}
                    {r.relevant === false && <span className="inline-flex items-center gap-1 text-muted-foreground"><X className="size-3.5" />not labeled relevant</span>}
                    <span className="ml-auto tabular-nums">{r.score.toFixed(3)}{r.dense_score !== null && ` (dense ${r.dense_score.toFixed(3)})`}</span>
                  </div>
                  <p className="line-clamp-3 break-words text-sm">{r.text}</p>
                </li>
              ))}
            </ol>
          </TabsContent>
        ))}
      </Tabs>
      <ul className="list-disc space-y-1 pl-5 text-[11px] text-muted-foreground">{c.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  )
}
