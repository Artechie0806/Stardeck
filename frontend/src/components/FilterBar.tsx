import { useEffect, useMemo, useRef, useState } from 'react'
import {
  CalendarDays, Check, ChevronDown, Filter, ListFilter, LoaderCircle, MousePointerClick, Plus, Search, Trash2, X,
} from 'lucide-react'
import type { ActiveFilter, Cell, ColumnValues, DataSource, DatePreset, DateRange, FilterDef } from '../api/types'
import { formatRange, PRESET_GROUPS, presetLabel, presetRanges } from '../lib/dates'
import { formatCategory, humanize } from '../lib/format'
import { Popover } from './Popover'

interface Props {
  preset: DatePreset
  custom: DateRange | null
  fiscalStart: number
  onDate: (preset: DatePreset, custom?: DateRange) => void
  filters: FilterDef[]
  selections: Record<string, Cell[]>
  onSelect: (id: string, values: Cell[]) => void
  crossFilters: ActiveFilter[]
  onClearCross: (key: string) => void
  loadValues: (f: FilterDef, q: string) => Promise<ColumnValues>
  /** Editors: add and remove filters. */
  canEdit?: boolean
  sources?: DataSource[]
  onAddFilter?: (f: FilterDef) => void
  onRemoveFilter?: (id: string) => void
  /** The view differs from the saved defaults: offer to save it. */
  dirty?: boolean
  onMakeDefault?: () => void
  onClearAll?: () => void
}

/** The dashboard's filter row: date range, filters (slicers), and cross-filters from clicking charts. */
export function FilterBar(p: Props) {
  const anyActive = p.crossFilters.length > 0 || Object.values(p.selections).some((v) => v.length > 0)
  return (
    <div className="filter-bar" role="toolbar" aria-label="Filters">
      <DateFilter preset={p.preset} custom={p.custom} fiscalStart={p.fiscalStart} onChange={p.onDate} />
      {p.filters.map((f) => (
        <SlicerChip key={f.id} def={f} selected={p.selections[f.id] ?? []} onChange={(v) => p.onSelect(f.id, v)}
          load={(q) => p.loadValues(f, q)} onRemove={p.canEdit && p.onRemoveFilter ? () => p.onRemoveFilter?.(f.id) : undefined} />
      ))}
      {p.crossFilters.map((c) => (
        <button key={c.key} className="filter-chip cross active" onClick={() => p.onClearCross(c.key)}
          title="Clicked on a chart. Click to clear.">
          <MousePointerClick size={14} aria-hidden />
          <span className="chip-name">{c.label}</span>
          <span className="chip-value">{formatCategory(c.value ?? null)}</span>
          <X size={13} aria-hidden />
        </button>
      ))}
      {p.canEdit && p.sources && p.onAddFilter && <AddFilter sources={p.sources} existing={p.filters} onAdd={p.onAddFilter} />}
      {(anyActive || p.dirty) && (
        <span className="filter-actions">
          {anyActive && p.onClearAll && <button className="btn text small" onClick={p.onClearAll}>Clear filters</button>}
          {p.dirty && p.onMakeDefault && (
            <button className="btn text small" onClick={p.onMakeDefault}
              title="Everyone opening this dashboard (and the shared view) starts with these dates and filters">
              Make default
            </button>
          )}
        </span>
      )}
    </div>
  )
}

