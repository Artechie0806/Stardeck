import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useParams } from 'react-router'
import { Check, Database, Ellipsis, Eye, Link2, Move, Printer, RefreshCw, Sparkles, Undo2 } from 'lucide-react'
import { api, ApiError, type Cell, type ChartType, type DataSource, type FilterDef, type GridPos, type Stack, type Widget } from '../api'
import { ChatPanel } from '../components/ChatPanel'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { DashboardGrid } from '../components/DashboardGrid'
import { FilterBar } from '../components/FilterBar'
import { Popover } from '../components/Popover'
import { ShareDialog } from '../components/ShareDialog'
import { UpgradeNote } from '../components/Upgrade'
import { useCan } from '../lib/authContext'
import { copyText } from '../lib/browser'
import { DateOptionsContext } from '../lib/dateContext'
import { usePlan } from '../lib/plan'
import { useDashboardRunner } from '../lib/useDashboardRunner'

export function DashboardPage() {
  const { id = '' } = useParams()
  const canEdit = useCan('editor')
  const oneSource = !usePlan().has('multi_source')
  const d = useDashboardRunner(id)
  const [sources, setSources] = useState<DataSource[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [sharing, setSharing] = useState(false) // editors: public links and the workspace link
  const [editing, setEditing] = useState(false)
  // The assistant slides over the dashboard; it starts closed so the dashboard gets the whole screen.
  const [chatOpen, setChatOpen] = useState(false)
  const [chatMounted, setChatMounted] = useState(false) // stays mounted once opened, so the conversation survives
  const [draft, setDraft] = useState<{ text: string; n: number } | undefined>()
  const launcherRef = useRef<HTMLButtonElement>(null)
  // Whatever opened the assistant gets focus back when it closes.
  const opener = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  // A widget the assistant just added or changed: scrolled into view and briefly highlighted.
  const [flash, setFlash] = useState<{ id: string; n: number } | null>(null)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef<HTMLButtonElement>(null)
  const [dataOpen, setDataOpen] = useState(false)
  const dataRef = useRef<HTMLButtonElement>(null)
  // Removing a widget waits a few seconds so it can be undone.
  const [removing, setRemoving] = useState<{ id: string; title: string } | null>(null)
  const removeTimer = useRef<number | undefined>(undefined)
  const removingRef = useRef<string | null>(null)
  const reload = d.reload

  useEffect(() => { api.listSources().then(setSources).catch(() => {}) }, [])

  // Ctrl/Cmd+P opens the print-ready view, like the PDF button: the editing grid can't be paginated.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'p') {
        e.preventDefault()
        window.open(`/view/${encodeURIComponent(id)}?print=1`, '_blank', 'noopener')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id])

  // Leaving the page commits a pending removal instead of silently dropping it.
  useEffect(() => () => {
    window.clearTimeout(removeTimer.current)
    if (removingRef.current) void api.removeWidget(id, removingRef.current).catch(() => {})
  }, [id])

  const openChat = () => {
    const active = document.activeElement
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null
    setChatOpen(true)
    setChatMounted(true)
  }
  const closeChat = useCallback(() => setChatOpen(false), [])

  useEffect(() => {
    if (chatOpen) { wasOpen.current = true; return }
    if (!wasOpen.current) return
    wasOpen.current = false
    const back = opener.current?.isConnected && opener.current.offsetParent !== null ? opener.current : launcherRef.current
    back?.focus({ preventScroll: true })
  }, [chatOpen])

  useEffect(() => {
    if (!flash) return
    let frame = 0
    let tries = 0
    const find = () => { // the grid may need a frame or two to place a new widget
      const el = document.querySelector<HTMLElement>(`[data-widget-id="${CSS.escape(flash.id)}"]`)
      if (el) {
        const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        el.scrollIntoView({ block: 'center', behavior: still ? 'auto' : 'smooth' })
      } else if (tries++ < 30) frame = requestAnimationFrame(find)
    }
    find()
    const done = window.setTimeout(() => setFlash(null), 2600)
    return () => { cancelAnimationFrame(frame); window.clearTimeout(done) }
  }, [flash])

  const dashboard = d.def
  if (d.error && !dashboard) {
    return (
      <div className="page"><section className="empty"><h2>This dashboard couldn’t be opened</h2><p>{d.error}</p>
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

  const preset = d.view.preset
  const mine = sources.filter((s) => dashboard.sourceIds.includes(s.id))
  const sourceNames = dashboard.sourceIds.length > 1 ? Object.fromEntries(mine.map((s) => [s.id, s.name])) : undefined
  // Wrap mutations so a failure shows a message instead of an unhandled rejection.
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await reload()
    } catch (e) {
      setNotice((e as Error).message)
    }
  }
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
  const visible = d.widgets.filter((w) => w.id !== removing?.id)
  const onChangeType = (widgetId: string, type: ChartType, stack: Stack) =>
    act(() => api.updateWidget(id, widgetId, { chartType: type, stack }))
  const onRunSql = (w: Widget, sql: string) => api.previewWidget(w.sourceId ?? dashboard.sourceId, { ...w, sql }, d.ranges)
  const onSaveSql = async (widgetId: string, sql: string) => {
    await api.updateWidget(id, widgetId, { sql }) // the server refuses SQL that fails
    await reload()
  }
  const onPick = async (widgetId: string, column: string, value: Cell) => {
    try {
      d.toggleCross(await api.crossFilter(id, widgetId, column, value))
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 422)) setNotice((e as Error).message)
    }
  }
  const saveFilters = (filters: FilterDef[]) => act(() => api.updateDashboard(id, { filters }))
  const toggleSource = (sourceId: string) => {
    const has = dashboard.sourceIds.includes(sourceId)
    const next = has ? dashboard.sourceIds.filter((s) => s !== sourceId) : [...dashboard.sourceIds, sourceId]
    if (next.length) void act(() => api.updateDashboard(id, { sourceIds: next }))
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
    <DateOptionsContext.Provider value={d.dateOpts}>
    <div className="dashboard-layout">
      <div className="page dashboard-page">
        <div className="page-head">
          <div className="title-block">
            <h1>
              {canEdit ? (
                <input className="title-input" defaultValue={dashboard.name} key={dashboard.name} aria-label="Dashboard name"
                  onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()} />
              ) : dashboard.name}
            </h1>
            <p className="muted">{dashboard.description}</p>
            {mine.length > 1 && (
              <p className="source-chips">{mine.map((s) => <span key={s.id} className="badge"><Database size={11} aria-hidden /> {s.name}</span>)}</p>
            )}
          </div>
          <div className="head-actions">
            <button className="btn ghost wide-only" onClick={d.refresh} title="Re-run every query, skipping the cache">
              <RefreshCw size={15} aria-hidden className={d.loading ? 'spin' : ''} /> Refresh
            </button>
            {canEdit && (
              <button ref={dataRef} className="btn ghost wide-only" aria-haspopup="dialog" aria-expanded={dataOpen}
                onClick={() => setDataOpen((o) => !o)} title="Choose which data sources this dashboard uses">
                <Database size={15} aria-hidden /> Data
              </button>
            )}
            {dataOpen && (
              <Popover anchor={dataRef} onClose={() => setDataOpen(false)} className="slicer-menu" role="dialog" label="Data sources">
                <div className="menu-label">Data on this dashboard</div>
                <div className="slicer-values">
                  {sources.map((s) => {
                    const on = dashboard.sourceIds.includes(s.id)
                    return (
                      <label key={s.id} className={`slicer-option${on ? ' on' : ''}`}>
                        <input type="checkbox" checked={on}
                          disabled={on ? dashboard.sourceIds.length === 1 : oneSource && dashboard.sourceIds.length >= 1}
                          onChange={() => toggleSource(s.id)} />
                        <span>{s.name}</span>
                      </label>
                    )
                  })}
                </div>
                {oneSource ? <UpgradeNote>Several data sources on one dashboard is part of Stardeck Enterprise.</UpgradeNote>
                  : <p className="muted small slicer-note">Ask Stardeck for a chart and it picks the right source. A source still used by a chart can’t be removed.</p>}
              </Popover>
            )}
            {canEdit ? (
              <button className="btn ghost wide-only" onClick={() => setSharing(true)} aria-haspopup="dialog"
                title="Share a read-only link, with people in your workspace or anyone">
                <Link2 size={15} aria-hidden /> Share
              </button>
            ) : (
              <button className="btn ghost wide-only" onClick={copyLink}
                title="Copy a link to the read-only view. Anyone who can sign in to this Stardeck can open it.">
                {copied ? <Check size={15} aria-hidden /> : <Link2 size={15} aria-hidden />} {copied ? 'Link copied' : 'Share'}
              </button>
            )}
            <a className="btn ghost wide-only" href={`/view/${id}`} target="_blank" rel="noreferrer" title="Open the read-only view">
              <Eye size={15} aria-hidden /> View
            </a>
            <a className="btn ghost wide-only" href={`/view/${id}?print=1`} target="_blank" rel="noreferrer"
              title="Open the printable view; choose 'Save as PDF' in the print dialog">
              <Printer size={15} aria-hidden /> PDF
            </a>
            {canEdit && (
              <button className={`btn ${editing ? 'primary' : 'ghost'} wide-only`} onClick={() => setEditing((e) => !e)}>
                {editing ? <><Check size={15} aria-hidden /> Done</> : <><Move size={15} aria-hidden /> Edit layout</>}
              </button>
            )}
            <button ref={moreRef} className="btn ghost narrow-only" aria-label="More actions" aria-haspopup="menu"
              aria-expanded={moreOpen} onClick={() => setMoreOpen((o) => !o)}>
              <Ellipsis size={16} aria-hidden />
            </button>
            {moreOpen && (
              <Popover anchor={moreRef} onClose={() => setMoreOpen(false)} label="Dashboard actions">
                <button className="menu-item" onClick={() => { setMoreOpen(false); d.refresh() }}><RefreshCw size={15} /> Refresh data</button>
                {canEdit
                  ? <button className="menu-item" onClick={() => { setMoreOpen(false); setSharing(true) }}><Link2 size={15} /> Share…</button>
                  : <button className="menu-item" onClick={() => { setMoreOpen(false); void copyLink() }}><Link2 size={15} /> Copy share link</button>}
                <a className="menu-item" href={`/view/${id}`} target="_blank" rel="noreferrer" onClick={() => setMoreOpen(false)}><Eye size={15} /> Open read-only view</a>
                <a className="menu-item" href={`/view/${id}?print=1`} target="_blank" rel="noreferrer" onClick={() => setMoreOpen(false)}><Printer size={15} /> Save as PDF</a>
              </Popover>
            )}
          </div>
        </div>

        <FilterBar preset={preset} custom={d.view.custom} fiscalStart={d.dateOpts.fiscalStart} onDate={d.setDate}
          filters={dashboard.filters ?? []} selections={d.selections} onSelect={d.select}
          crossFilters={d.cross} onClearCross={d.clearCross}
          loadValues={(f, q) => api.filterValues(id, f.sourceId, f.table, f.column, q)}
          canEdit={canEdit} sources={mine.length ? mine : undefined}
          onAddFilter={(f) => void saveFilters([...(dashboard.filters ?? []), f])}
          onRemoveFilter={(fid) => { d.select(fid, []); void saveFilters((dashboard.filters ?? []).filter((f) => f.id !== fid)) }}
          dirty={canEdit && d.dirty} onMakeDefault={() => void d.makeDefault().catch((e: Error) => setNotice(e.message))}
          onClearAll={d.clearAll} />
        {(notice || d.error) && <p className="error banner" role="alert">{notice ?? d.error}
          <button className="icon-btn" onClick={() => { setNotice(null); d.setError(null) }} title="Dismiss">×</button></p>}
        {editing && <p className="edit-hint">Drag widgets by their title bar and resize from the bottom-right corner.</p>}
        {visible.length === 0 && (
          <section className="empty">
            <span className="icon-tile large"><Sparkles size={26} aria-hidden /></span>
            <h2>This dashboard is empty</h2>
            <p>{canEdit ? 'Ask Stardeck for a chart, e.g. “revenue by month”.' : 'Nothing has been added to this dashboard yet.'}</p>
            {canEdit && <button className="btn primary" onClick={openChat}><Sparkles size={15} aria-hidden /> Ask Stardeck</button>}
          </section>
        )}

        <DashboardGrid widgets={visible} preset={preset} editing={editing} readOnly={!canEdit} highlightId={flash?.id}
          onLayoutChange={onLayoutChange} onRemove={onRemove} onChangeType={onChangeType}
          onRunSql={onRunSql} onSaveSql={onSaveSql} onAsk={canEdit ? onAsk : undefined}
          onPick={(wid, column, value) => void onPick(wid, column, value)} picked={d.picked} refreshing={d.pending}
          filterLabels={d.filterLabels} sourceNames={sourceNames} defaultSourceId={dashboard.sourceId} />
      </div>

      {createPortal(
        <button ref={launcherRef} className={`chat-launcher${chatOpen ? ' is-hidden' : ''}`} onClick={openChat}
          aria-haspopup="dialog" aria-expanded={chatOpen} aria-controls={chatMounted ? 'stardeck-assistant' : undefined}>
          <Sparkles size={18} aria-hidden /> <span>Ask Stardeck</span>
        </button>,
        document.body,
      )}
      {chatMounted && (
        // Keyed by dashboard: a conversation (and its proposals) belongs to the dashboard it was about.
        <ErrorBoundary key={id} label="The assistant hit an error">
          <ChatPanel dashboardId={id} sourceId={dashboard.sourceId} preset={preset} ranges={d.ranges} filters={d.active}
            canEdit={canEdit} open={chatOpen} draft={draft}
            widgets={visible.map((w) => ({ id: w.id, title: w.title, type: w.spec.type }))}
            onApplied={async (widgetId) => { await reload(); if (widgetId) setFlash({ id: widgetId, n: Date.now() }) }}
            onShow={(widgetId) => {
              // Close the panel where it covers the dashboard, or when the widget sits behind it.
              const r = document.querySelector(`[data-widget-id="${CSS.escape(widgetId)}"]`)?.getBoundingClientRect()
              const panel = document.querySelector('.chat')?.getBoundingClientRect()
              const behind = !!r && !!panel && Math.min(r.right, panel.right) - Math.max(r.left, panel.left) > r.width * 0.4
              if (behind || window.matchMedia('(max-width: 1100px)').matches) setChatOpen(false)
              setFlash({ id: widgetId, n: Date.now() })
            }}
            onClose={closeChat} />
        </ErrorBoundary>
      )}
      {removing && (
        <div className="toast" role="status">
          <span>Removed “{removing.title}”</span>
          <button className="btn text small" onClick={undoRemove}><Undo2 size={14} aria-hidden /> Undo</button>
        </div>
      )}
      {sharing && <ShareDialog dashboardId={id} name={dashboard.name} onClose={() => setSharing(false)} />}
    </div>
    </DateOptionsContext.Provider>
  )
}
