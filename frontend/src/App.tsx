import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DashboardsPage } from './pages/DashboardsPage'
import { DefinitionsPage } from './pages/DefinitionsPage'
import { NewDashboardPage } from './pages/NewDashboardPage'
import { SourcesPage } from './pages/SourcesPage'
import { TransformEditor } from './pages/TransformEditor'
import { TransformsPage } from './pages/TransformsPage'

// Chart-heavy pages (ECharts, grid layout) load on demand to keep the first load small.
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const ViewPage = lazy(() => import('./pages/ViewPage').then((m) => ({ default: m.ViewPage })))

const loading = <div className="page"><p className="muted">Loading…</p></div>

export default function App() {
  return (
    <ErrorBoundary>
    <Suspense fallback={loading}>
      <Routes>
        <Route path="view/:id" element={<ViewPage />} />
        <Route element={<AppShell />}>
          <Route index element={<DashboardsPage />} />
          <Route path="new" element={<NewDashboardPage />} />
          <Route path="d/:id" element={<DashboardPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="sources/:id/definitions" element={<DefinitionsPage />} />
          <Route path="sources/:id/transforms" element={<TransformsPage />} />
          <Route path="sources/:id/transforms/:name" element={<TransformEditor />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
        </Route>
      </Routes>
    </Suspense>
    </ErrorBoundary>
  )
}