function DateFilter({ preset, custom, fiscalStart, onChange }: {
  preset: DatePreset; custom: DateRange | null; fiscalStart: number; onChange: (p: DatePreset, custom?: DateRange) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLButtonElement>(null)
  const opts = { fiscalStart, custom }
  const range = presetRanges(preset, opts).range
  const [start, setStart] = useState(custom?.start ?? range?.start ?? '')
  const [end, setEnd] = useState(custom?.end ?? range?.end ?? '')
  const pick = (p: DatePreset) => { setOpen(false); onChange(p) }
  return (
    <>
      <button ref={ref} className="filter-chip date-chip" aria-haspopup="menu" aria-expanded={open}
        onClick={() => { setStart(custom?.start ?? range?.start ?? ''); setEnd(custom?.end ?? range?.end ?? ''); setOpen((o) => !o) }}>
        <CalendarDays size={15} aria-hidden />
        <span className="chip-name">{presetLabel(preset, opts)}</span>
        {preset !== 'all' && preset !== 'custom' && <span className="chip-sub">{formatRange(range)}</span>}
        <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <Popover anchor={ref} align="start" onClose={() => setOpen(false)} className="date-menu" label="Date range">
          {PRESET_GROUPS.map((g) => (
            <div key={g.label || 'all'} className="date-group">
              {g.label && <div className="menu-label">{g.label}</div>}
              {g.items.map((it) => (
                <button key={it.value} role="menuitemradio" aria-checked={preset === it.value} className="menu-item"
                  onClick={() => pick(it.value)}>
                  <span>{it.value === 'this_fy' || it.value === 'last_fy' ? `${it.label} (${presetLabel(it.value, opts)})` : it.label}</span>
                  {preset === it.value && <Check size={15} className="menu-check" aria-hidden />}
                </button>
              ))}
            </div>
          ))}
          <div className="menu-sep" />
          <div className="menu-label">Custom range</div>
          <form className="custom-range" onSubmit={(e) => { e.preventDefault(); if (start && end && end >= start) { setOpen(false); onChange('custom', { start, end }) } }}>
            <input type="date" aria-label="From" value={start} max={end || undefined} onChange={(e) => setStart(e.target.value)} />
            <span aria-hidden>–</span>
            <input type="date" aria-label="To" value={end} min={start || undefined} onChange={(e) => setEnd(e.target.value)} />
            <button className="btn primary small" disabled={!start || !end || end < start}>Apply</button>
          </form>
        </Popover>
      )}
    </>
  )
}

function SlicerChip({ def, selected, onChange, load, onRemove }: {
  def: FilterDef; selected: Cell[]; onChange: (v: Cell[]) => void; load: (q: string) => Promise<ColumnValues>
  onRemove?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [data, setData] = useState<ColumnValues | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLButtonElement>(null)
  const label = (v: Cell) => data?.labels[String(v)] ?? formatCategory(v)
  // The page re-renders as results stream in; that shouldn't re-fetch the values.
  const loader = useRef(load)
  useEffect(() => { loader.current = load })

  useEffect(() => {
    if (!open) return
    let live = true
    const t = window.setTimeout(() => {
      loader.current(q).then((d) => { if (live) { setData(d); setError(null) } }).catch((e: Error) => { if (live) setError(e.message) })
    }, q ? 250 : 0)
    return () => { live = false; window.clearTimeout(t) }
  }, [open, q])

  const toggle = (v: Cell) => onChange(selected.some((s) => s === v) ? selected.filter((s) => s !== v) : [...selected, v])
  const summary = selected.length === 0 ? 'All'
    : selected.length === 1 ? (data?.labels[String(selected[0])] ?? formatCategory(selected[0])) : `${selected.length} selected`
  return (
    <>
      <button ref={ref} className={`filter-chip${selected.length ? ' active' : ''}`} aria-haspopup="dialog" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}>
        <ListFilter size={14} aria-hidden />
        <span className="chip-name">{def.label || humanize(def.column)}</span>
        <span className="chip-value">{summary}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open && (
        <Popover anchor={ref} align="start" onClose={() => setOpen(false)} className="slicer-menu" role="dialog"
          label={`Filter by ${def.label || def.column}`}>
          <label className="menu-search">
            <Search size={14} aria-hidden />
            <input autoFocus value={q} placeholder="Search…" onChange={(e) => setQ(e.target.value)} aria-label="Search values" />
          </label>
          <div className="slicer-values">
            {error && <p className="error small">{error}</p>}
            {!data && !error && <p className="muted small slicer-loading"><LoaderCircle size={14} className="spin" aria-hidden /> Loading…</p>}
            {data?.values.length === 0 && <p className="muted small">Nothing matches.</p>}
            {data?.values.map((v) => {
              const on = selected.some((s) => s === v)
              return (
                <label key={String(v)} className={`slicer-option${on ? ' on' : ''}`}>
                  <input type="checkbox" checked={on} onChange={() => toggle(v)} />
                  <span>{label(v)}</span>
                  {data.labels[String(v)] && <code className="muted">{String(v)}</code>}
                </label>
              )
            })}
            {data?.more && <p className="muted small">Showing the first {data.values.length}. Search to narrow down.</p>}
          </div>
          <div className="slicer-foot">
            <button className="btn text small" disabled={!selected.length} onClick={() => onChange([])}>Clear</button>
            {onRemove && (
              <button className="btn text small danger-text" onClick={() => { setOpen(false); onRemove() }}>
                <Trash2 size={13} aria-hidden /> Remove filter
              </button>
            )}
            <button className="btn primary small" onClick={() => setOpen(false)}>Done</button>
          </div>
        </Popover>
      )}
    </>
  )
}

