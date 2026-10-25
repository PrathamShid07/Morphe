import { CheckCircle2, CircleSlash, XCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { AuditEvent } from "@/api/types"

const LABEL: Record<string, string> = {
  "auth.registered": "Registered", "auth.register": "Registration attempt", "auth.login": "User sign-in", "auth.logout": "User sign-out",
  "admin.login": "Administrator sign-in", "admin.logout": "Administrator sign-out",
  "query.submitted": "Question submitted", "query.completed": "Question answered", "memory.created": "Memory saved", "memory.deleted": "Memory deleted",
  "memory.updated": "Memory updated", "graph.viewed": "Graph viewed", "user.disabled": "Account disabled", "user.reactivated": "Account reactivated",
  "admin.user_memories_accessed": "Memory content opened (support)", "version.restored": "Version restored", "version.checkpoint": "Checkpoint created",
  "adaptation.applied": "Adaptation applied", "adaptation.evaluated": "Adaptation evaluated", "adaptation.run": "Adaptation run", "compression.run": "Compression run",
  "ingestion.created": "Content ingested", "source.visibility_changed": "Source visibility changed", "benchmark.started": "Benchmark started",
  "admin.demo_reset": "Demo reset", "admin.demo_seed": "Demo seed",
}
export const actionLabel = (a: string) => LABEL[a] ?? a

export function EventStatus({ status }: { status: AuditEvent["status"] }) {
  const m = {
    success: { icon: CheckCircle2, cls: "text-success", text: "Success" },
    failure: { icon: XCircle, cls: "text-destructive", text: "Failed" },
    denied: { icon: CircleSlash, cls: "text-warning", text: "Denied" },
  }[status]
  const Icon = m.icon
  return <span className={cn("inline-flex items-center gap-1 text-xs font-medium", m.cls)}><Icon className="size-3.5" />{m.text}</span>
}

export function ActionBadge({ action }: { action: string }) {
  return <Badge variant="secondary" className="whitespace-normal text-left font-normal" title={action}>{actionLabel(action)}</Badge>
}

/** One short line of the most useful metadata (never memory text: the log does not contain it). */
export function metaSummary(e: AuditEvent): string {
  const m = e.metadata as Record<string, unknown>
  const bits: string[] = []
  if (typeof m.query === "string") bits.push(`“${m.query.length > 80 ? m.query.slice(0, 79) + "…" : m.query}”`)
  if (m.strategy) bits.push(String(m.strategy))
  if (typeof m.result_count === "number") bits.push(`${m.result_count} results`)
  if (typeof m.latency_ms === "number") bits.push(`${m.latency_ms} ms`)
  if (typeof m.chunks === "number") bits.push(`${m.chunks} pieces`)
  if (m.error_code) bits.push(String(m.error_code))
  if (m.reason) bits.push(`reason: ${String(m.reason)}`)
  if (typeof m.status_code === "number") bits.push(`HTTP ${m.status_code}`)
  return bits.join(" · ")
}

export function who(e: AuditEvent): string {
  if (e.actor_role === "admin") return `Admin${e.actor_label ? ` · ${e.actor_label}` : ""}`
  return e.user?.full_name ?? e.actor_label ?? e.subject_user_id ?? "Unknown"
}
