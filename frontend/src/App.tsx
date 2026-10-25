import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AppShell } from "@/layouts/AppShell"
import { LoadingBlock } from "@/components/app/states"
import { useAuth } from "@/lib/auth"

// Route-level code splitting keeps the first load small (React Flow and recharts only load on the pages that use them).
const Overview = lazy(() => import("@/pages/Overview"))
const QueryPlayground = lazy(() => import("@/pages/QueryPlayground"))
const UserQuery = lazy(() => import("@/pages/UserQuery"))
const Memories = lazy(() => import("@/pages/Memories"))
const GraphExplorer = lazy(() => import("@/pages/GraphExplorer"))
const Compression = lazy(() => import("@/pages/Compression"))
const Adaptation = lazy(() => import("@/pages/Adaptation"))
const Metrics = lazy(() => import("@/pages/Metrics"))
const Versions = lazy(() => import("@/pages/Versions"))
const Benchmarks = lazy(() => import("@/pages/Benchmarks"))
const Users = lazy(() => import("@/pages/Users"))
const ActivityLog = lazy(() => import("@/pages/ActivityLog"))
const AuthPage = lazy(() => import("@/pages/Auth"))

const page = (el: React.ReactNode) => <Suspense fallback={<LoadingBlock rows={4} />}>{el}</Suspense>

/** Three completely separate route tables chosen by the SERVER-verified role. A visitor can only see the sign-in page; an ordinary user can only
 *  reach the Query Playground and Graph Explorer (administrator pages are not routable for them, and the API refuses their data anyway). */
export function App() {
  const { loading, authenticated, role } = useAuth()
  if (loading) return <div className="mx-auto max-w-3xl p-8"><LoadingBlock rows={3} /></div>
  if (!authenticated) return page(<AuthPage />)
  if (role === "admin") {
    return (
      <Routes>
        <Route element={<AppShell mode="admin" />}>
          <Route index element={page(<Overview />)} />
          <Route path="query" element={page(<QueryPlayground />)} />
          <Route path="memories" element={page(<Memories />)} />
          <Route path="graph" element={page(<GraphExplorer />)} />
          <Route path="compression" element={page(<Compression />)} />
          <Route path="adaptation" element={page(<Adaptation />)} />
          <Route path="metrics" element={page(<Metrics />)} />
          <Route path="versions" element={page(<Versions />)} />
          <Route path="benchmarks" element={page(<Benchmarks />)} />
          <Route path="users" element={page(<Users />)} />
          <Route path="activity" element={page(<ActivityLog />)} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    )
  }
  return (
    <Routes>
      <Route element={<AppShell mode="user" />}>
        <Route path="query" element={page(<UserQuery />)} />
        <Route path="graph" element={page(<GraphExplorer />)} />
        <Route path="*" element={<Navigate to="/query" replace />} />
      </Route>
    </Routes>
  )
}
