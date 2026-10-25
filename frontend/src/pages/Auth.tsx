import { useState, type FormEvent, type ReactNode } from "react"
import { CheckCircle2, Eye, EyeOff, Loader2, LockKeyhole, ShieldCheck, UserPlus, UserRound } from "lucide-react"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Orb } from "@/components/app/Orb"
import { errorMessage } from "@/components/app/states"
import { useAuth } from "@/lib/auth"
import { useTheme } from "@/lib/theme"
import { Moon, Sun } from "lucide-react"

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/
export const PASSWORD_MIN = 10

/** The same rules the server enforces (it checks again; this only saves a round trip). */
export function passwordProblems(pw: string): string[] {
  const out: string[] = []
  if (pw.length < PASSWORD_MIN) out.push(`at least ${PASSWORD_MIN} characters`)
  if (pw && !(/[A-Za-z]/.test(pw) && /[^A-Za-z]/.test(pw))) out.push("letters plus at least one digit or symbol")
  return out
}

function PasswordField({ id, label, value, onChange, autoComplete, error, describedBy }: {
  id: string; label: string; value: string; onChange: (v: string) => void; autoComplete: string; error?: string | null; describedBy?: string
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input id={id} name={id} type={show ? "text" : "password"} autoComplete={autoComplete} value={value} onChange={(e) => onChange(e.target.value)}
          aria-invalid={!!error} aria-describedby={error ? `${id}-err` : describedBy} className="h-11 rounded-xl pr-12" />
        <button type="button" onClick={() => setShow((s) => !s)} aria-pressed={show} aria-label={show ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          className="absolute right-1.5 top-1/2 grid size-9 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
          {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
      {error && <p id={`${id}-err`} className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

function TextField({ id, label, value, onChange, autoComplete, type = "text", error, inputMode }: {
  id: string; label: string; value: string; onChange: (v: string) => void; autoComplete: string; type?: string; error?: string | null; inputMode?: "email" | "text"
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} name={id} type={type} inputMode={inputMode} autoComplete={autoComplete} autoCapitalize="none" spellCheck={false} value={value}
        onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} aria-describedby={error ? `${id}-err` : undefined} className="h-11 rounded-xl" />
      {error && <p id={`${id}-err`} className="text-xs text-destructive">{error}</p>}
    </div>
  )
}

function FormError({ message }: { message: string | null }) {
  return message ? <p role="alert" className="rounded-xl border border-destructive/30 bg-danger-soft p-3 text-sm">{message}</p> : null
}

function SubmitButton({ busy, busyLabel, children }: { busy: boolean; busyLabel: string; children: ReactNode }) {
  return (
    <Button type="submit" className="h-11 w-full rounded-xl" disabled={busy}>
      {busy ? <Loader2 className="size-4 animate-spin" /> : null}{busy ? busyLabel : children}
    </Button>
  )
}

function UserLoginForm({ onSwitch }: { onSwitch: () => void }) {
  const { loginUser, registrationEnabled } = useAuth()
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bad = { email: !EMAIL.test(email.trim()), password: !password }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (bad.email || bad.password || busy) return
    setBusy(true); setError(null)
    try { await loginUser(email.trim(), password) } catch (err) { setError(errorMessage(err).message); setPassword("") } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate aria-label="User sign in">
      <TextField id="login-email" label="Email address" value={email} onChange={setEmail} autoComplete="username" type="email" inputMode="email"
        error={touched && bad.email ? "Enter a valid email address." : null} />
      <PasswordField id="login-password" label="Password" value={password} onChange={setPassword} autoComplete="current-password" error={touched && bad.password ? "Enter your password." : null} />
      <FormError message={error} />
      <SubmitButton busy={busy} busyLabel="Signing in…">Sign in</SubmitButton>
      {registrationEnabled && (
        <p className="text-center text-sm text-muted-foreground">New to MORPHE?{" "}
          <button type="button" className="font-medium text-primary underline-offset-4 hover:underline" onClick={onSwitch}>Create an account</button></p>
      )}
    </form>
  )
}

function RegisterForm({ onSwitch }: { onSwitch: () => void }) {
  const { register, registrationEnabled } = useAuth()
  const [f, setF] = useState({ full_name: "", email: "", password: "", confirm_password: "" })
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f) => (v: string) => setF((x) => ({ ...x, [k]: v }))
  const pw = passwordProblems(f.password)
  const err = {
    full_name: f.full_name.trim().length < 2 ? "Enter your full name." : null,
    email: !EMAIL.test(f.email.trim()) ? "Enter a valid email address." : null,
    password: pw.length ? `Use ${pw.join(" and ")}.` : null,
    confirm_password: f.confirm_password !== f.password ? "The passwords do not match." : null,
  }
  const invalid = Object.values(err).some(Boolean)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (invalid || busy) return
    setBusy(true); setError(null)
    try { await register({ ...f, full_name: f.full_name.trim(), email: f.email.trim() }); setDone(true) } catch (e2) { setError(errorMessage(e2).message) } finally { setBusy(false) }
  }
  if (!registrationEnabled) return <Alert><AlertDescription>Account creation is turned off on this server. Ask an administrator for access.</AlertDescription></Alert>
  if (done) return <p role="status" className="flex items-center gap-2 rounded-xl border border-success/40 bg-success-soft p-3 text-sm"><CheckCircle2 className="size-4 text-success" />Account created. Opening your workspace…</p>
  return (
    <form onSubmit={submit} className="space-y-4" noValidate aria-label="Create account">
      <TextField id="reg-name" label="Full name" value={f.full_name} onChange={set("full_name")} autoComplete="name" error={touched ? err.full_name : null} />
      <TextField id="reg-email" label="Email address" value={f.email} onChange={set("email")} autoComplete="email" type="email" inputMode="email" error={touched ? err.email : null} />
      <PasswordField id="reg-password" label="Password" value={f.password} onChange={set("password")} autoComplete="new-password" error={touched ? err.password : null} describedBy="reg-password-hint" />
      <p id="reg-password-hint" className="-mt-2 text-xs text-muted-foreground">At least {PASSWORD_MIN} characters, with letters and at least one digit or symbol.</p>
      <PasswordField id="reg-confirm" label="Confirm password" value={f.confirm_password} onChange={set("confirm_password")} autoComplete="new-password" error={touched ? err.confirm_password : null} />
      <FormError message={error} />
      <SubmitButton busy={busy} busyLabel="Creating account…">Create account</SubmitButton>
      <p className="text-center text-xs text-muted-foreground">Your memories are private by default. Administrators can see operational activity and the questions you ask, for support and auditing.</p>
      <p className="text-center text-sm text-muted-foreground">Already have an account?{" "}
        <button type="button" className="font-medium text-primary underline-offset-4 hover:underline" onClick={onSwitch}>Sign in</button></p>
    </form>
  )
}

