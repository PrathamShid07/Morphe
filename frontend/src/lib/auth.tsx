/** Who is using the app. The SERVER decides (GET /auth/me reads HttpOnly cookies); this module only mirrors that answer. Hiding UI based on it is a
 *  convenience, never the security boundary: every protected route checks the session again on the server. */
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { api, setCsrfToken, setSessionLostHandler } from "@/api/client"
import type { AccountUser, AuthSession } from "@/api/types"
import { setRoute } from "@/lib/route-store"

export interface RegisterInput { full_name: string; email: string; password: string; confirm_password: string }

export interface AuthState {
  loading: boolean
  authenticated: boolean
  role: "admin" | "user" | null
  /** The signed-in account (ordinary users only). */
  user: AccountUser | null
  /** Administrator username (administrators only). */
  username: string | null
  /** True for a server-verified administrator session. Name kept because the shared pages use it to decide what an operator may see. */
  isAdminView: boolean
  isAdminSession: boolean
  adminConfigured: boolean
  registrationEnabled: boolean
  /** The last session belonged to an account that an administrator has since disabled. */
  accountDisabled: boolean
  register: (input: RegisterInput) => Promise<void>
  loginUser: (email: string, password: string) => Promise<void>
  loginAdmin: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const noop = async () => {}
/** The safe default (no provider): nobody is signed in. */
export const AuthContext = createContext<AuthState>({
  loading: false, authenticated: false, role: null, user: null, username: null, isAdminView: false, isAdminSession: false, adminConfigured: true,
  registrationEnabled: true, accountDisabled: false, register: noop, loginUser: noop, loginAdmin: noop, logout: noop, refresh: noop,
})
export const useAuth = () => useContext(AuthContext)

async function fetchSession(): Promise<AuthSession> {
  const s = await api.get<AuthSession>("/auth/me")
  setCsrfToken(s.csrf_token)  // set here (not in an effect) so requests fired by child components already carry it
  return s
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ["auth", "session"], queryFn: fetchSession, staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 })
  const session = q.data
  const role = session?.authenticated ? session.role : null

  /** Drop every cached server response except the session itself, so nothing from the previous identity can linger on screen. */
  const forgetData = useCallback(() => {
    qc.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" })
    qc.getMutationCache().clear()
    setRoute(null)
  }, [qc])

  const refresh = useCallback(async () => { await qc.invalidateQueries({ queryKey: ["auth", "session"] }) }, [qc])

  // The server says the session is gone (expired, revoked, account disabled, API restarted): back to the sign-in page, and say so.
  useEffect(() => {
    setSessionLostHandler(async () => {
      setCsrfToken(null)
      await qc.invalidateQueries({ queryKey: ["auth", "session"] })
      forgetData()
      toast.warning("Your session ended. Please sign in again.")
      nav("/", { replace: true })
    })
    return () => setSessionLostHandler(null)
  }, [qc, nav, forgetData])

  // Return to the sign-in page exactly when the session expires, even if no request is made.
  useEffect(() => {
    if (!session?.expires_at) return
    const ms = Math.min(session.expires_at * 1000 - Date.now() + 500, 2 ** 31 - 1)
    const t = setTimeout(() => { void refresh().then(() => { forgetData(); nav("/", { replace: true }) }) }, Math.max(ms, 0))
    return () => clearTimeout(t)
  }, [session?.expires_at, refresh, forgetData, nav])

  const enter = useCallback((s: AuthSession) => {
    setCsrfToken(s.csrf_token)
    forgetData()
    qc.setQueryData(["auth", "session"], s)
    nav(s.role === "admin" ? "/" : "/query", { replace: true })
  }, [qc, nav, forgetData])

  const register = useCallback(async (input: RegisterInput) => { enter(await api.post<AuthSession>("/auth/register", input)) }, [enter])
  const loginUser = useCallback(async (email: string, password: string) => { enter(await api.post<AuthSession>("/auth/login", { email, password })) }, [enter])
  const loginAdmin = useCallback(async (username: string, password: string) => { enter(await api.post<AuthSession>("/auth/admin/login", { username, password })) }, [enter])

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout") } catch { /* an already-invalid session is the outcome we want anyway */ }
    setCsrfToken(null)
    forgetData()
    await qc.invalidateQueries({ queryKey: ["auth", "session"] })
    nav("/", { replace: true })
  }, [qc, nav, forgetData])

  const value = useMemo<AuthState>(() => ({
    loading: q.isLoading, authenticated: !!role, role, user: session?.user ?? null, username: session?.username ?? null, isAdminView: role === "admin",
    isAdminSession: role === "admin", adminConfigured: session?.admin_configured ?? true, registrationEnabled: session?.registration_enabled ?? true,
    accountDisabled: !!session?.account_disabled, register, loginUser, loginAdmin, logout, refresh,
  }), [q.isLoading, role, session, register, loginUser, loginAdmin, logout, refresh])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
