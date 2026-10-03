import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { Check, Eye, Link2, Move, Printer, RefreshCw, Sparkles } from 'lucide-react'
import { api, type ChartType, type Dashboard, type DatePreset, type GridPos, type Stack, type Widget } from '../api'
import { ChatPanel } from '../components/ChatPanel'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { DashboardGrid } from '../components/DashboardGrid'
import { FilterBar } from '../components/FilterBar'
import { copyText } from '../lib/browser'

export function DashboardPage() {
  const { id = '' } = useParams()
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  // Wide screens get the assistant docked open; on narrow ones it would cover the dashboard.
  const [chatOpen, setChatOpen] = useState(() => window.matchMedia('(min-width: 1101px)').matches)
  const request = useRef(0)

  /** Re-runs every widget. The previous render stays (dimmed) until fresh data lands. */
  const load = useCallback(async (fresh = false) => {
    const n = ++request.current
    setLoading(true)
    try {
      const d = await api.getDashboard(id, fresh)
      if (n === request.current) setDashboard(d)
    } catch (e) {
      if (n === request.current) setError((e as Error).message)
    } finally {
      if (n === request.current) setLoading(false)
    }
  }, [id])

  useEffect(() => { void load() }, [load])

  if (error) return <div className="page"><p className="error">{error}</p><Link to="/">Back to dashboards</Link></div>
  if (!dashboard) return <div className="page"><p className="muted">Running queries…</p></div>

  const preset = dashboard.datePreset
  // Wrap mutations so a failure shows a message instead of an unhandled rejection.
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await load()
    } catch (e) {
      setNotice((e as Error).message)
    }
  }
  const setPreset = (p: DatePreset) => act(() => api.updateDashboard(id, { datePreset: p }))
  const onLayoutChange = (layout: Record<string, GridPos>) => {
    if (editing) api.saveLayout(id, layout).catch((e: Error) => setNotice(e.message))
  }
  const onRemove = (widgetId: string) => act(() => api.removeWidget(id, widgetId))
  const onChangeType = (widgetId: string, type: ChartType, stack: Stack) =>
    act(() => api.updateWidget(id, widgetId, { chartType: type, stack }))
  const onRunSql = (w: Widget, sql: string) => api.previewWidget(dashboard.sourceId, { ...w, sql }, preset)
  const onSaveSql = async (widgetId: string, sql: string) => {
    await api.updateWidget(id, widgetId, { sql }) // the server refuses SQL that fails
    await load()
  }
  const viewUrl = `${window.location.origin}/view/${id}`
  const copyLink = async () => {
    if (await copyText(viewUrl)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } else {
      setNotice(`Copy this link: ${viewUrl}`)
    }
  }
  const rename = async (name: string) => {
    if (name.trim() && name.trim() !== dashboard.name) await act(() => api.updateDashboard(id, { name: name.trim() }))
  }

  return (
    <div className={`dashboard-layout ${chatOpen ? 'with-chat' : ''}`}>
      <div className="page dashboard-page">
        <div className="page-head">
          <div className="title-block">
            <h1>
              <input className="title-input" defaultValue={dashboard.name} key={dashboard.name} aria-label="Dashboard name"
                onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
            </h1>
            <p className="muted">{dashboard.description}</p>
          </div>
          <div className="head-actions">
            <button className="btn ghost" onClick={() => void load(true)} title="Re-run every query, skipping the cache">
              <RefreshCw size={15} aria-hidden className={loading ? 'spin' : ''} /> Refresh
            </button>
            <button className="btn ghost" onClick={copyLink} title="Copy a link to the read-only view">
              {copied ? <Check size={15} aria-hidden /> : <Link2 size={15} aria-hidden />} {copied ? 'Copied' : 'Share'}
            </button>
            <a className="btn ghost" href={`/view/${id}`} target="_blank" rel="noreferrer" title="Open the read-only view">
              <Eye size={15} aria-hidden /> View
            </a>
            <a className="btn ghost" href={`/view/${id}?print=1`} target="_blank" rel="noreferrer"
              title="Open the printable view; choose 'Save as PDF' in the print dialog">
              <Printer size={15} aria-hidden /> PDF
            </a>
            <button className={`btn ${editing ? 'primary' : 'ghost'}`} onClick={() => setEditing((e) => !e)}>
              {editing ? <><Check size={15} aria-hidden /> Done</> : <><Move size={15} aria-hidden /> Edit layout</>}
            </button>
            {!chatOpen && (
              <button className="btn primary" onClick={() => setChatOpen(true)}>
                <Sparkles size={15} aria-hidden /> Assistant
              </button>
            )}
          </div>
        </div>

        <FilterBar value={preset} onChange={setPreset} />
        {notice && <p className="error banner" role="alert">{notice}
          <button className="icon-btn" onClick={() => setNotice(null)} title="Dismiss">×</button></p>}
        {editing && <p className="edit-hint">Drag widgets by their title bar and resize from the bottom-right corner.</p>}
        {dashboard.widgets.length === 0 && (
          <section className="empty"><h2>This dashboard is empty</h2>
            <p>Ask the assistant for a chart, e.g. "revenue by month".</p></section>
        )}

        <div className={loading ? 'refetching' : undefined}>
          <DashboardGrid widgets={dashboard.widgets} preset={preset} editing={editing}
            onLayoutChange={onLayoutChange} onRemove={onRemove} onChangeType={onChangeType}
            onRunSql={onRunSql} onSaveSql={onSaveSql} />
        </div>
      </div>

      {chatOpen && (
        <ErrorBoundary label="The assistant hit an error">
          <ChatPanel dashboardId={id} sourceId={dashboard.sourceId} preset={preset}
            onApplied={() => void load()} onClose={() => setChatOpen(false)} />
        </ErrorBoundary>
      )}
    </div>
  )
}
