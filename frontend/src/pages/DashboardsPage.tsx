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
        <h1>Dashboards</h1>
        <Link to="/new" className="btn primary"><Plus size={16} aria-hidden /> New dashboard</Link>
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      {dashboards?.length === 0 && (
        <section className="empty">
          <h2>No dashboards yet</h2>
          <p>Connect a database or upload a spreadsheet, and Stardeck will build a dashboard for you.</p>
          <Link to={sources.length ? '/new' : '/sources'} className="btn primary">
            {sources.length ? 'Create your first dashboard' : 'Connect data'}
          </Link>
        </section>
      )}

      <div className="card-grid">
        {dashboards?.map((d) => (
          <Link key={d.id} to={`/d/${d.id}`} className="dash-card">
            <div className="card-top">
              <LayoutDashboard size={20} className="dash-card-icon" aria-hidden />
              <ConfirmDelete label="Delete dashboard?" title="Delete dashboard" onConfirm={() => remove(d.id)} />
            </div>
            <h2>{d.name}</h2>
            <p>{d.description}</p>
            <div className="dash-card-meta">
              <span>{d.widgetCount} widgets</span>
              <span>{sourceName(d.sourceId)}</span>
              <span>Updated {relativeTime(d.updatedAt)}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
