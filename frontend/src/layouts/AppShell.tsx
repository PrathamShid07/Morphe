import { useState } from "react"
import { NavLink, Outlet, useLocation } from "react-router-dom"
import {
  Activity, Archive, BarChart3, Database, FlaskConical, GitBranch, LayoutDashboard, LockKeyhole, LogOut, Menu, Moon, Network, Plus, Search, ShieldCheck, Sun, User, Workflow,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Orb } from "@/components/app/Orb"
import { IngestProvider, useIngestDialog } from "@/components/app/IngestDialog"
import { AdminLoginDialog } from "@/components/app/AdminLoginDialog"
import { ErrorState } from "@/components/app/states"
import { useHealth, useSummary } from "@/api/hooks"
import { useAuth } from "@/lib/auth"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

export type ShellMode = "admin" | "user"

/** Administrator dashboard navigation (unchanged). */
export const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/query", label: "Query Playground", icon: Search },
  { to: "/memories", label: "Memories", icon: Database },
  { to: "/graph", label: "Graph Explorer", icon: Network },
  { to: "/compression", label: "Compression", icon: Archive },
  { to: "/adaptation", label: "Adaptation", icon: Workflow },
  { to: "/metrics", label: "Metrics", icon: BarChart3 },
  { to: "/versions", label: "Versions", icon: GitBranch },
  { to: "/benchmarks", label: "Benchmarks", icon: FlaskConical },
] as const

/** Visitor navigation: exactly two destinations. */
export const USER_NAV = [
  { to: "/query", label: "Query Playground", icon: Search },
  { to: "/graph", label: "Graph Explorer", icon: Network },
] as const

type NavItem = { to: string; label: string; icon: typeof Search; end?: boolean }

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <Orb small pulse={false} className="size-7" />
      <span className="text-lg font-semibold tracking-tight">MORPHE</span>
    </div>
  )
}

