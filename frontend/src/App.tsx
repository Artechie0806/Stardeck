import { lazy, Suspense, type ReactNode } from 'react'
import { Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'
import { useCan } from './lib/authContext'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DashboardsPage } from './pages/DashboardsPage'
import { DefinitionsPage } from './pages/DefinitionsPage'
import { NewDashboardPage } from './pages/NewDashboardPage'
import { PeoplePage } from './pages/PeoplePage'
import { SettingsPage } from './pages/SettingsPage'
import { SourcesPage } from './pages/SourcesPage'
import { TransformEditor } from './pages/TransformEditor'
import { TransformsPage } from './pages/TransformsPage'

// Chart-heavy pages (ECharts, grid layout) load on demand to keep the first load small.
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const ViewPage = lazy(() => import('./pages/ViewPage').then((m) => ({ default: m.ViewPage })))

const loading = <div className="page"><p className="muted">Loading…</p></div>

/** Pages for building (editors and admins). Viewers get a short explanation instead. */
function EditorsOnly({ children }: { children: ReactNode }) {
  return useCan('editor') ? children : (
    <div className="page narrow"><section className="empty"><h2>This page is for editors</h2>
      <p>You can open dashboards, use their filters and ask the data analyst. Ask an admin if you need to build or change things.</p>
    </section></div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
    <Suspense fallback={loading}>
      <Routes>
        <Route path="view/:id" element={<ViewPage />} />
        <Route element={<AppShell />}>
          <Route index element={<DashboardsPage />} />
          <Route path="new" element={<EditorsOnly><NewDashboardPage /></EditorsOnly>} />
          <Route path="d/:id" element={<DashboardPage />} />
          <Route path="sources" element={<EditorsOnly><SourcesPage /></EditorsOnly>} />
          <Route path="sources/:id/definitions" element={<EditorsOnly><DefinitionsPage /></EditorsOnly>} />
          <Route path="sources/:id/transforms" element={<EditorsOnly><TransformsPage /></EditorsOnly>} />
          <Route path="sources/:id/transforms/:name" element={<EditorsOnly><TransformEditor /></EditorsOnly>} />
          <Route path="people" element={<PeoplePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
        </Route>
      </Routes>
    </Suspense>
    </ErrorBoundary>
  )
}
