export const NO_DATA = "Not enough data yet"

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NO_DATA
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
}
export function score(v: number | null | undefined, digits = 3): string {
  return v === null || v === undefined ? NO_DATA : v.toFixed(digits)
}
export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? NO_DATA : `${v.toFixed(digits)}%`
}
export function ratioPct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? NO_DATA : `${(v * 100).toFixed(digits)}%`
}
export function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return NO_DATA
  return v < 10 ? `${v.toFixed(2)} ms` : v < 1000 ? `${v.toFixed(1)} ms` : `${(v / 1000).toFixed(2)} s`
}
export function mb(v: number | null | undefined): string {
  if (v === null || v === undefined) return "Not measurable"
  return v >= 1024 ? `${(v / 1024).toFixed(2)} GB` : `${v.toFixed(1)} MB`
}
export function delta(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined) return "—"
  const s = v.toFixed(digits)
  return v > 0 ? `+${s}` : s
}
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never"
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return iso
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 5) return "just now"
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" })
}
export function duration(msv: number | null | undefined): string {
  if (msv === null || msv === undefined) return "—"
  return msv < 1000 ? `${Math.round(msv)} ms` : `${(msv / 1000).toFixed(1)} s`
}
export const OPERATION_LABEL: Record<string, string> = {
  split_module: "Split module",
  merge_modules: "Merge modules",
  add_shortcut_edge: "Shortcut edge",
  restore_version: "Restore version",
  exact_duplicate_removal: "Exact duplicate removal",
  near_duplicate_consolidation: "Near-duplicate consolidation",
  summarize_group: "Summarize group",
  shorten_record: "Shorten record",
  emergency_delete: "Emergency delete",
  compression_job: "Compression job",
  checkpoint: "Checkpoint",
  baseline: "Baseline",
  restore: "Restore",
}
export const opLabel = (op: string) => OPERATION_LABEL[op] ?? op.replace(/_/g, " ")