function AdminLoginForm() {
  const { loginAdmin, adminConfigured } = useAuth()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bad = { username: !username.trim(), password: !password }
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (bad.username || bad.password || busy) return
    setBusy(true); setError(null)
    try { await loginAdmin(username.trim(), password) } catch (err) { setError(errorMessage(err).message); setPassword("") } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-4" noValidate aria-label="Administrator sign in">
      <p className="text-sm text-muted-foreground">Sign in to access MORPHE's memory management, evaluation, and architecture controls.</p>
      {!adminConfigured && (
        <p role="alert" className="rounded-xl border border-warning/40 bg-warning-soft p-3 text-sm">
          Administrator sign-in has not been set up on this server yet. Run <code className="font-mono text-xs">backend/scripts/admin_credentials.py init</code> and restart the API.
        </p>
      )}
      <TextField id="admin-username" label="Username" value={username} onChange={setUsername} autoComplete="username" error={touched && bad.username ? "Enter your username." : null} />
      <PasswordField id="admin-password" label="Password" value={password} onChange={setPassword} autoComplete="current-password" error={touched && bad.password ? "Enter your password." : null} />
      <FormError message={error} />
      <Button type="submit" className="h-11 w-full rounded-xl" disabled={busy || !adminConfigured}>
        {busy ? <Loader2 className="size-4 animate-spin" /> : null}{busy ? "Signing in…" : "Sign in as administrator"}
      </Button>
    </form>
  )
}

/** What an unauthenticated visitor sees: nothing but sign-in, account creation and administrator sign-in. No product data is requested before this succeeds. */
export default function AuthPage() {
  const { registrationEnabled, accountDisabled } = useAuth()
  const { theme, toggle } = useTheme()
  const [tab, setTab] = useState("login")
  return (
    <div className="ambient flex min-h-dvh flex-col items-center justify-center px-4 py-8">
      <Button variant="ghost" size="icon" className="absolute right-3 top-3 size-11" onClick={toggle} aria-label="Toggle light and dark theme">
        {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
      </Button>
      <main className="w-full max-w-md space-y-6" id="main">
        <header className="space-y-2 text-center">
          <div className="mx-auto flex items-center justify-center gap-2.5"><Orb small pulse={false} className="size-8" /><span className="text-2xl font-semibold tracking-tight">MORPHE</span></div>
          <h1 className="text-xl font-semibold tracking-tight">Your memory, organised.</h1>
          <p className="text-sm text-muted-foreground">Sign in to your private memory, or create an account to start one.</p>
        </header>
        {accountDisabled && <Alert variant="destructive"><AlertDescription>This account has been disabled. Contact an administrator if you think this is a mistake.</AlertDescription></Alert>}
        <div className="surface p-4 sm:p-6">
          <Tabs value={tab} onValueChange={setTab} className="gap-5">
            <TabsList className="grid h-auto w-full grid-cols-3 gap-1 p-1">
              <TabsTrigger value="login" className="h-10 gap-1.5 px-1 text-xs sm:text-sm"><UserRound className="size-4" />Sign in</TabsTrigger>
              <TabsTrigger value="register" className="h-10 gap-1.5 px-1 text-xs sm:text-sm" disabled={!registrationEnabled}><UserPlus className="size-4" />Create account</TabsTrigger>
              <TabsTrigger value="admin" className="h-10 gap-1.5 px-1 text-xs sm:text-sm"><ShieldCheck className="size-4" />Administrator</TabsTrigger>
            </TabsList>
            <TabsContent value="login"><UserLoginForm onSwitch={() => setTab("register")} /></TabsContent>
            <TabsContent value="register"><RegisterForm onSwitch={() => setTab("login")} /></TabsContent>
            <TabsContent value="admin">
              <div className="mb-3 flex items-center gap-2 text-base font-semibold"><LockKeyhole className="size-4 text-primary" />Administrator sign in</div>
              <AdminLoginForm />
            </TabsContent>
          </Tabs>
        </div>
      </main>
    </div>
  )
}
