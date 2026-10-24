import type { ReactNode } from "react"
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError } from "@/api/client"
import { cn } from "@/lib/utils"

export function EmptyState({ title, description, icon, action, className }: { title: string; description?: string; icon?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div role="status" className={cn("flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed px-6 py-10 text-center", className)}>
      <div className="tile-icon size-11">{icon ?? <Inbox className="size-5" />}</div>
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {description && <p className="mx-auto max-w-md text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function errorMessage(e: unknown): { message: string; code?: string; requestId?: string } {
  if (e instanceof ApiError) return { message: e.message, code: e.code, requestId: e.requestId }
  if (e instanceof Error) return { message: e.message }
  return { message: "Unexpected error" }
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const { message, code, requestId } = errorMessage(error)
  return (
    <div role="alert" className={cn("flex flex-col items-start gap-3 rounded-2xl border border-destructive/30 bg-danger-soft p-4 sm:flex-row sm:items-center", className)}>
      <AlertTriangle className="size-5 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-medium">{message}</p>
        {(code || requestId) && <p className="mono-id break-all">{[code, requestId].filter(Boolean).join(" · ")}</p>}
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Retry
        </Button>
      )}
    </div>
  )
}

export function LoadingBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-3", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-14 w-full rounded-xl" />
      ))}
    </div>
  )
}

/** Standard loading / error / empty handling around a react-query result. */
export function QueryBoundary<T>({
  query, children, empty, rows = 3, isEmpty,
}: {
  query: { data: T | undefined; isLoading: boolean; isError: boolean; error: unknown; refetch: () => unknown }
  children: (data: T) => ReactNode
  empty?: ReactNode
  rows?: number
  isEmpty?: (d: T) => boolean
}) {
  if (query.isLoading) return <LoadingBlock rows={rows} />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />
  if (query.data === undefined) return null
  if (isEmpty?.(query.data)) return <>{empty}</>
  return <>{children(query.data)}</>
}