function NavList({ items, onNavigate }: { items: readonly NavItem[]; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={!!end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "group flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors",
              isActive
                ? "bg-gradient-to-r from-primary/5 via-primary/25 to-primary text-foreground shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--primary)_35%,transparent)]"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )
          }
        >
          <Icon className="size-[18px] shrink-0" />
          <span className="truncate">{label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

/** The explicit, always-visible statement of which experience is showing and what the session is. */
function ModeBlock({ mode, onNavigate }: { mode: ShellMode; onNavigate?: () => void }) {
  const { isAdminSession, username, openLogin, logout, switchToUser, switchToAdmin } = useAuth()
  if (mode === "admin") {
    return (
      <div className="space-y-2 rounded-2xl border bg-violet-soft/60 p-3" data-testid="mode-indicator" aria-label="Admin Mode">
        <div className="flex items-center gap-2 text-sm font-semibold"><ShieldCheck className="size-4 text-primary" />Admin Mode</div>
        <p className="truncate text-xs text-muted-foreground">Signed in as <b className="text-foreground">{username}</b></p>
        <div className="grid gap-1.5">
          <Button variant="outline" size="sm" className="h-10 justify-start gap-2 rounded-xl" onClick={() => { onNavigate?.(); switchToUser() }}><User className="size-4" />Switch to User Mode</Button>
          <Button variant="ghost" size="sm" className="h-10 justify-start gap-2 rounded-xl text-muted-foreground" onClick={() => { onNavigate?.(); void logout() }}><LogOut className="size-4" />Sign out</Button>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2 rounded-2xl border p-3" data-testid="mode-indicator" aria-label="User Mode">
      <div className="flex items-center gap-2 text-sm font-semibold"><User className="size-4 text-muted-foreground" />User Mode</div>
      {isAdminSession ? (
        <>
          <p className="text-xs text-muted-foreground">An administrator session is still active (<b className="text-foreground">{username}</b>).</p>
          <div className="grid gap-1.5">
            <Button variant="secondary" size="sm" className="h-10 justify-start gap-2 rounded-xl border" onClick={() => { onNavigate?.(); switchToAdmin() }}><ShieldCheck className="size-4" />Return to Admin Mode</Button>
            <Button variant="ghost" size="sm" className="h-10 justify-start gap-2 rounded-xl text-muted-foreground" onClick={() => { onNavigate?.(); void logout() }}><LogOut className="size-4" />Sign out</Button>
          </div>
        </>
      ) : (
        <Button variant="secondary" className="h-11 w-full justify-start gap-2 rounded-xl border" onClick={() => { onNavigate?.(); openLogin() }}><LockKeyhole className="size-4" />Admin Login</Button>
      )}
    </div>
  )
}

function AdminStatus() {
  const health = useHealth()
  const summary = useSummary()  // administrator endpoint: only ever mounted in Admin Mode
  const ready = health.data?.ready
  return (
    <div className="flex items-center gap-2 rounded-xl px-2 py-1.5 text-xs text-muted-foreground" data-testid="backend-status">
      <span className={cn("size-2 rounded-full", health.isError || ready === false ? "bg-destructive" : ready ? "bg-success" : "bg-muted-foreground animate-pulse")} />
      <span className="truncate">{health.isError ? "Backend unreachable" : ready === false ? "Backend degraded" : ready ? "Backend ready" : "Connecting…"}</span>
      {summary.data?.architecture_version && <span className="mono-id ml-auto shrink-0">{summary.data.architecture_version}</span>}
    </div>
  )
}

function UserStatus() {
  const health = useHealth()
  const ready = health.data?.ready
  return (
    <div className="flex items-center gap-2 rounded-xl px-2 py-1.5 text-xs text-muted-foreground" data-testid="backend-status">
      <span className={cn("size-2 rounded-full", health.isError || ready === false ? "bg-destructive" : ready ? "bg-success" : "bg-muted-foreground animate-pulse")} />
      <span className="truncate">{health.isError ? "Service unreachable" : ready === false ? "Service degraded" : ready ? "Service ready" : "Connecting…"}</span>
    </div>
  )
}

function SidebarFooter({ mode, onNavigate }: { mode: ShellMode; onNavigate?: () => void }) {
  const { theme, toggle } = useTheme()
  return (
    <div className="space-y-3 border-t pt-3">
      <ModeBlock mode={mode} onNavigate={onNavigate} />
      {mode === "admin" ? <AdminStatus /> : <UserStatus />}
      <Button variant="ghost" className="h-11 w-full justify-start gap-3 rounded-xl px-3 text-muted-foreground" onClick={toggle} aria-label="Toggle light and dark theme">
        {theme === "dark" ? <Sun className="size-[18px]" /> : <Moon className="size-[18px]" />}
        {theme === "dark" ? "Light mode" : "Dark mode"}
      </Button>
    </div>
  )
}

function IngestButton({ className }: { className?: string }) {
  const { open } = useIngestDialog()
  return (
    <Button variant="secondary" className={cn("h-11 w-full justify-start gap-2 rounded-xl border", className)} onClick={open}>
      <Plus className="size-4" /> Ingest content
    </Button>
  )
}

function Shell({ mode }: { mode: ShellMode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { theme, toggle } = useTheme()
  const { openLogin, switchToUser } = useAuth()
  const health = useHealth()
  const loc = useLocation()
  const items: readonly NavItem[] = mode === "admin" ? NAV : USER_NAV
  const current = items.find((n) => (n.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.to)))
  return (
    <div className="flex min-h-dvh w-full" data-mode={mode}>
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col gap-4 border-r bg-sidebar p-4 lg:flex">
        <Brand />
        {mode === "admin" && <IngestButton />}
        <div className="scroll-thin -mx-1 flex-1 overflow-y-auto px-1"><NavList items={items} /></div>
        <SidebarFooter mode={mode} />
      </aside>
      <div className="ambient flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/80 px-3 backdrop-blur sm:px-6 lg:px-8">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="size-11 lg:hidden" aria-label="Open navigation"><Menu className="size-5" /></Button>
            </SheetTrigger>
            <SheetContent side="left" className="flex w-[min(20rem,88vw)] flex-col gap-4 overflow-y-auto bg-sidebar p-4">
              <SheetHeader className="p-0 text-left"><SheetTitle asChild><div><Brand /></div></SheetTitle><SheetDescription className="sr-only">Navigation</SheetDescription></SheetHeader>
              {mode === "admin" && <IngestButton />}
              <div className="scroll-thin flex-1"><NavList items={items} onNavigate={() => setMobileOpen(false)} /></div>
              <SidebarFooter mode={mode} onNavigate={() => setMobileOpen(false)} />
            </SheetContent>
          </Sheet>
          <div className="flex min-w-0 items-center gap-2 lg:hidden"><Orb small pulse={false} className="size-5 shrink-0" /></div>
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-muted-foreground sm:text-base">{current?.label ?? "MORPHE"}</p>
          {mode === "admin" ? (
            <>
              <span className="hidden items-center gap-1.5 rounded-full border border-primary/40 bg-violet-soft px-3 py-1 text-xs font-medium sm:flex"><ShieldCheck className="size-3.5 text-primary" />Admin Mode</span>
              <Button variant="outline" size="sm" className="h-10 gap-1.5 rounded-xl lg:hidden" onClick={switchToUser} aria-label="Switch to User Mode"><User className="size-4" /><span className="hidden sm:inline">User Mode</span></Button>
            </>
          ) : (
            <>
              <span className="hidden items-center gap-1.5 rounded-full border px-3 py-1 text-xs text-muted-foreground sm:flex"><Activity className="size-3.5" />User Mode</span>
              <Button variant="outline" size="sm" className="h-10 gap-1.5 rounded-xl lg:hidden" onClick={openLogin} aria-label="Admin Login"><LockKeyhole className="size-4" /><span className="hidden sm:inline">Admin Login</span></Button>
            </>
          )}
          <Button variant="ghost" size="icon" className="size-11 lg:hidden" onClick={toggle} aria-label="Toggle light and dark theme">
            {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
          </Button>
        </header>
        <main className="mx-auto w-full max-w-[1400px] min-w-0 flex-1 px-3 py-5 sm:px-6 sm:py-8 lg:px-8" id="main">
          {health.isError && (
            <ErrorState className="mb-5" error={health.error} onRetry={() => health.refetch()} />
          )}
          {health.data && !health.data.ready && !health.isError && (
            <ErrorState className="mb-5" error={new Error(`Backend not ready: ${health.data.error?.message ?? "a dependency is unavailable"}`)} onRetry={() => health.refetch()} />
          )}
          <Outlet />
        </main>
      </div>
      <AdminLoginDialog />
    </div>
  )
}

export function AppShell({ mode = "admin" }: { mode?: ShellMode }) {
  return (
    <IngestProvider>
      <Shell mode={mode} />
    </IngestProvider>
  )
}
