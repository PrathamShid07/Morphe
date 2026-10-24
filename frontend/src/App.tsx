import { lazy, Suspense } from "react"
import { Navigate, Route, Routes } from "react-router-dom"
import { AppShell } from "@/layouts/AppShell"
import { LoadingBlock } from "@/components/app/states"

// Route-level code splitting keeps the first load small (React Flow and recharts only load on the pages that use them).
const Overview = lazy(() => import("@/pages/Overview"))
const QueryPlayground = lazy(() => import("@/pages/QueryPlayground"))
const Memories = lazy(() => import("@/pages/Memories"))
const GraphExplorer = lazy(() => import("@/pages/GraphExplorer"))
const Compression = lazy(() => import("@/pages/Compression"))
const Adaptation = lazy(() => import("@/pages/Adaptation"))
const Metrics = lazy(() => import("@/pages/Metrics"))
const Versions = lazy(() => import("@/pages/Versions"))
const Benchmarks = lazy(() => import("@/pages/Benchmarks"))

const page = (el: React.ReactNode) => <Suspense fallback={<LoadingBlock rows={4} />}>{el}</Suspense>

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={page(<Overview />)} />
        <Route path="query" element={page(<QueryPlayground />)} />
        <Route path="memories" element={page(<Memories />)} />
        <Route path="graph" element={page(<GraphExplorer />)} />
        <Route path="compression" element={page(<Compression />)} />
        <Route path="adaptation" element={page(<Adaptation />)} />
        <Route path="metrics" element={page(<Metrics />)} />
        <Route path="versions" element={page(<Versions />)} />
        <Route path="benchmarks" element={page(<Benchmarks />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
