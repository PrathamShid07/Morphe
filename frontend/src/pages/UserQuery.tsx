import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ArrowRight, History, Loader2, Network, Search, Send, Sparkles, TriangleAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Progress } from "@/components/ui/progress"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { EmptyState, ErrorState } from "@/components/app/states"
import { useMyQueries, useQuery_, fetchStoredQuery } from "@/api/hooks"
import type { QueryMode, QueryResponse } from "@/api/types"
import { DEMO_QUERIES } from "@/lib/examples"
import { routeFromResponse, setRoute } from "@/lib/route-store"
import { describeRoute, WEAK_MATCH_SCORE } from "@/lib/route-text"
import { MODE_ORDER, STRATEGY_HINT, STRATEGY_LABEL } from "@/lib/strategies"
import { ms, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

interface View { query: string; mode: QueryMode; response: QueryResponse }

/** Visitor landing page: a memory assistant, not an administration console. Uses the real retrieval engine through the visitor-safe API. */
export default function UserQuery() {
  const [text, setText] = useState("")
  const [topK, setTopK] = useState("5")
  const [mode, setMode] = useState<QueryMode>("progressive")
  const [view, setView] = useState<View | null>(null)
  const [opening, setOpening] = useState<string | null>(null)
  const nav = useNavigate()
  const m = useQuery_()
  const history = useMyQueries()

  const run = (q = text) => {
    const query = q.trim()
    if (!query || m.isPending) return
    const body = { query, top_k: Number(topK), debug_trace: true, mode }
    m.mutate(body, { onSuccess: (response) => { setView({ query, mode, response }); setRoute(routeFromResponse(query, mode, response)) } })
  }
  const onSubmit = (e: FormEvent) => { e.preventDefault(); run() }
  const openPast = async (id: string) => {
    setOpening(id)
    try {
      const r = await fetchStoredQuery(id)
      const md = (r.mode as QueryMode) in STRATEGY_LABEL ? (r.mode as QueryMode) : "graph"
      setText(r.query)
      setView({ query: r.query, mode: md, response: r })
      setRoute(routeFromResponse(r.query, md, r))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not open that question")
    } finally {
      setOpening(null)
    }
  }

  const data = view?.response
  const best = data?.results[0]?.score
  const weak = !!data && data.results.length > 0 && typeof best === "number" && best < WEAK_MATCH_SCORE
  const searched = data?.trace?.searched_modules?.length
  const status = m.isPending ? "Searching memory…" : m.isError ? "The last search failed" : data ? (searched ? `Searched ${searched} module${searched === 1 ? "" : "s"}` : "Searched all memories") : "Ready"

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <header className="space-y-3 pt-2 text-center sm:pt-6">
        <span className="tile-icon mx-auto size-12"><Sparkles className="size-6" /></span>
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Ask your memory anything.</h1>
        <p className="mx-auto max-w-xl text-sm text-muted-foreground sm:text-base">
          Ask questions about the knowledge stored in MORPHE and inspect how the memory graph helps retrieve relevant information.
        </p>
      </header>

      <form onSubmit={onSubmit} className="surface space-y-4 p-4 sm:p-5" aria-label="Ask a question">
        <Label htmlFor="user-question" className="sr-only">Your question</Label>
        <Textarea id="user-question" value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={2000} placeholder="e.g. How do I make Django reuse PostgreSQL connections?"
          className="min-h-24 resize-none border-0 bg-transparent px-1 text-base shadow-none focus-visible:ring-0 sm:text-lg"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run() } }} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3 border-t pt-3">
          <div className="flex items-center gap-2">
            <Label htmlFor="user-topk" className="text-xs text-muted-foreground">Results</Label>
            <Select value={topK} onValueChange={setTopK}>
              <SelectTrigger id="user-topk" className="h-10 w-20 rounded-full" aria-label="Number of results"><SelectValue /></SelectTrigger>
              <SelectContent>{["3", "5", "8", "10", "20"].map((k) => <SelectItem key={k} value={k}>{k}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <Label htmlFor="user-mode" className="text-xs text-muted-foreground">Retrieval</Label>
            <Select value={mode} onValueChange={(v) => setMode(v as QueryMode)}>
              <SelectTrigger id="user-mode" className="h-10 w-52 max-w-full rounded-full" aria-label="Retrieval strategy"><SelectValue /></SelectTrigger>
              <SelectContent>{MODE_ORDER.map((k) => <SelectItem key={k} value={k}>{STRATEGY_LABEL[k]}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={!text.trim() || m.isPending} className="ml-auto h-11 min-w-32 gap-2 rounded-xl">
            {m.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}Ask
          </Button>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="mode-hint"><b className="text-foreground">{STRATEGY_LABEL[mode]}:</b> {STRATEGY_HINT[mode]}</p>
      </form>

      <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground" role="status" aria-live="polite" data-testid="retrieval-status">
        <span className={cn("size-2 rounded-full", m.isPending ? "animate-pulse bg-warning" : m.isError ? "bg-destructive" : "bg-success")} />{status}
        {data && !m.isPending && <span>· {ms(data.latency_ms)}</span>}
      </div>

      {m.isPending && !data && (
        <div className="surface flex items-center justify-center gap-3 p-8 text-sm text-muted-foreground" aria-busy="true" aria-label="Searching">
          <Loader2 className="size-5 animate-spin" />Searching MORPHE's memory…
        </div>
      )}
      {m.isError && <ErrorState error={m.error} onRetry={() => run()} />}

      {!data && !m.isPending && !m.isError && (
        <section aria-label="Example questions" className="space-y-3">
          <h2 className="text-center text-sm font-medium text-muted-foreground">Try one of these</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {DEMO_QUERIES.map((s) => (
              <button key={s} type="button" onClick={() => { setText(s); run(s) }} className="surface flex items-center justify-between gap-3 p-4 text-left text-sm transition hover:border-primary/50">
                <span>{s}</span><ArrowRight className="size-4 shrink-0 text-muted-foreground" />
              </button>
            ))}
          </div>
        </section>
      )}

      {data && (
        <section className="space-y-5" aria-live="polite" aria-label="Answer">
          <div className="surface space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="max-w-full truncate" title={view?.query}>“{view?.query}”</Badge>
              <Badge variant="outline">{STRATEGY_LABEL[view!.mode]}</Badge>
              <span className="text-xs text-muted-foreground">{data.results.length} result{data.results.length === 1 ? "" : "s"} · {ms(data.latency_ms)}</span>
              <Button size="sm" variant="outline" className="ml-auto h-9 gap-1.5" onClick={() => nav("/graph")} disabled={!data.trace}><Network className="size-3.5" />Show route on graph</Button>
            </div>
            {data.trace ? (
              <div data-testid="route-explanation" className="space-y-1 text-sm text-muted-foreground">
                <h2 className="text-xs font-semibold uppercase tracking-wide">How MORPHE searched</h2>
                {describeRoute(data.trace, view!.mode).map((l, i) => <p key={i}>{l}</p>)}
              </div>
            ) : <p className="text-sm text-muted-foreground">No routing explanation was provided for this search.</p>}
          </div>

          {data.results.length === 0 ? (
            <EmptyState title="No relevant memories were found" description="MORPHE has nothing stored that matches this question. Try different words, or ask about a topic you know has been added." icon={<Search className="size-5" />} />
          ) : (
            <>
              {weak && (
                <p role="note" className="flex items-start gap-2 rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm"><TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
                  These are the closest memories, but none is a strong match for your question. Treat them as hints and try rephrasing.</p>
              )}
              <ol className="space-y-3" aria-label="Retrieved memories">
                {data.results.map((r, i) => (
                  <li key={r.memory_id} className="surface p-4">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="grid size-6 place-items-center rounded-full bg-violet-soft font-semibold text-accent-foreground">{i + 1}</span>
                      <Badge variant="secondary">{r.module_name ?? r.module_id}</Badge>
                      {r.source_name && <span className="text-muted-foreground">{r.source_name}{r.section ? ` · ${r.section}` : ""}</span>}
                      <span className="ml-auto flex items-center gap-2 tabular-nums"><Progress value={Math.max(0, r.score) * 100} className="h-1.5 w-16" aria-hidden /><b>{r.score.toFixed(3)}</b></span>
                    </div>
                    <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed">{r.text}</p>
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      )}

      {history.data && history.data.items.length > 0 && (
        <section aria-label="Your recent questions" className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold"><History className="size-4" />Your recent questions</h2>
          <p className="text-xs text-muted-foreground">Only you can see these; they belong to this browser session.</p>
          <ul className="space-y-1.5">
            {history.data.items.map((h) => (
              <li key={h.query_id}>
                <button type="button" onClick={() => void openPast(h.query_id)} disabled={opening === h.query_id}
                  className="flex min-h-11 w-full items-center gap-3 rounded-xl border px-3 py-2 text-left text-sm hover:bg-muted">
                  <span className="min-w-0 flex-1 truncate">{h.query}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{h.result_count} results · {timeAgo(h.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
