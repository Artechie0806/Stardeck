import { useCallback, useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { Printer } from 'lucide-react'
import { api, type Dashboard, type DatePreset } from '../api'
import { DashboardGrid } from '../components/DashboardGrid'
import { FilterBar } from '../components/FilterBar'
import { PRESETS } from '../lib/dates'

/** Read-only dashboard for sharing and presenting: no editing, no assistant, no sidebar. */
export function ViewPage() {
  const { id = '' } = useParams()
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [preset, setPreset] = useState<DatePreset | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [printedAt] = useState(() => new Date().toLocaleString())
  const [params] = useSearchParams()
  const autoPrint = params.get('print') === '1'

  // Viewers can change the date range for themselves; it isn't saved to the dashboard.
  const load = useCallback(async (p: DatePreset | null) => {
    setLoading(true)
    try {
      const def = await api.getDashboardDef(id)
      const use = p ?? (def.datePreset as DatePreset)
      const results = await api.runDashboard(id, use)
      setDashboard({ ...def, datePreset: use, widgets: def.widgets.map((w) => ({ ...w, ...results[w.id] })) })
      setPreset(use)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { void load(null) }, [load])

  // ?print=1 (the editor's PDF button): open the print dialog once charts have drawn.
  useEffect(() => {
    if (!autoPrint || loading || !dashboard) return
    const t = setTimeout(() => window.print(), 1200)
    return () => clearTimeout(t)
  }, [autoPrint, loading, dashboard])

  if (error) return <div className="page"><p className="error">{error}</p></div>
  if (!dashboard || !preset) return <div className="page"><p className="muted">Running queries…</p></div>

  const label = PRESETS.find((p) => p.value === preset)?.label
  return (
    <div className="page view-page">
      <div className="page-head">
        <div>
          <p className="crumb">✦ Stardeck · <Link to={`/d/${id}`}>Open editor</Link></p>
          <h1>{dashboard.name}</h1>
          <p className="muted">{dashboard.description}</p>
          <p className="print-only muted">Date range: {label} · Printed {printedAt}</p>
        </div>
        <button className="btn ghost no-print" onClick={() => window.print()}><Printer size={15} aria-hidden /> Export PDF</button>
      </div>
      <div className="no-print"><FilterBar value={preset} onChange={(p) => void load(p)} /></div>
      <div className={loading ? 'refetching' : undefined}>
        <DashboardGrid widgets={dashboard.widgets} preset={preset} editing={false} readOnly
          onLayoutChange={() => {}} onRemove={() => {}} onChangeType={() => {}}
          onRunSql={(w, sql) => api.previewWidget(dashboard.sourceId, { ...w, sql }, preset)}
          onSaveSql={async () => {}} />
      </div>
    </div>
  )
}
