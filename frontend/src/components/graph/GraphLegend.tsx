import { cn } from "@/lib/utils"

function Item({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn("flex items-center gap-2", className)}>{children}</span>
}

export function GraphLegend({ route }: { route: boolean }) {
  return (
    <div className="surface space-y-2 px-4 py-3 text-xs" aria-label="Graph legend">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Item><svg width="30" height="10" aria-hidden><line x1="0" y1="5" x2="24" y2="5" stroke="var(--muted-foreground)" strokeWidth="2" /><path d="M24 1 L30 5 L24 9 Z" fill="var(--muted-foreground)" /></svg> hierarchy (parent → child)</Item>
        <Item><svg width="30" height="8" aria-hidden><line x1="0" y1="4" x2="30" y2="4" stroke="var(--chart-3)" strokeWidth="3" strokeDasharray="6 4" /></svg> co-access shortcut (label = times used together)</Item>
        <Item><span className="size-3 rounded border border-primary/30 bg-gradient-to-br from-violet-soft to-card" /> group</Item>
        <Item><span className="size-3 rounded border bg-card" /> content module (bar = share of split capacity)</Item>
        <Item><span className="size-3 rounded border-2 border-primary" /> selected</Item>
        <Item><span className="size-3 rounded border-2 border-warning" /> search match</Item>
      </div>
      {route && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t pt-2" aria-label="Route legend">
          <span className="font-medium text-foreground">Last query route:</span>
          <Item><span className="size-3 rounded bg-primary" /> seed module (profile match)</Item>
          <Item><span className="size-3 rounded bg-info" /> expanded module</Item>
          <Item><span className="size-3 rounded bg-success-soft ring-1 ring-success" /> contains returned memories</Item>
          <Item><span className="w-6 border-t-2 border-primary" /> edge the expansion went through</Item>
          <span className="text-muted-foreground">Faded modules were not searched or returned. “Returned” means a retrieved memory lives there, not that the module is the answer.</span>
        </div>
      )}
    </div>
  )
}