function AddFilter({ sources, existing, onAdd }: { sources: DataSource[]; existing: FilterDef[]; onAdd: (f: FilterDef) => void }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const ref = useRef<HTMLButtonElement>(null)
  const options = useMemo(() => sources.flatMap((s) => s.tables.filter((t) => !t.error).flatMap((t) => t.columns
    .filter((c) => !c.definition?.hidden && !existing.some((f) => f.sourceId === s.id && f.table === t.name && f.column === c.name))
    .map((c) => ({
      source: s, table: t.name, column: c.name, type: c.type, coded: !!c.definition?.values && Object.keys(c.definition.values).length > 0,
      label: c.definition?.display_name || c.definition?.description || humanize(c.name),
    })))), [sources, existing])
  const needle = q.trim().toLowerCase()
  const shown = options.filter((o) => !needle || `${o.table} ${o.column} ${o.label}`.toLowerCase().includes(needle))
  // Categories first: text and coded columns are what people filter by.
  shown.sort((a, b) => Number(isNumeric(a.type) && !a.coded) - Number(isNumeric(b.type) && !b.coded))
  return (
    <>
      <button ref={ref} className="filter-chip add-filter" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Plus size={14} aria-hidden /> <span>Filter</span>
      </button>
      {open && (
        <Popover anchor={ref} align="start" onClose={() => setOpen(false)} className="slicer-menu" role="dialog" label="Add a filter">
          <div className="menu-label">Add a filter for…</div>
          <label className="menu-search">
            <Filter size={14} aria-hidden />
            <input autoFocus value={q} placeholder="Find a column, e.g. city" onChange={(e) => setQ(e.target.value)} aria-label="Find a column" />
          </label>
          <div className="slicer-values">
            {shown.slice(0, 80).map((o) => (
              <button key={`${o.source.id}.${o.table}.${o.column}`} className="menu-item column-option"
                onClick={() => {
                  setOpen(false)
                  // Two filters both called "City" can't be told apart: name the table too.
                  const clash = existing.some((f) => (f.label || humanize(f.column)).toLowerCase() === o.label.toLowerCase())
                  onAdd({ id: `f-${Date.now().toString(36)}`, sourceId: o.source.id, table: o.table, column: o.column,
                    label: shortLabel(clash ? `${o.label} (${humanize(o.table)})` : o.label), values: [] })
                }}>
                <span className="column-option-main"><strong>{shortLabel(o.label)}</strong>
                  <span className="muted small">{sources.length > 1 ? `${o.source.name} · ` : ''}{o.table}.{o.column}</span></span>
              </button>
            ))}
            {shown.length === 0 && <p className="muted small">No columns match.</p>}
          </div>
        </Popover>
      )}
    </>
  )
}

const isNumeric = (type: string) => /int|real|num|dec|float|double/i.test(type)
const shortLabel = (s: string) => (s.length > 28 ? `${s.slice(0, 27)}…` : s)
