import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const MAP: Record<string, { label: string; cls: string; pulse?: boolean }> = {
  running: { label: "Running", cls: "bg-info-soft text-info border-info/30", pulse: true },
  queued: { label: "Queued", cls: "bg-info-soft text-info border-info/30", pulse: true },
  completed: { label: "Completed", cls: "bg-success-soft text-success border-success/30" },
  accepted: { label: "Accepted", cls: "bg-success-soft text-success border-success/30" },
  rejected: { label: "Rejected", cls: "bg-warning-soft text-warning border-warning/30" },
  rolled_back: { label: "Rolled Back", cls: "bg-violet-soft text-accent-foreground border-primary/30" },
  failed: { label: "Failed", cls: "bg-danger-soft text-destructive border-destructive/30" },
  proposed: { label: "Proposed", cls: "bg-violet-soft text-accent-foreground border-primary/30" },
  simulated: { label: "Simulated", cls: "bg-muted text-muted-foreground border-border" },
  skipped: { label: "Skipped", cls: "bg-muted text-muted-foreground border-border" },
  idle: { label: "Idle", cls: "bg-muted text-muted-foreground border-border" },
  active: { label: "Active", cls: "bg-success-soft text-success border-success/30" },
  superseded: { label: "Superseded", cls: "bg-muted text-muted-foreground border-border" },
  deleted: { label: "Deleted", cls: "bg-danger-soft text-destructive border-destructive/30" },
  pending: { label: "Pending", cls: "bg-warning-soft text-warning border-warning/30" },
}

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const m = MAP[status] ?? { label: status.replace(/_/g, " "), cls: "bg-muted text-muted-foreground border-border" }
  return (
    <Badge variant="outline" data-status={status} className={cn("gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap", m.cls, className)}>
      <span className={cn("size-1.5 rounded-full bg-current", m.pulse && "animate-pulse")} />
      {m.label}
    </Badge>
  )
}
