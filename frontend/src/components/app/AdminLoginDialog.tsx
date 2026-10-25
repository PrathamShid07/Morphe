import { useRef, useState, type FormEvent } from "react"
import { Eye, EyeOff, Loader2, LockKeyhole } from "lucide-react"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { errorMessage } from "@/components/app/states"
import { useAuth } from "@/lib/auth"

/** Administrator sign-in. It only ever talks to POST /auth/login; the browser never stores the password. */
export function AdminLoginDialog() {
  const { loginOpen, closeLogin, login, adminConfigured } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)
  const userRef = useRef<HTMLInputElement>(null)

  const reset = () => { setPassword(""); setShow(false); setError(null); setTouched(false); setBusy(false) }

  const missing = { username: !username.trim(), password: !password }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (missing.username || missing.password || busy) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
      reset()
    } catch (err) {
      setError(errorMessage(err).message)
      setPassword("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={loginOpen} onOpenChange={(o) => { if (!o) { reset(); closeLogin() } }}>
      <DialogContent className="max-w-[calc(100%-1.5rem)] gap-5 rounded-3xl p-5 sm:max-w-md sm:p-6" onOpenAutoFocus={(e) => { e.preventDefault(); userRef.current?.focus() }}>
        <DialogHeader className="space-y-2 text-left">
          <span className="tile-icon size-11"><LockKeyhole className="size-5" /></span>
          <DialogTitle className="text-xl">Administrator sign in</DialogTitle>
          <DialogDescription>Sign in to access MORPHE's memory management, evaluation, and architecture controls.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate aria-label="Administrator sign in">
          {!adminConfigured && (
            <p role="alert" className="rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm">
              Administrator sign-in has not been set up on this server yet. Run <code className="font-mono text-xs">backend/scripts/admin_credentials.py init</code> and restart the API.
            </p>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="admin-username">Username</Label>
            <Input id="admin-username" ref={userRef} name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username}
              onChange={(e) => setUsername(e.target.value)} aria-invalid={touched && missing.username} aria-describedby={touched && missing.username ? "admin-username-err" : undefined} className="h-11 rounded-xl" />
            {touched && missing.username && <p id="admin-username-err" className="text-xs text-destructive">Enter your username.</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="admin-password">Password</Label>
            <div className="relative">
              <Input id="admin-password" name="password" type={show ? "text" : "password"} autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} aria-invalid={touched && missing.password} aria-describedby={touched && missing.password ? "admin-password-err" : undefined} className="h-11 rounded-xl pr-12" />
              <button type="button" onClick={() => setShow((s) => !s)} aria-pressed={show} aria-label={show ? "Hide password" : "Show password"}
                className="absolute right-1.5 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
                {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
            {touched && missing.password && <p id="admin-password-err" className="text-xs text-destructive">Enter your password.</p>}
          </div>
          {error && <p role="alert" className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm">{error}</p>}
          <DialogFooter className="gap-2 sm:gap-2">
            <Button type="button" variant="ghost" className="h-11 rounded-xl" onClick={closeLogin}>Cancel</Button>
            <Button type="submit" className="h-11 min-w-28 rounded-xl" disabled={busy || !adminConfigured}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : null}{busy ? "Signing in…" : "Sign in"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
