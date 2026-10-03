import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
import {
  Check, ChevronRight, CircleAlert, Download, EyeOff, Lightbulb, LoaderCircle, Pencil, Plus, Search, Sparkles, Trash2, Upload, X,
} from 'lucide-react'
import { api, type ColumnDefinition, type ColumnInfo, type DataSource, type GlossaryTerm } from '../api'

const SOURCE_LABEL = { user: 'You', chat: 'From chat', ai_suggested: 'AI guess' } as const

export function DefinitionsPage() {
  const { id = '' } = useParams()
  const [source, setSource] = useState<DataSource | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [context, setContext] = useState('')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  // Tables the user opened/closed; others follow the default (open when something needs attention).
  const [toggled, setToggled] = useState<Record<string, boolean>>({})
  const [query, setQuery] = useState('')
  const importRef = useRef<HTMLInputElement>(null)

  const apply = useCallback((s: DataSource) => {
    setSource(s)
    setContext(s.databaseDescription ?? '')
  }, [])

  useEffect(() => {
    api.getSource(id).then(apply).catch((e: Error) => setNotice({ kind: 'error', text: e.message }))
  }, [id, apply])

  // Every save returns the updated source, so the page never needs a separate reload.
  const run = async (fn: () => Promise<DataSource>, ok?: string) => {
    try {
      apply(await fn())
      if (ok) setNotice({ kind: 'ok', text: ok })
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message })
    }
  }

  if (!source) {
    return <div className="page">{notice ? <p className="error">{notice.text}</p> : <p className="muted">Loading…</p>}</div>
  }

  const flagged = source.tables.flatMap((t) => t.columns.filter((c) => c.flag).map((c) => ({ table: t.name, col: c })))
  const guesses = source.tables.flatMap((t) => t.columns).filter((c) => c.source === 'ai_suggested').length

  const saveColumn = async (table: string, column: string, def: ColumnDefinition | null) => {
    await run(() => api.saveColumnDefinition(id, table, column, def))
    setEditing(null)
  }

  const suggest = async () => {
    setSuggesting(true)
    setNotice(null)
    try {
      const res = await api.suggestDefinitions(id)
      apply(res.source)
      setNotice(res.suggested
        ? { kind: 'ok', text: `The AI suggested ${res.suggested} definition${res.suggested > 1 ? 's' : ''}. Review them below.` }
        : { kind: 'ok', text: 'The AI was not confident about any of these columns. Add them yourself if you know.' })
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message })
    } finally {
      setSuggesting(false)
    }
  }

  const jumpTo = (table: string, column: string) => {
    setToggled((o) => ({ ...o, [table]: true }))
    setEditing(`${table}.${column}`)
    requestAnimationFrame(() =>
      document.getElementById(`col-${table}-${column}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }

  return (
    <div className="page narrow-wide">
      <p className="crumb"><Link to="/sources">Data sources</Link> / {source.name}</p>
      <div className="page-head">
        <h1>Definitions</h1>
        <div className="head-actions">
          <a className="btn ghost small" href={api.definitionsExportUrl(id)} download>
            <Download size={14} aria-hidden /> Export YAML
          </a>
          <button className="btn ghost small" onClick={() => importRef.current?.click()}>
            <Upload size={14} aria-hidden /> Import YAML
          </button>
          <input ref={importRef} type="file" accept=".yaml,.yml" hidden onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void run(() => api.importDefinitions(id, f), 'Definitions imported.')
            e.target.value = ''
          }} />
        </div>
      </div>
      <p className="muted">
        Optional. Explain anything the AI can't work out from names alone, such as cryptic columns, codes or units.
        You can also do this from the dashboard assistant, e.g. <em>"col1 is customer age"</em>.
      </p>

      {notice && (
        <p className={notice.kind === 'error' ? 'error banner' : 'ok-text banner'} role={notice.kind === 'error' ? 'alert' : 'status'}>
          {notice.text}
          <button className="icon-btn" onClick={() => setNotice(null)} title="Dismiss"><X size={14} /></button>
        </p>
      )}
      {source.profileError && (
        <p className="error banner"><CircleAlert size={15} aria-hidden /> Last scan failed: {source.profileError}</p>
      )}

      {flagged.length > 0 && (
        <section className="help-ai">
          <div className="help-ai-head"><Lightbulb size={16} aria-hidden /> Help the AI ({flagged.length})</div>
          <p>These columns have names that are easy to misread. A one-line description makes dashboards more accurate.</p>
          <div className="chips">
            {flagged.map(({ table, col }) => (
              <button key={`${table}.${col.name}`} className="chip" onClick={() => jumpTo(table, col.name)}>
                {table}.{col.name} <span className="muted">· {col.flag}</span>
              </button>
            ))}
          </div>
          <button className="btn ghost small" onClick={suggest} disabled={suggesting}>
            {suggesting ? <LoaderCircle size={14} className="spin" aria-hidden /> : <Sparkles size={14} aria-hidden />}
            {suggesting ? 'Thinking…' : 'Let the AI guess'}
          </button>
          <span className="muted small"> You review every guess before it counts as confirmed.</span>
        </section>
      )}
      {guesses > 0 && flagged.length === 0 && (
        <p className="hint"><Sparkles size={15} aria-hidden /> {guesses} AI guess{guesses > 1 ? 'es' : ''} waiting for review below.</p>
      )}

      <section className="panel">
        <h2>About this database</h2>
        <textarea rows={2} value={context} onChange={(e) => setContext(e.target.value)}
          onBlur={() => context !== (source.databaseDescription ?? '') && run(() => api.saveDatabaseContext(id, context))}
          placeholder="e.g. Retail sales for 2024–2025. Money is stored in paise." />
        {/* Keyed by saved content so the editor resets whenever the server copy changes. */}
        <GlossaryEditor key={JSON.stringify(source.glossary)} terms={source.glossary} onSave={(g) => run(() => api.saveGlossary(id, g), 'Business terms saved.')} />
      </section>

      <div className="defs-toolbar">
        <label className="search-box">
          <Search size={15} aria-hidden />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a column…" aria-label="Find a column" />
          {query && <button className="icon-btn" onClick={() => setQuery('')} aria-label="Clear search"><X size={14} /></button>}
        </label>
        <button className="btn text small" onClick={() => setToggled(Object.fromEntries(source.tables.map((t) => [t.name, true])))}>Expand all</button>
        <button className="btn text small" onClick={() => setToggled(Object.fromEntries(source.tables.map((t) => [t.name, false])))}>Collapse all</button>
      </div>

      {source.tables.map((t) => {
        const q = query.trim().toLowerCase()
        const needs = t.columns.filter((c) => c.flag || c.source === 'ai_suggested')
        const explained = t.columns.filter((c) => c.definition?.description).length
        const shown = q ? t.columns.filter((c) => c.name.toLowerCase().includes(q) || (c.definition?.description ?? '').toLowerCase().includes(q)) : t.columns
        if (q && !shown.length) return null
        // Columns that need attention first, then the rest in table order.
        const ordered = [...shown].sort((a, b) => Number(!!(b.flag || b.source === 'ai_suggested')) - Number(!!(a.flag || a.source === 'ai_suggested')))
        const open = q ? true : toggled[t.name] ?? (needs.length > 0 || source.tables.length <= 2)
        return (
        <section key={t.name} className={`panel table-defs ${open ? 'open' : 'closed'}`}>
          <div className="table-defs-head">
            <button className="table-toggle" aria-expanded={open} onClick={() => setToggled((o) => ({ ...o, [t.name]: !open }))}>
              <ChevronRight size={16} className="chev" aria-hidden />
              <h2><code>{t.name}</code></h2>
            </button>
            {t.isView && <span className="badge">view</span>}
            {t.prepared && <Link to={`/sources/${id}/transforms/${t.name}`} className="badge src-ai_suggested">prepared table</Link>}
            <span className="table-defs-meta">
              {needs.length > 0 && <span className="badge warn">{needs.length} to explain</span>}
              <span className="muted small">{explained} of {t.columns.length} explained</span>
              {t.rowCount !== null && <span className="muted small">· {t.rowCount.toLocaleString('en-IN')} rows</span>}
            </span>
          </div>
          {open && <>
          <TableDescription key={t.description ?? ''} value={t.description ?? ''}
            onSave={(d) => run(() => api.saveTableDescription(id, t.name, d))} />
          {t.error && <p className="error small"><CircleAlert size={13} aria-hidden /> Couldn't read this table: {t.error}</p>}
          <div className="col-list">
            {ordered.map((c) => {
              const key = `${t.name}.${c.name}`
              return editing === key ? (
                <ColumnEditor key={key} table={t.name} col={c} onCancel={() => setEditing(null)}
                  onSave={(def) => saveColumn(t.name, c.name, def)} />
              ) : (
                <ColumnRow key={key} table={t.name} col={c} onEdit={() => setEditing(key)}
                  onAccept={() => saveColumn(t.name, c.name, c.definition)}
                  onReject={() => saveColumn(t.name, c.name, null)} />
              )
            })}
          </div>
          </>}
        </section>
        )
      })}
    </div>
  )
}

function TableDescription({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [text, setText] = useState(value)
  return (
    <input className="table-desc" value={text} placeholder="What is this table? (optional)"
      onChange={(e) => setText(e.target.value)} onBlur={() => text !== value && onSave(text)} />
  )
}

function GlossaryEditor({ terms, onSave }: { terms: GlossaryTerm[]; onSave: (g: GlossaryTerm[]) => void }) {
  const [rows, setRows] = useState(terms)
  const dirty = JSON.stringify(rows) !== JSON.stringify(terms)
  const set = (i: number, patch: Partial<GlossaryTerm>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  return (
    <>
      <h3>Business terms</h3>
      <p className="muted small">How your team defines metrics, e.g. what counts as "revenue" or an "active customer".</p>
      <div className="glossary-edit">
        {rows.map((g, i) => (
          <div key={i} className="glossary-row">
            <input aria-label="Term" value={g.term} placeholder="Term" onChange={(e) => set(i, { term: e.target.value })} />
            <textarea rows={1} className="grow" aria-label="Definition" value={g.definition} placeholder="Definition"
              onChange={(e) => set(i, { definition: e.target.value })} />
            <button className="icon-btn" title="Remove term" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button className="btn ghost small" onClick={() => setRows([...rows, { term: '', definition: '' }])}>
          <Plus size={14} aria-hidden /> Add term
        </button>
        {dirty && <button className="btn primary small" onClick={() => onSave(rows)}>Save terms</button>}
      </div>
    </>
  )
}

function ColumnRow({ table, col, onEdit, onAccept, onReject }: {
  table: string; col: ColumnInfo; onEdit: () => void; onAccept: () => void; onReject: () => void
}) {
  const d = col.definition
  const guess = col.source === 'ai_suggested'
  return (
    <div id={`col-${table}-${col.name}`} className={`col-row ${col.flag ? 'flagged' : ''} ${guess ? 'guess' : ''}`}>
      <div className="col-name">
        <code>{col.name}</code>
        <span className="col-type">{col.type.toLowerCase()}</span>
      </div>
      <div className="col-meaning">
        {d?.hidden && <span className="badge"><EyeOff size={12} aria-hidden /> Hidden from AI</span>}
        {d?.description ? (
          <span>{d.description}{d.unit && <span className="muted"> · {d.unit}</span>}</span>
        ) : (
          <span className="muted">e.g. {col.samples.slice(0, 4).join(', ') || '–'}</span>
        )}
        {d?.values && (
          <div className="pills">
            {Object.entries(d.values).map(([k, v]) => <span key={k} className="code-pill">{k} = {v}</span>)}
          </div>
        )}
      </div>
      <div className="col-actions">
        {col.source && <span className={`badge src-${col.source}`}>
          {guess && <Sparkles size={12} aria-hidden />} {SOURCE_LABEL[col.source]}
        </span>}
        {guess && (
          <>
            <button className="btn ghost small" onClick={onAccept}><Check size={14} aria-hidden /> Accept</button>
            <button className="icon-btn" onClick={onReject} title="Reject this guess"><X size={14} /></button>
          </>
        )}
        <button className="icon-btn" onClick={onEdit} title={`Edit ${col.name}`}><Pencil size={14} /></button>
      </div>
    </div>
  )
}

function ColumnEditor({ table, col, onSave, onCancel }: {
  table: string; col: ColumnInfo; onSave: (d: ColumnDefinition | null) => void; onCancel: () => void
}) {
  const d = col.definition ?? {}
  const [description, setDescription] = useState(d.description ?? '')
  const [unit, setUnit] = useState(d.unit ?? '')
  const [values, setValues] = useState(Object.entries(d.values ?? {}).map(([k, v]) => `${k} = ${v}`).join('\n'))
  const [hidden, setHidden] = useState(d.hidden ?? false)

  const submit = () => {
    const parsed: Record<string, string> = {}
    for (const line of values.split('\n')) {
      const [k, ...rest] = line.split('=')
      if (k?.trim() && rest.length) parsed[k.trim()] = rest.join('=').trim()
    }
    onSave({
      description: description.trim() || undefined,
      unit: unit.trim() || undefined,
      values: Object.keys(parsed).length ? parsed : undefined,
      hidden: hidden || undefined,
    })
  }

  return (
    <div className="col-editor" id={`col-${table}-${col.name}`}>
      <div className="col-name"><code>{col.name}</code><span className="col-type">{col.type.toLowerCase()}</span></div>
      <p className="muted small">Sample values: {col.samples.join(', ') || '–'}</p>
      <div className="form-grid">
        <label className="field wide"><span>What does it mean?</span>
          <textarea autoFocus rows={1} className="grow" value={description} onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }} placeholder="e.g. Customer age" /></label>
        <label className="field"><span>Unit <em>(optional)</em></span>
          <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="years, INR, paise…" /></label>
      </div>
      <label className="field"><span>Code meanings <em>(optional, one per line)</em></span>
        <textarea rows={3} value={values} onChange={(e) => setValues(e.target.value)} placeholder={'1 = Completed\n9 = Refunded'} /></label>
      <label className="checkbox">
        <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} />
        Hide from the AI (for example, personal or sensitive data)
      </label>
      <div className="form-actions">
        <button className="btn primary small" onClick={submit}>Save</button>
        <button className="btn ghost small" onClick={onCancel}>Cancel</button>
        {col.definition && <button className="btn ghost small" onClick={() => onSave(null)}>Clear definition</button>}
      </div>
    </div>
  )
}
