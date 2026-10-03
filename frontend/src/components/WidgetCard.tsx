import { Fragment, useRef, useState } from 'react'
import {
  CalendarOff, Check, CircleAlert, Code, Copy, Database, Download, Ellipsis, FilterX, LoaderCircle, Pencil, Play, Sparkles, Table2,
  Trash2, X,
} from 'lucide-react'
import type { Cell, ChartType, DatePreset, QueryResult, Stack, Widget } from '../api/types'
import { CHART_OPTIONS, deriveRoles, fitProblem, SINGLE_OPTIONS } from '../lib/chartFit'
import { copyText } from '../lib/browser'
import { downloadCsv } from '../lib/csv'
import { useDateOptions } from '../lib/dateContext'
import { presetRanges } from '../lib/dates'
import { DataTable } from './DataTable'
import { Popover } from './Popover'
import { WidgetVisual } from './WidgetVisual'

interface Props {
  widget: Widget
  preset: DatePreset
  editing: boolean
  /** View-only dashboards: no edits, SQL is read-only. */
  readOnly?: boolean
  /** Shared links: no SQL at all (the server doesn't send it). */
  hideSql?: boolean
  onRemove: () => void
  onChangeType: (t: ChartType, stack: Stack) => void
  /** Run SQL with the dashboard's current filters, without saving. */
  onRunSql: (sql: string) => Promise<QueryResult>
  onSaveSql: (sql: string) => Promise<void>
  /** Hand a broken widget to the assistant (editor only). */
  onAsk?: (text: string) => void
  /** Clicking a category filters the rest of the dashboard (cross-filtering). */
  onPick?: (column: string, value: Cell) => void
  /** What this chart is filtering the dashboard by, if someone clicked it. */
  picked?: Cell
  /** New results are on their way: keep showing the old ones, dimmed. */
  refreshing?: boolean
  /** Labels of the dashboard's active filters, by key (to say which ones don't reach this widget). */
  filterLabels?: Record<string, string>
  /** Shown when the dashboard mixes data sources. */
  sourceName?: string
}

export function WidgetCard({
  widget, preset, editing, readOnly = false, hideSql = false, onRemove, onChangeType, onRunSql, onSaveSql, onAsk, onPick, picked,
  refreshing, filterLabels = {}, sourceName,
}: Props) {
  const [view, setView] = useState<'chart' | 'data' | 'sql'>('chart')
  const [menuOpen, setMenuOpen] = useState(false)
  const moreRef = useRef<HTMLButtonElement>(null)
  const { spec, result } = widget
  const isKpi = spec.type === 'kpi'
  const unfiltered = !widget.dateFilter && preset !== 'all'
  const notReached = (widget.unaffected ?? []).map((k) => filterLabels[k]).filter(Boolean)

  return (
    <article className={`widget ${isKpi ? 'widget-kpi' : ''}${refreshing ? ' refreshing' : ''}`} aria-busy={refreshing || undefined}>
      <header className={`widget-head ${editing ? 'widget-drag' : ''}`}>
        <h3 title={widget.title}>{widget.title}</h3>
        <div className="widget-actions">
          {!isKpi && (
            <button className={`icon-btn ${view === 'data' ? 'active' : ''}`} aria-pressed={view === 'data'}
              title={view === 'data' ? 'Show chart' : 'Show data'} onClick={() => setView(view === 'data' ? 'chart' : 'data')}>
              <Table2 size={15} />
            </button>
          )}
          {!hideSql && (
            <button className={`icon-btn ${view === 'sql' ? 'active' : ''}`} aria-pressed={view === 'sql'}
              title={view === 'sql' ? 'Hide SQL' : 'View SQL'} onClick={() => setView(view === 'sql' ? 'chart' : 'sql')}>
              <Code size={15} />
            </button>
          )}
          <button ref={moreRef} className="icon-btn" title="More" aria-label={`More actions for ${widget.title}`}
            aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((o) => !o)}>
            <Ellipsis size={15} />
          </button>
          {menuOpen && (
              <Popover anchor={moreRef} onClose={() => setMenuOpen(false)} label="Widget actions">
                {result && (
                  <button role="menuitem" className="menu-item" onClick={() => { downloadCsv(result, widget.title); setMenuOpen(false) }}>
                    <Download size={15} /> Download CSV
                  </button>
                )}
                {!readOnly && (
                  <ChartTypeMenu widget={widget} onPick={(t, st) => { onChangeType(t, st); setView('chart'); setMenuOpen(false) }} />
                )}
                {!readOnly && (
                  <>
                    <div className="menu-sep" />
                    <button role="menuitem" className="menu-item danger" onClick={() => { setMenuOpen(false); onRemove() }}>
                      <Trash2 size={15} /> Remove from dashboard
                    </button>
                  </>
                )}
              </Popover>
          )}
        </div>
      </header>

      <div className="widget-body">
        {view === 'sql' && !hideSql ? (
          <SqlPanel widget={widget} preset={preset} readOnly={readOnly} onClose={() => setView('chart')} onRun={onRunSql}
            onSave={async (sql) => { await onSaveSql(sql); setView('chart') }} />
        ) : widget.error ? (
          <div className="widget-error" role="alert">
            <p className="widget-error-title"><CircleAlert size={16} aria-hidden /> This chart couldn’t load</p>
            {!hideSql && <details><summary>What went wrong</summary><p className="mono small">{widget.error}</p></details>}
            {!readOnly && (
              <div className="widget-error-actions">
                {onAsk && (
                  <button className="btn primary small" onClick={() => onAsk(`The "${widget.title}" chart fails with: ${widget.error}. Please fix it.`)}>
                    <Sparkles size={13} aria-hidden /> Ask the assistant to fix it
                  </button>
                )}
                <button className="btn ghost small" onClick={() => setView('sql')}><Pencil size={13} aria-hidden /> Edit SQL</button>
              </div>
            )}
          </div>
        ) : result ? (
          <WidgetVisual spec={spec} result={result} preset={preset} asTable={view === 'data'} dateFiltered={!!widget.dateFilter}
            onPick={view === 'chart' ? onPick : undefined} picked={picked} />
        ) : (
          <div className="widget-loading" aria-label="Loading"><span className="skeleton-bar" /><span className="skeleton-bar short" /></div>
        )}
      </div>

      <footer className="widget-summary" title={widget.summary}>
        {[
          unfiltered && <span key="dates" className="unfiltered" title="This widget doesn't use the dashboard date filter">
            <CalendarOff size={12} aria-hidden /> All dates
          </span>,
          notReached.length > 0 && <span key="filters" className="unfiltered" title="These filters don't apply to this widget's data">
            <FilterX size={12} aria-hidden /> Not filtered by {notReached.join(', ')}
          </span>,
          sourceName && <span key="source" className="widget-source"><Database size={11} aria-hidden /> {sourceName}</span>,
          widget.summary,
        ].filter(Boolean).map((part, i) => <Fragment key={i}>{i > 0 && ' · '}{part}</Fragment>)}
      </footer>
    </article>
  )
}

