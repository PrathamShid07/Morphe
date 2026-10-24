import { useState, type ReactNode } from "react"
import { Check, Copy } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { NO_DATA } from "@/lib/format"
import { cn } from "@/lib/utils"

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0 space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
        {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Section({ title, description, actions, children, className }: { title: string; description?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Card className={cn("rounded-2xl shadow-none", className)}>
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1 basis-64">
            <CardTitle className="text-base">{title}</CardTitle>
            {description && <CardDescription className="mt-1">{description}</CardDescription>}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

/** A KPI. `value === null` is rendered as "Not enough data yet", never as a misleading 0. */
export function StatCard({ label, value, hint, icon, loading, className }: { label: string; value: ReactNode | null; hint?: ReactNode; icon?: ReactNode; loading?: boolean; className?: string }) {
  return (
    <div className={cn("surface flex flex-col gap-2 p-4", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
        {icon && <span className="tile-icon size-8 [&_svg]:size-4">{icon}</span>}
      </div>
      {loading ? (
        <Skeleton className="h-8 w-24" />
      ) : value === null || value === undefined ? (
        <span className="text-sm font-medium text-muted-foreground">{NO_DATA}</span>
      ) : (
        <span className="text-2xl font-semibold tabular-nums tracking-tight">{value}</span>
      )}
      {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
    </div>
  )
}

export function MonoId({ id, short = 18, className, plain }: { id: string | null | undefined; short?: number; className?: string; plain?: boolean }) {
  const [copied, setCopied] = useState(false)
  if (!id) return <span className="text-muted-foreground">—</span>
  const label = id.length > short + 4 ? `${id.slice(0, short)}…` : id
  if (plain) return <span title={id} className={cn("mono-id inline-block max-w-full truncate align-bottom", className)}>{label}</span>
  return (
    <button
      type="button"
      title={`${id} (click to copy)`}
      className={cn("mono-id inline-flex max-w-full items-center gap-1 rounded px-1 py-0.5 hover:bg-muted", className)}
      onClick={async (e) => {
        e.stopPropagation()
        try { await navigator.clipboard.writeText(id); setCopied(true); setTimeout(() => setCopied(false), 1200) } catch { /* clipboard unavailable */ }
      }}
    >
      <span className="truncate">{label}</span>
      {copied ? <Check className="size-3 shrink-0" /> : <Copy className="size-3 shrink-0 opacity-50" />}
    </button>
  )
}

/** Token budget bar with the configured normal / target / emergency thresholds drawn on it. */
export function TokenGauge({ usage, start, target, emergency, tokens, budget }: { usage: number; start: number; target: number; emergency: number; tokens: number; budget: number }) {
  const tone = usage >= emergency ? "bg-destructive" : usage >= start ? "bg-warning" : "bg-primary"
  const marks = [[start, "compress"], [target, "target"], [emergency, "emergency"]] as const
  return (
    <div className="space-y-2" role="group" aria-label="Active-text token budget">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm tabular-nums"><span className="text-2xl font-semibold">{tokens.toLocaleString()}</span> <span className="text-muted-foreground">/ {budget.toLocaleString()} tokens</span></span>
        <span className="text-sm font-medium tabular-nums">{usage.toFixed(1)}%</span>
      </div>
      <div className="relative">
        <Progress value={Math.min(usage, 100)} aria-label="token budget usage" className="h-3 rounded-full" indicatorClassName={tone} />
        {marks.map(([v]) => <span key={v} aria-hidden className="absolute -top-1 h-5 w-0.5 -translate-x-1/2 rounded bg-foreground/60" style={{ left: `${Math.min(v, 100)}%` }} />)}
      </div>
      <p className="text-[11px] text-muted-foreground sm:hidden">{marks.map(([v, l]) => `${l} ${v}%`).join(" · ")}</p>
      <div className="relative h-6 text-[10px] text-muted-foreground sm:h-8">
        {marks.map(([v, label]) => (
          <span key={v} className="absolute top-0 -translate-x-1/2 whitespace-nowrap text-center leading-tight" style={{ left: `clamp(1.5rem, ${Math.min(v, 100)}%, calc(100% - 1.5rem))` }}>
            <b className="block text-[11px] font-medium text-foreground">{v}%</b><span className="hidden sm:inline">{label}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export function ConfirmDialog({
  open, onOpenChange, title, description, confirmLabel, onConfirm, destructive, busy, children,
}: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: ReactNode; confirmLabel: string
  onConfirm: () => void; destructive?: boolean; busy?: boolean; children?: ReactNode
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-h-[90dvh] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-2xl sm:max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          {description && <AlertDialogDescription asChild><div>{description}</div></AlertDialogDescription>}
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter className="gap-2 sm:gap-0">
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          <Button variant={destructive ? "destructive" : "default"} disabled={busy} onClick={onConfirm}>
            {busy ? "Working…" : confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function KeyValue({ items }: { items: { k: string; v: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map((i) => (
        <div key={i.k} className="contents">
          <dt className="text-muted-foreground">{i.k}</dt>
          <dd className="min-w-0 break-words text-right sm:text-left">{i.v}</dd>
        </div>
      ))}
    </dl>
  )
}
