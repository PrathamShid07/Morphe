/** Who is using the app. The SERVER decides (GET /auth/session reads HttpOnly cookies); this module only mirrors that answer and
 *  remembers which of the two experiences an administrator chose to look at. Hiding UI here is a convenience, never the security boundary. */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { api, setCsrfToken, setSessionLostHandler } from "@/api/client"
import type { AuthSession } from "@/api/types"
import { setRoute } from "@/lib/route-store"

const MODE_KEY = "morphe.view-mode.v1"
type ViewMode = "user" | "admin"

export interface AuthState {
  loading: boolean
  /** A server-verified administrator session exists (even if the UI is currently showing User Mode). */
  isAdminSession: boolean
  /** Admin Mode is showing: administrator session AND the administrator chose the full dashboard. */
  isAdminView: boolean
  username: string | null
  adminConfigured: boolean
  loginOpen: boolean
  openLogin: () => void
  closeLogin: () => void
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  switchToUser: () => void
  switchToAdmin: () => void
  refresh: () => Promise<void>
}

const noop = async () => {}
/** The safe default (no provider): an ordinary visitor. */
export const AuthContext = createContext<AuthState>({
  loading: false, isAdminSession: false, isAdminView: false, username: null, adminConfigured: true, loginOpen: false,
  openLogin: () => {}, closeLogin: () => {}, login: noop, logout: noop, switchToUser: () => {}, switchToAdmin: () => {}, refresh: noop,
})
export const useAuth = () => useContext(AuthContext)

function readMode(): ViewMode | null {
  try {
    const v = sessionStorage.getItem(MODE_KEY)
    return v === "user" || v === "admin" ? v : null
  } catch { return null }
}
function writeMode(m: ViewMode): void {
  try { sessionStorage.setItem(MODE_KEY, m) } catch { /* the choice then only lasts for this page view */ }
}

async function fetchSession(): Promise<AuthSession> {
  const s = await api.get<AuthSession>("/auth/session")
  setCsrfToken(s.csrf_token)  // set here (not in an effect) so requests fired by child components already carry it
  return s
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const [mode, setMode] = useState<ViewMode | null>(readMode)
  const [loginOpen, setLoginOpen] = useState(false)
  const q = useQuery({ queryKey: ["auth", "session"], queryFn: fetchSession, staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 })
  const session = q.data
  const isAdminSession = !!session?.authenticated && session.role === "admin"
  const isAdminView = isAdminSession && mode !== "user"

  /** Drop every cached server response except the session itself, so nothing from the other experience can linger on screen. */
  const forgetData = useCallback(() => {
    qc.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" })
    qc.getMutationCache().clear()
  }, [qc])

  const refresh = useCallback(async () => { await qc.invalidateQueries({ queryKey: ["auth", "session"] }) }, [qc])

  // The server says the administrator session is gone (expired, revoked, restarted): fall back to User Mode and say so.
  useEffect(() => {
    setSessionLostHandler(async () => {
      setCsrfToken(null)
      await qc.invalidateQueries({ queryKey: ["auth", "session"] })
      forgetData()
      toast.warning("Your administrator session ended. You are now in User Mode.")
      nav("/query", { replace: true })
    })
    return () => setSessionLostHandler(null)
  }, [qc, nav, forgetData])

  // Return to User Mode exactly when the session expires, even if no request is made.
  useEffect(() => {
    if (!session?.expires_at) return
    const ms = Math.min(session.expires_at * 1000 - Date.now() + 500, 2 ** 31 - 1)
    const t = setTimeout(() => { void refresh().then(() => { forgetData(); nav("/query", { replace: true }) }) }, Math.max(ms, 0))
    return () => clearTimeout(t)
  }, [session?.expires_at, refresh, forgetData, nav])

  const login = useCallback(async (username: string, password: string) => {
    const s = await api.post<AuthSession>("/auth/login", { username, password })  // throws ApiError (401/429/503) with the server's message
    setCsrfToken(s.csrf_token)
    qc.setQueryData(["auth", "session"], s)
    forgetData()
    writeMode("admin")
    setMode("admin")
    setLoginOpen(false)
    nav("/", { replace: true })
  }, [qc, nav, forgetData])

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout") } catch { /* an already-invalid session is the outcome we want anyway */ }
    setCsrfToken(null)
    setRoute(null)
    writeMode("user")
    setMode("user")
    forgetData()
    await qc.invalidateQueries({ queryKey: ["auth", "session"] })
    nav("/query", { replace: true })
  }, [qc, nav, forgetData])

  const switchToUser = useCallback(() => { writeMode("user"); setMode("user"); forgetData(); nav("/query", { replace: true }) }, [nav, forgetData])
  const switchToAdmin = useCallback(() => { if (isAdminSession) { writeMode("admin"); setMode("admin"); forgetData(); nav("/", { replace: true }) } }, [isAdminSession, nav, forgetData])

  const value = useMemo<AuthState>(() => ({
    loading: q.isLoading, isAdminSession, isAdminView, username: session?.username ?? null, adminConfigured: session?.admin_configured ?? true,
    loginOpen, openLogin: () => setLoginOpen(true), closeLogin: () => setLoginOpen(false), login, logout, switchToUser, switchToAdmin, refresh,
  }), [q.isLoading, isAdminSession, isAdminView, session, loginOpen, login, logout, switchToUser, switchToAdmin, refresh])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
