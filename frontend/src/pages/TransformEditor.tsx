import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import {
  ArrowDown, ArrowUp, ChevronDown, Code, Database, LoaderCircle, Pencil, Plus, Save, Sparkles, Trash2, X,
} from 'lucide-react'
import { api, type DataSource, type StepSuggestion, type TransformPreview, type TransformStep } from '../api'
import { StepForm } from '../components/StepForm'
import { formatCategory } from '../lib/format'
import { STEP_DEFS, STEP_GROUPS } from '../lib/steps'

type FormState = { index: number; step: TransformStep; replace: boolean } | null

/** Power Query-style editor: applied steps on the left, live preview, add/edit steps, AI suggestions. */
export function TransformEditor() {
  const { id = '', name: routeName = 'new' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const isNew = routeName === 'new'
  const [source, setSource] = useState<DataSource | null>(null)
  const [name, setName] = useState(isNew ? params.get('name') ?? '' : routeName)
  const [base, setBase] = useState(params.get('base') ?? '')
  const [steps, setSteps] = useState<TransformStep[]>([])
  const [selected, setSelected] = useState(0) // preview after this many steps
  const [preview, setPreview] = useState<TransformPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [form, setForm] = useState<FormState>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [headerMenu, setHeaderMenu] = useState<string | null>(null)
  const [aiText, setAiText] = useState('')
  const [aiBusy, setAiBusy] = useState(false)
  const [suggestion, setSuggestion] = useState<StepSuggestion | null>(null)
  const [dirty, setDirty] = useState(isNew)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [showSql, setShowSql] = useState(false)
  const [saving, setSaving] = useState(false)
  const request = useRef(0)

  // Load the source (tables for merge/append) and, when editing, the saved recipe.
  useEffect(() => {
    api.getSource(id).then(setSource).catch((e: Error) => setNotice({ kind: 'error', text: e.message }))
    if (!isNew) {
      api.listTransforms(id).then((all) => {
        const t = all.find((x) => x.name === routeName)
        if (!t) { setNotice({ kind: 'error', text: `Prepared table ${routeName} not found.` }); return }
        setBase(t.base); setSteps(t.steps); setSelected(t.steps.length); setDirty(false)
      }).catch((e: Error) => setNotice({ kind: 'error', text: e.message }))
    }
  }, [id, isNew, routeName])

  // Live preview, debounced; only the newest response is kept.
  useEffect(() => {
    if (!base) return
    const n = ++request.current
    const t = setTimeout(() => {
      setLoading(true)
      api.previewTransform(id, base, steps, { upto: selected, editing: isNew ? undefined : routeName })
        .then((p) => { if (n === request.current) setPreview(p) })
        .catch((e: Error) => { if (n === request.current) setNotice({ kind: 'error', text: e.message }) })
        .finally(() => { if (n === request.current) setLoading(false) })
    }, 250)
    return () => clearTimeout(t)
  }, [id, base, steps, selected, isNew, routeName])

  const formIndex = form?.index ?? 0
  const loadValues = useCallback((column: string) => api.distinctValues(id, base, steps.slice(0, formIndex), formIndex, column,
    isNew ? undefined : routeName), [id, base, steps, formIndex, isNew, routeName])
  const otherTables = useMemo(() => (source?.tables ?? []).filter((t) => t.name !== base && t.name !== routeName && !t.error), [source, base, routeName])
  const columnsAt = (i: number) => preview?.stepColumns?.[i] ?? preview?.columns ?? []

  const change = (next: TransformStep[], select: number) => { setSteps(next); setSelected(select); setDirty(true); setSuggestion(null) }
  const insert = (step: TransformStep, at: number) => change([...steps.slice(0, at), step, ...steps.slice(at)], at + 1)
  const applyForm = (step: TransformStep) => {
    if (!form) return
    if (form.replace) change(steps.map((s, i) => (i === form.index ? step : s)), form.index + 1)
    else insert(step, form.index)
    setForm(null)
  }
  const openAdd = (op: string, prefill: Partial<TransformStep> = {}) => {
    setAddOpen(false); setHeaderMenu(null)
    setForm({ index: selected, step: { op, ...prefill } as TransformStep, replace: false })
  }
  const quick = (step: TransformStep) => { setHeaderMenu(null); insert(step, selected) }
  const move = (i: number, by: number) => {
    const next = [...steps]
    const [s] = next.splice(i, 1)
    next.splice(i + by, 0, s)
    change(next, Math.max(selected, i + by + 1))
  }

  const suggest = async () => {
    if (!aiText.trim() || !base) return
    setAiBusy(true); setSuggestion(null)
    try {
      setSuggestion(await api.suggestStep(id, base, steps.slice(0, selected), aiText, isNew ? undefined : routeName))
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message })
    } finally {
      setAiBusy(false)
    }
  }

  const save = async () => {
    setSaving(true); setNotice(null)
    try {
      const saved = await api.saveTransform(id, name.trim(), base, steps, isNew ? undefined : routeName)
      setDirty(false)
      setNotice({ kind: 'ok', text: `Saved. "${saved.name}" now works like a table everywhere: dashboards, the assistant and the Definitions page.` })
      if (saved.name !== routeName) navigate(`/sources/${id}/transforms/${saved.name}`, { replace: true })
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  if (!source) return <div className="page"><p className="muted">{notice?.text ?? 'Loading…'}</p></div>

  const labels = preview?.labels ?? []
  const errorStep = preview?.errorStep ?? null

  return (
    <div className="page transform-page">
      <p className="crumb"><Link to="/sources">Data sources</Link> / <Link to={`/sources/${id}/transforms`}>{source.name}: prepared tables</Link></p>
      <div className="page-head">
        <div className="title-block">
          <h1><input className="title-input" value={name} placeholder="table_name" aria-label="Prepared table name"
            onChange={(e) => { setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_')); setDirty(true) }} /></h1>
          <p className="muted small">Lowercase letters, digits and _. Dashboards and the assistant can use it like any table.</p>
        </div>
        <div className="head-actions">
          <button className={`btn ghost ${showSql ? 'active' : ''}`} onClick={() => setShowSql((s) => !s)}><Code size={15} aria-hidden /> SQL</button>
          <button className="btn primary" disabled={!dirty || !name || !base || saving || !!preview?.error} onClick={save}>
            {saving ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Save size={15} aria-hidden />} {dirty ? 'Save' : 'Saved'}
          </button>
        </div>
      </div>
      {notice && (
        <p className={`banner ${notice.kind === 'error' ? 'error' : 'ok-text'}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
          {notice.text}<button className="icon-btn" onClick={() => setNotice(null)} title="Dismiss"><X size={14} /></button>
        </p>
      )}

      <div className="transform-layout">
        <aside className="steps-panel" aria-label="Applied steps">
          <h2>Applied steps</h2>
          <ol className="step-list">
            <li className={`step-item ${selected === 0 ? 'selected' : ''}`}>
              <button className="step-main" onClick={() => setSelected(0)}>
                <Database size={14} aria-hidden />
                <span>Source: <select value={base} onClick={(e) => e.stopPropagation()} aria-label="Source table"
                  onChange={(e) => { setBase(e.target.value); setDirty(true) }}>
                  <option value="" disabled>Choose a table…</option>
                  {source.tables.filter((t) => t.name !== routeName && !t.error).map((t) =>
                    <option key={t.name} value={t.name}>{t.name}{t.prepared ? ' (prepared)' : ''}</option>)}
                </select></span>
              </button>
            </li>
            {steps.map((s, i) => (
              <li key={i} className={`step-item ${selected === i + 1 ? 'selected' : ''} ${errorStep === i ? 'failed' : ''} ${i + 1 > selected ? 'later' : ''}`}>
                <button className="step-main" onClick={() => setSelected(i + 1)} title={labels[i]}>
                  <span className="step-num">{i + 1}</span><span className="step-label">{labels[i] ?? s.op}</span>
                </button>
                <span className="step-actions">
                  <button className="icon-btn" title="Edit step" onClick={() => { setSelected(i); setForm({ index: i, step: s, replace: true }) }}><Pencil size={13} /></button>
                  <button className="icon-btn" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={13} /></button>
                  <button className="icon-btn" title="Move down" disabled={i === steps.length - 1} onClick={() => move(i, 1)}><ArrowDown size={13} /></button>
                  <button className="icon-btn" title="Delete step" onClick={() => change(steps.filter((_, j) => j !== i), Math.min(selected, steps.length - 1))}><Trash2 size={13} /></button>
                </span>
              </li>
            ))}
          </ol>
          {selected < steps.length && <p className="muted small">Showing the table after step {selected}. New steps go here.</p>}
        </aside>

        <section className="transform-main">
          <div className="transform-toolbar">
            <div className="menu-anchor">
              <button className="btn primary small" disabled={!base} onClick={() => setAddOpen((o) => !o)} aria-expanded={addOpen}>
                <Plus size={14} aria-hidden /> Add step <ChevronDown size={13} aria-hidden />
              </button>
              {addOpen && (
                <div className="menu add-menu" role="menu">
                  {STEP_GROUPS.map((g) => (
                    <div key={g}>
                      <div className="menu-label">{g}</div>
                      {STEP_DEFS.filter((d) => d.group === g).map((d) => (
                        <button key={d.op} role="menuitem" className="menu-item" onClick={() => openAdd(d.op)}>{d.label}</button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <form className="ai-step" onSubmit={(e) => { e.preventDefault(); void suggest() }}>
              <Sparkles size={15} aria-hidden />
              <input value={aiText} onChange={(e) => setAiText(e.target.value)} disabled={!base}
                placeholder='Describe a change, e.g. "convert amt from paise to rupees"' aria-label="Describe a change" />
              <button className="btn ghost small" disabled={!aiText.trim() || aiBusy || !base}>
                {aiBusy && <LoaderCircle size={13} className="spin" aria-hidden />} Suggest
              </button>
            </form>
          </div>

          {suggestion && 'question' in suggestion && (
            <div className="proposal"><p>{suggestion.question}</p>
              <div className="proposal-actions"><button className="btn ghost small" onClick={() => setSuggestion(null)}>OK</button></div></div>
          )}
          {suggestion && 'step' in suggestion && (
            <div className="proposal">
              <div className="proposal-title"><Sparkles size={14} aria-hidden /> {suggestion.label}</div>
              <PreviewTable preview={suggestion.preview} maxRows={5} />
              <div className="proposal-actions">
                <button className="btn primary small" onClick={() => { insert(suggestion.step, selected); setAiText('') }}>Apply step</button>
                <button className="btn ghost small" onClick={() => setForm({ index: selected, step: suggestion.step, replace: false })}>Edit first</button>
                <button className="btn ghost small" onClick={() => setSuggestion(null)}>Dismiss</button>
              </div>
            </div>
          )}

          {form && (
            <StepForm key={`${form.index}-${form.step.op}-${form.replace}`} step={form.step} columns={columnsAt(form.index)}
              tables={otherTables} onSave={applyForm} onCancel={() => setForm(null)} loadValues={loadValues} />
          )}

          {showSql && preview?.sql && <pre className="sql-block">{preview.sql}</pre>}

          {!base ? <p className="muted">Choose a source table to start.</p> : preview?.error ? (
            <p className="error banner" role="alert">{errorStep !== null ? `Step ${errorStep + 1}: ` : ''}{preview.error}</p>
          ) : preview && (
            <div className={loading ? 'refetching' : undefined}>
              <p className="muted small">{preview.rows.length}{preview.truncated ? '+' : ''} rows shown · {preview.columns.length} columns · click a column for quick actions</p>
              <PreviewTable preview={preview} onHeader={setHeaderMenu} activeHeader={headerMenu}
                headerMenu={(col) => (
                  <div className="menu header-menu" role="menu">
                    <button className="menu-item" onClick={() => quick({ op: 'sort', column: col, descending: false })}>Sort ascending</button>
                    <button className="menu-item" onClick={() => quick({ op: 'sort', column: col, descending: true })}>Sort descending</button>
                    <button className="menu-item" onClick={() => openAdd('filter_rows', { column: col, operator: 'eq' })}>Filter…</button>
                    <button className="menu-item" onClick={() => openAdd('rename_column', { column: col })}>Rename…</button>
                    <button className="menu-item" onClick={() => openAdd('change_type', { column: col })}>Change type…</button>
                    <button className="menu-item" onClick={() => openAdd('replace_values', { column: col })}>Replace values…</button>
                    <button className="menu-item" onClick={() => openAdd('fill_blanks', { column: col })}>Fill blanks…</button>
                    <button className="menu-item" onClick={() => openAdd('date_part', { column: col, name: `${col}_month`.slice(0, 60) })}>Extract from date…</button>
                    <button className="menu-item" onClick={() => openAdd('split_column', { column: col, delimiter: ',', mode: 'each', parts: 2 })}>Split column…</button>
                    <button className="menu-item" onClick={() => openAdd('conditional_column', { name: `${col}_group`.slice(0, 60), rules: [{ column: col, operator: 'gt', value: '', result: '' }], otherwise: '' })}>Conditional column…</button>
                    <button className="menu-item" onClick={() => openAdd('group_by', { by: [col], aggregations: [{ column: null, func: 'count', name: 'rows' }] })}>Group by this…</button>
                    <button className="menu-item" onClick={() => openAdd('pivot', { column: col, func: 'sum', values: [] })}>Pivot this column…</button>
                    <div className="menu-sep" />
                    <button className="menu-item danger" onClick={() => quick({ op: 'remove_columns', columns: [col] })}>Remove column</button>
                  </div>
                )} />
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function PreviewTable({ preview, maxRows, onHeader, activeHeader, headerMenu }: {
  preview: TransformPreview; maxRows?: number
  onHeader?: (col: string | null) => void; activeHeader?: string | null
  headerMenu?: (col: string) => React.ReactNode
}) {
  const rows = maxRows ? preview.rows.slice(0, maxRows) : preview.rows
  return (
    <div className="table-wrap preview-table">
      <table className="data-table">
        <thead>
          <tr>{preview.columns.map((c, j) => (
            <th key={c} className={typeof preview.rows[0]?.[j] === 'number' ? 'num' : undefined}>
              {onHeader ? (
                <span className="menu-anchor">
                  <button className="th-btn" aria-expanded={activeHeader === c}
                    onClick={() => onHeader(activeHeader === c ? null : c)}>{c} <ChevronDown size={12} aria-hidden /></button>
                  {activeHeader === c && headerMenu?.(c)}
                </span>
              ) : c}
            </th>
          ))}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((v, j) => (
              <td key={j} className={typeof v === 'number' ? 'num' : undefined}>
                {v === null ? <span className="null">blank</span> : typeof v === 'number' ? v.toLocaleString('en-IN') : formatCategory(v)}
              </td>
            ))}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
