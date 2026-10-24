import { cn } from "@/lib/utils"

export function Orb({ className, pulse = true, small = false }: { className?: string; pulse?: boolean; small?: boolean }) {
  return <div aria-hidden className={cn("orb", small && "orb-sm", pulse && "orb-pulse", className)} />
}
