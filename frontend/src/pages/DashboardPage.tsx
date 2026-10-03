import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import { Check, Ellipsis, Eye, Link2, Move, Printer, RefreshCw, Sparkles, Undo2 } from 'lucide-react'
import { api, type ChartType, type Dashboard, type DatePreset, type GridPos, type Stack, type Widget } from '../api'
import { ChatPanel } from '../components/ChatPanel'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { DashboardGrid } from '../components/DashboardGrid'
import { FilterBar } from '../components/FilterBar'
import { Popover } from '../components/Popover'
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
  const [chatMounted, setChatMounted] = useState(chatOpen) // stays mounted once opened, so the conversation survives
  const [draft, setDraft] = useState<{ text: string; n: number } | undefined>()
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLButtonElement>(null)
  // Removing a widget waits a few seconds so it can be undone.
  const [removing, setRemoving] = useState<{ id: string; title: string } | null>(null)
  const removeTimer = useRef<number | undefined>(undefined)
  const removingRef = useRef<string | null>(null)
  const request = useRef(0)
  // The date range being viewed. Changing it doesn't touch the saved default until "Make default".
  const viewPreset = useRef<DatePreset | null>(null)
  const [savedPreset, setSavedPreset] = useState<DatePreset | null>(null)

  // Leaving the page commits a pending removal instead of silently dropping it.
  useEffect(() => () => {
    window.clearTimeout(removeTimer.current)
    if (removingRef.current) void api.removeWidget(id, removingRef.current).catch(() => {})
  }, [id])

  /** Re-runs every widget. The previous render stays (dimmed) until fresh data lands. */
  const load = useCallback(async (fresh = false) => {
    const n = ++request.current
    setLoading(true)
    try {
      const def = await api.getDashboardDef(id)
      const preset = viewPreset.current ?? (def.datePreset as DatePreset)
      const results = await api.runDashboard(id, preset, fresh)
      if (n === request.current) {
        setSavedPreset(def.datePreset as DatePreset)
        setDashboard({ ...def, datePreset: preset, widgets: def.widgets.map((w) => ({ ...w, ...results[w.id] })) })
      }
    } catch (e) {
      if (n === request.current) setError((e as Error).message)
    } finally {
      if (n === request.current) setLoading(false)
    }
  }, [id])

  useEffect(() => { void load() }, [load])

  const openChat = () => { setChatOpen(true); setChatMounted(true) }

  if (error) {
    return (
      <div className="page"><section className="empty"><h2>This dashboard couldn’t be opened</h2><p>{error}</p>
        <Link to="/" className="btn primary">Back to dashboards</Link></section></div>
    )
  }
  if (!dashboard) {
    return (
      <div className="page" aria-busy="true">
        <div className="skeleton-line wide" /><div className="skeleton-line" />
        <div className="skeleton-grid">{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} className={`widget skeleton s${i}`} />)}</div>
        <p className="visually-hidden" role="status">Running queries…</p>
      </div>
    )
  }

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
  const setPreset = (p: DatePreset) => { viewPreset.current = p; void load() }
  const makeDefault = () => act(async () => {
    await api.updateDashboard(id, { datePreset: preset })
    viewPreset.current = null
  })
  const onLayoutChange = (layout: Record<string, GridPos>) => {
    if (editing) api.saveLayout(id, layout).catch((e: Error) => setNotice(e.message))
  }
  const commitRemoval = (widgetId: string) => {
    removingRef.current = null
    setRemoving(null)
    void act(() => api.removeWidget(id, widgetId))
  }
  const onRemove = (widgetId: string) => {
    window.clearTimeout(removeTimer.current)
    if (removingRef.current) commitRemoval(removingRef.current)
    removingRef.current = widgetId
    setRemoving({ id: widgetId, title: dashboard.widgets.find((w) => w.id === widgetId)?.title ?? 'Widget' })
    removeTimer.current = window.setTimeout(() => commitRemoval(widgetId), 6000)
  }
  const undoRemove = () => {
    window.clearTimeout(removeTimer.current)
    removingRef.current = null
    setRemoving(null)
  }
  const onAsk = (text: string) => { setDraft({ text, n: Date.now() }); openChat() }
  const visible = dashboard.widgets.filter((w) => w.id !== removing?.id)
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
            <button className="btn ghost wide-only" onClick={() => void load(true)} title="Re-run every query, skipping the cache">
              <RefreshCw size={15} aria-hidden className={loading ? 'spin' : ''} /> Refresh
            </button>
            <button className="btn ghost wide-only" onClick={copyLink}
              title="Copy a link to the read-only view. Anyone who can reach this Stardeck (and sign in, if a password is set) can open it.">
              {copied ? <Check size={15} aria-hidden /> : <Link2 size={15} aria-hidden />} {copied ? 'Link copied' : 'Share'}
            </button>
            <a className="btn ghost wide-only" href={`/view/${id}`} target="_blank" rel="noreferrer" title="Open the read-only view">
              <Eye size={15} aria-hidden /> View
            </a>
            <a className="btn ghost wide-only" href={`/view/${id}?print=1`} target="_blank" rel="noreferrer"
              title="Open the printable view; choose 'Save as PDF' in the print dialog">
              <Printer size={15} aria-hidden /> PDF
            </a>
            <button className={`btn ${editing ? 'primary' : 'ghost'} wide-only`} onClick={() => setEditing((e) => !e)}>
              {editing ? <><Check size={15} aria-hidden /> Done</> : <><Move size={15} aria-hidden /> Edit layout</>}
            </button>
            {!chatOpen && (
              <button className="btn primary" onClick={openChat}>
                <Sparkles size={15} aria-hidden /> Assistant
              </button>
            )}
            <button ref={moreRef} className="btn ghost narrow-only" aria-label="More actions" aria-haspopup="menu"
              aria-expanded={moreOpen} onClick={() => setMoreOpen((o) => !o)}>
              <Ellipsis size={16} aria-hidden />
            </button>
            {moreOpen && (
              <Popover anchor={moreRef} onClose={() => setMoreOpen(false)} label="Dashboard actions">
                <button className="menu-item" onClick={() => { setMoreOpen(false); void load(true) }}><RefreshCw size={15} /> Refresh data</button>
                <button className="menu-item" onClick={() => { setMoreOpen(false); void copyLink() }}><Link2 size={15} /> Copy share link</button>
                <a className="menu-item" href={`/view/${id}`} target="_blank" rel="noreferrer" onClick={() => setMoreOpen(false)}><Eye size={15} /> Open read-only view</a>
                <a className="menu-item" href={`/view/${id}?print=1`} target="_blank" rel="noreferrer" onClick={() => setMoreOpen(false)}><Printer size={15} /> Save as PDF</a>
              </Popover>
            )}
          </div>
        </div>

        <div className="filter-row">
          <FilterBar value={preset} onChange={setPreset} />
          {savedPreset && preset !== savedPreset && (
            <button className="btn text small" onClick={() => void makeDefault()}
              title="Everyone opening this dashboard (including the shared view) will start with this date range">
              Make default
            </button>
          )}
        </div>
        {notice && <p className="error banner" role="alert">{notice}
          <button className="icon-btn" onClick={() => setNotice(null)} title="Dismiss">×</button></p>}
        {editing && <p className="edit-hint">Drag widgets by their title bar and resize from the bottom-right corner.</p>}
        {visible.length === 0 && (
          <section className="empty">
            <span className="icon-tile large"><Sparkles size={26} aria-hidden /></span>
            <h2>This dashboard is empty</h2>
            <p>Ask the assistant for a chart, e.g. “revenue by month”.</p>
            {!chatOpen && <button className="btn primary" onClick={openChat}><Sparkles size={15} aria-hidden /> Open assistant</button>}
          </section>
        )}

        <div className={loading ? 'refetching' : undefined}>
          <DashboardGrid widgets={visible} preset={preset} editing={editing}
            onLayoutChange={onLayoutChange} onRemove={onRemove} onChangeType={onChangeType}
            onRunSql={onRunSql} onSaveSql={onSaveSql} onAsk={onAsk} />
        </div>
      </div>

      {chatMounted && (
        <ErrorBoundary label="The assistant hit an error">
          <ChatPanel dashboardId={id} sourceId={dashboard.sourceId} preset={preset} open={chatOpen} draft={draft}
            widgets={visible.map((w) => ({ title: w.title, type: w.spec.type }))}
            onApplied={() => void load()} onClose={() => setChatOpen(false)} />
        </ErrorBoundary>
      )}
      {removing && (
        <div className="toast" role="status">
          <span>Removed “{removing.title}”</span>
          <button className="btn text small" onClick={undoRemove}><Undo2 size={14} aria-hidden /> Undo</button>
        </div>
      )}
    </div>
  )
}
