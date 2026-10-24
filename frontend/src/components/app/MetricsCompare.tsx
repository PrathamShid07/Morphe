import { ArrowDown, ArrowUp, Check, Minus, X } from "lucide-react"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { NO_DATA, ms } from "@/lib/format"
import { cn } from "@/lib/utils"
import type { Check as CheckT, Metrics } from "@/api/types"

type Row = { key: string; label: string; get: (m: Metrics) => number | null; fmt: (v: number) => string; better: "up" | "down" | "none" }
const ROWS: Row[] = [
  { key: "recall", label: "Recall@5", get: (m) => m.recall_at_5, fmt: (v) => v.toFixed(3), better: "up" },
  { key: "mrr", label: "MRR", get: (m) => m.mrr, fmt: (v) => v.toFixed(3), better: "up" },
  { key: "p95", label: "p95 latency", get: (m) => m.p95_latency_ms, fmt: (v) => ms(v), better: "down" },
  { key: "cand", label: "Mean candidates", get: (m) => m.mean_candidate_count, fmt: (v) => v.toFixed(1), better: "down" },
  { key: "fb", label: "Fallback rate", get: (m) => m.fallback_rate, fmt: (v) => `${(v * 100).toFixed(1)}%`, better: "down" },
  { key: "n", label: "Sample size", get: (m) => m.sample_size, fmt: (v) => String(v), better: "none" },
]

/** Same-dataset comparison of two evaluation results. Unavailable values are shown as text, never as 0. */
export function MetricsCompare({ before, after, beforeLabel = "Baseline", afterLabel = "Candidate" }: { before: Metrics | null | undefined; after: Metrics | null | undefined; beforeLabel?: string; afterLabel?: string }) {
  if (!before || !after) return <p className="text-sm text-muted-foreground">{NO_DATA}: no evaluation was recorded for this operation.</p>
  return (
    <div className="overflow-x-auto rounded-xl border">
      <Table aria-label="Metric comparison">
        <TableHeader>
          <TableRow><TableHead>Metric</TableHead><TableHead className="text-right">{beforeLabel}</TableHead><TableHead className="text-right">{afterLabel}</TableHead><TableHead className="text-right">Change</TableHead></TableRow>
        </TableHeader>
        <TableBody>
          {ROWS.map((r) => {
            const a = r.get(before), b = r.get(after)
            const d = a !== null && b !== null ? b - a : null
            const good = d === null || r.better === "none" || Math.abs(d) < 1e-9 ? null : (r.better === "up" ? d > 0 : d < 0)
            return (
              <TableRow key={r.key}>
                <TableCell className="font-medium">{r.label}</TableCell>
                <TableCell className="text-right tabular-nums">{a === null ? NO_DATA : r.fmt(a)}</TableCell>
                <TableCell className="text-right tabular-nums">{b === null ? NO_DATA : r.fmt(b)}</TableCell>
                <TableCell className={cn("text-right tabular-nums", good === true && "text-success", good === false && "text-warning")}>
                  {d === null ? "—" : Math.abs(d) < 1e-9 ? <span className="inline-flex items-center gap-1 text-muted-foreground"><Minus className="size-3" />no change</span> : (
                    <span className="inline-flex items-center gap-1">{d > 0 ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />}{r.key === "p95" ? ms(Math.abs(d)) : r.key === "fb" ? `${(Math.abs(d) * 100).toFixed(1)}pp` : Math.abs(d).toFixed(r.key === "cand" ? 1 : 3)}</span>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

/** The configured acceptance checks, each shown as passed / failed with the numbers behind it. */
export function DecisionChecks({ checks }: { checks: CheckT[] }) {
  return (
    <ul className="space-y-1.5" aria-label="Acceptance checks">
      {checks.map((c) => (
        <li key={c.name} data-passed={c.passed} className={cn("flex items-start gap-2 rounded-xl border p-2.5 text-xs", c.passed ? "border-success/30 bg-success-soft" : "border-destructive/40 bg-danger-soft")}>
          {c.passed ? <Check className="mt-0.5 size-3.5 shrink-0 text-success" /> : <X className="mt-0.5 size-3.5 shrink-0 text-destructive" />}
          <span className="min-w-0"><b className="font-medium">{c.name.replace(/_/g, " ")}</b><span className="block text-muted-foreground">{c.detail}</span></span>
        </li>
      ))}
    </ul>
  )
}