/** Chart types grouped like Power BI's visual picker; types that can't show this data are disabled. */
function ChartTypeMenu({ widget, onPick }: { widget: Widget; onPick: (t: ChartType, stack: Stack) => void }) {
  const { spec, result } = widget
  const single = spec.type === 'kpi' || spec.type === 'gauge'
  const options = single ? SINGLE_OPTIONS : CHART_OPTIONS
  const roles = result ? deriveRoles(result, spec) : null
  const groups = [...new Set(options.map((o) => o.group))]
  return (
    <div className="menu-types">
      {groups.map((g) => (
        <div key={g}>
          <div className="menu-label">{g}</div>
          {options.filter((o) => o.group === g).map((o) => {
            const current = spec.type === o.type && (spec.stack ?? 'none') === (o.stack ?? 'none')
            const problem = roles && !single ? fitProblem(o.type, roles) : null
            return (
              <button key={o.label} role="menuitemradio" aria-checked={current} className="menu-item"
                disabled={!!problem} title={problem ?? undefined} onClick={() => onPick(o.type, o.stack ?? 'none')}>
                {o.label}
                {current && <Check size={16} strokeWidth={3} className="menu-check" />}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function SqlPanel({ widget, preset, readOnly, onClose, onRun, onSave }: {
  widget: Widget; preset: DatePreset; readOnly: boolean; onClose: () => void
  onRun: (sql: string) => Promise<QueryResult>; onSave: (sql: string) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [sql, setSql] = useState(widget.sql)
  const [busy, setBusy] = useState<'run' | 'save' | null>(null)
  const [preview, setPreview] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const { range } = presetRanges(preset, useDateOptions())
  const filterNote = widget.dateFilter && range
    ? `Dashboard filter added when it runs: ${widget.dateFilter.column} from ${range.start} to ${range.end}.`
    : !widget.dateFilter && preset !== 'all' ? "This widget isn't restricted by the dashboard date filter." : null

  const copy = async () => {
    if (await copyText(sql)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  const act = async (kind: 'run' | 'save') => {
    setBusy(kind)
    setError(null)
    try {
      if (kind === 'run') setPreview(await onRun(sql))
      else await onSave(sql)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="sql-view">
      <div className="sql-toolbar">
        <span>{editing ? 'Editing SQL (read-only queries only)' : 'SQL Stardeck runs for this widget'}</span>
        {!editing && !readOnly && (
          <button className="icon-btn" onClick={() => setEditing(true)} title="Edit SQL"><Pencil size={14} /></button>
        )}
        <button className="icon-btn" onClick={copy} title="Copy SQL">{copied ? <Check size={14} /> : <Copy size={14} />}</button>
        <button className="icon-btn" onClick={onClose} title="Close"><X size={14} /></button>
      </div>
      {editing ? (
        <textarea className="sql-editor" value={sql} spellCheck={false} aria-label="SQL"
          onChange={(e) => { setSql(e.target.value); setPreview(null) }} />
      ) : (
        <pre>{widget.sql}</pre>
      )}
      {filterNote && <p className="sql-note">{filterNote}</p>}
      {error && <p className="error small" role="alert">{error}</p>}
      {preview && (
        <div className="sql-preview">
          <span className="muted small">{preview.rows.length} row{preview.rows.length === 1 ? '' : 's'}{preview.truncated ? ' (truncated)' : ''}</span>
          <DataTable result={preview} spec={widget.spec} />
        </div>
      )}
      {editing && (
        <div className="form-actions sql-actions">
          <button className="btn ghost small" disabled={busy !== null || !sql.trim()} onClick={() => act('run')}>
            {busy === 'run' ? <LoaderCircle size={13} className="spin" aria-hidden /> : <Play size={13} aria-hidden />} Run
          </button>
          <button className="btn primary small" disabled={busy !== null || !sql.trim() || sql === widget.sql}
            onClick={() => act('save')}>
            {busy === 'save' && <LoaderCircle size={13} className="spin" aria-hidden />} Save
          </button>
          <button className="btn ghost small" onClick={() => { setSql(widget.sql); setEditing(false); setPreview(null); setError(null) }}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
