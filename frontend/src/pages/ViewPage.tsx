import { useCallback, useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { LoaderCircle, Printer } from 'lucide-react'
import { api, ApiError, type Cell } from '../api'
import { DashboardGrid } from '../components/DashboardGrid'
import { FilterBar } from '../components/FilterBar'
import { Logo } from '../components/Logo'
import { useCan } from '../lib/authContext'
import { DateOptionsContext } from '../lib/dateContext'
import { formatRange, presetLabel } from '../lib/dates'
import { formatCategory } from '../lib/format'
import { PrintContext } from '../lib/printing'
import { ThemeContext, useTheme } from '../lib/themeContext'
import { useDashboardRunner } from '../lib/useDashboardRunner'
import { appClient, type ViewClient } from '../lib/viewClient'

/** Read-only dashboard for presenting and printing, for people who can sign in. */
export function ViewPage() {
  const { id = '' } = useParams()
  const canEdit = useCan('editor')
  return (
    <DashboardView id={id} client={appClient} title={(
      <p className="crumb view-brand"><Logo size={20} />
        {canEdit && <Link to={`/d/${id}`} className="btn ghost small no-print editor-link">Open in editor</Link>}</p>
    )} onGone={<Link to="/" className="btn primary">Go to Stardeck</Link>} />
  )
}

interface ViewProps {
  id: string
  /** The app's API, or a shared link's (stable across renders). */
  client: ViewClient
  /** Shared links: no SQL anywhere. */
  isPublic?: boolean
  /** What sits above the dashboard's name. */
  title?: ReactNode
  /** The way out when the dashboard can't be opened. */
  onGone?: ReactNode
}

/** A dashboard to look at: no editing, no assistant, no sidebar. Dates and filters can still be
 *  changed by whoever is looking; nothing is saved. */
export function DashboardView({ id, client, isPublic = false, title, onGone }: ViewProps) {
  const d = useDashboardRunner(id, client)
  const [printedAt] = useState(() => new Date().toLocaleString())
  const [params, setParams] = useSearchParams()
  // ?print=1 (Export PDF, or the editor's PDF button): the print-ready layout, then the print dialog.
  const printing = params.get('print') === '1'
  const dashboard = d.def
  const setPrinting = useCallback((on: boolean) => setParams((p) => {
    const next = new URLSearchParams(p)
    if (on) next.set('print', '1')
    else next.delete('print')
    return next
  }, { replace: true }), [setParams])

  // Paper is white: print with the light theme, whatever the screen uses (restored afterwards).
  const theme = useTheme()
  useLayoutEffect(() => {
    if (!printing) return
    const root = document.documentElement
    const before = root.getAttribute('data-theme')
    root.setAttribute('data-theme', 'light')
    return () => {
      if (before === null) root.removeAttribute('data-theme')
      else root.setAttribute('data-theme', before)
    }
  }, [printing])

  // Ctrl/Cmd+P prints the print-ready layout too.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'p') {
        e.preventDefault()
        setPrinting(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setPrinting])

  // Open the dialog once every chart has its data and has drawn at the page's width (maps also need
  // their tiles); afterwards, back to the screen layout.
  const hasMap = !!dashboard?.widgets.some((w) => w.spec.type === 'map')
  useEffect(() => {
    if (!printing || d.loading || !dashboard) return
    const done = () => setPrinting(false)
    window.addEventListener('afterprint', done, { once: true })
    const t = setTimeout(() => window.print(), hasMap ? 2500 : 1200)
    return () => {
      clearTimeout(t)
      window.removeEventListener('afterprint', done)
    }
  }, [printing, d.loading, dashboard, hasMap, setPrinting])

  if (d.error && !dashboard) {
    return (
      <div className="page"><section className="empty"><h2>This dashboard couldn’t be opened</h2><p>{d.error}</p>
        {onGone}</section></div>
    )
  }
  if (!dashboard) {
    return (
      <div className="page view-page" aria-busy="true">
        <div className="skeleton-line wide" /><div className="skeleton-line" />
        <div className="skeleton-grid">{[0, 1, 2, 3].map((i) => <div key={i} className={`widget skeleton s${i}`} />)}</div>
      </div>
    )
  }

  const onPick = async (widgetId: string, column: string, value: Cell) => {
    try {
      d.toggleCross(await client.crossFilter(id, widgetId, column, value))
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 422)) d.setError((e as Error).message)
    }
  }
  const filterText = d.active.map((f) => `${f.label}: ${(f.values?.length ? f.values : [f.value ?? null]).map(formatCategory).join(', ')}`)
  return (
    <DateOptionsContext.Provider value={d.dateOpts}>
    <ThemeContext.Provider value={printing ? { ...theme, resolved: 'light' } : theme}>
    <PrintContext.Provider value={printing}>
    {printing && (
      <p className="print-preparing no-print" role="status"><LoaderCircle size={15} className="spin" aria-hidden /> Preparing the PDF…</p>
    )}
    <div className={`page view-page${printing ? ' print-sheet' : ''}`}>
      <div className="page-head">
        <div>
          {title}
          <h1>{dashboard.name}</h1>
          <p className="muted">{dashboard.description}</p>
          <p className="print-only muted">
            {presetLabel(d.view.preset, d.dateOpts)} ({formatRange(d.ranges.range)})
            {filterText.length ? ` · ${filterText.join(' · ')}` : ''} · Printed {printedAt}
          </p>
        </div>
        <button className="btn ghost no-print" onClick={() => setPrinting(true)} disabled={printing}>
          <Printer size={15} aria-hidden /> Export PDF
        </button>
      </div>
      <div className="no-print" data-tour="filters">
        <FilterBar preset={d.view.preset} custom={d.view.custom} fiscalStart={d.dateOpts.fiscalStart} onDate={d.setDate}
          filters={dashboard.filters ?? []} selections={d.selections} onSelect={d.select}
          crossFilters={d.cross} onClearCross={d.clearCross}
          loadValues={(f, q) => client.filterValues(id, f, q)} onClearAll={d.clearAll} />
      </div>
      {d.error && <p className="error banner" role="alert">{d.error}</p>}
      <DashboardGrid widgets={d.widgets} preset={d.view.preset} editing={false} readOnly hideSql={isPublic}
        onLayoutChange={() => {}} onRemove={() => {}} onChangeType={() => {}}
        onRunSql={async (w, sql) => {
          if (isPublic) throw new Error('SQL isn’t available on shared dashboards.')
          return api.previewWidget(w.sourceId ?? dashboard.sourceId, { ...w, sql }, d.ranges)
        }}
        onSaveSql={async () => {}} onPick={(wid, c, v) => void onPick(wid, c, v)} picked={d.picked} refreshing={d.pending}
        filterLabels={d.filterLabels} defaultSourceId={dashboard.sourceId} printLayout={printing} />
    </div>
    </PrintContext.Provider>
    </ThemeContext.Provider>
    </DateOptionsContext.Provider>
  )
}
