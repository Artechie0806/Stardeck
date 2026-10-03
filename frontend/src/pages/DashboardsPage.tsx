import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { LayoutDashboard, Plus } from 'lucide-react'
import { api, type DashboardSummary, type DataSource } from '../api'
import { ConfirmDelete } from '../components/ConfirmDelete'
import { relativeTime } from '../lib/format'

export function DashboardsPage() {
  const [dashboards, setDashboards] = useState<DashboardSummary[] | null>(null)
  const [sources, setSources] = useState<DataSource[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.listDashboards().then(setDashboards).catch((e: Error) => setError(e.message))
    api.listSources().then(setSources).catch(() => {})
  }, [])
  useEffect(load, [load])

  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name ?? id
  const remove = async (id: string) => {
    try {
      await api.deleteDashboard(id)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboards</h1>
          <p className="muted">Live dashboards built from your data. Open one to explore, edit, or ask the assistant.</p>
        </div>
        <Link to="/new" className="btn primary"><Plus size={16} aria-hidden /> New dashboard</Link>
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      {dashboards === null && !error && (
        <div className="card-grid" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="dash-card skeleton" />)}</div>
      )}
      {dashboards?.length === 0 && (
        <section className="empty">
          <span className="icon-tile large"><LayoutDashboard size={26} aria-hidden /></span>
          <h2>No dashboards yet</h2>
          <p>Connect a database or upload a spreadsheet, and Stardeck will build a dashboard for you.</p>
          <Link to={sources.length ? '/new' : '/sources'} className="btn primary">
            {sources.length ? 'Create your first dashboard' : 'Connect data'}
          </Link>
        </section>
      )}

      <div className="card-grid">
        {dashboards?.map((d) => (
          <div key={d.id} className="dash-card linked">
            <div className="card-top">
              <span className="icon-tile"><LayoutDashboard size={19} aria-hidden /></span>
              <ConfirmDelete label="Delete dashboard?" title="Delete dashboard" onConfirm={() => remove(d.id)} />
            </div>
            {/* The title is the link; its ::after stretches over the card, so the delete button isn't nested in it. */}
            <h2><Link to={`/d/${d.id}`} className="card-link">{d.name}</Link></h2>
            <p>{d.description}</p>
            <div className="dash-card-meta">
              <span>{d.widgetCount} {d.widgetCount === 1 ? 'widget' : 'widgets'}</span>
              <span>{sourceName(d.sourceId)}</span>
              <span>Updated {relativeTime(d.updatedAt)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
