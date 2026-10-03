import { useState, type FormEvent } from 'react'
import { ArrowRight, Calculator, Check, Link2, LoaderCircle, Pencil, Plus, Sparkles, Unlink, X } from 'lucide-react'
import { api, type Cell, type DataSource, type Measure } from '../api'
import { formatValue } from '../lib/format'
import { usePlan } from '../lib/plan'
import { ConfirmDelete } from './ConfirmDelete'
import { UpgradeNote } from './Upgrade'

const FORMAT_LABEL = { number: 'Number', currency: 'Money', percent: 'Percent' } as const
const ORIGIN_LABEL = { database: 'From the database', inferred: 'Detected from names', user: 'Added by you' } as const

const show = (m: Measure, v: Cell) =>
  typeof v === 'number' ? formatValue(v, { format: m.format, currency: m.currency ?? undefined }) : String(v ?? '–')

/** Measures: calculations defined once and used by every chart, the assistant and the analyst. */
export function MeasuresPanel({ source, onChange }: { source: DataSource; onChange: (s: DataSource) => void }) {
  const locked = !usePlan().has('measures')
  const [form, setForm] = useState<{ measure: Measure; original?: string } | null>(null)
  const [drafts, setDrafts] = useState<{ measure: Measure; value: Cell }[] | null>(null)
  const [busy, setBusy] = useState<'suggest' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const firstTable = source.tables.find((t) => !t.error)?.name ?? ''

  const suggest = async () => {
    setBusy('suggest')
    setError(null)
    try {
      const res = await api.suggestMeasures(source.id)
      setDrafts(res.measures)
      if (!res.measures.length) setError('The AI didn’t find measures it could check against the data. Add one yourself.')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const accept = async (m: Measure) => {
    try {
      onChange(await api.saveMeasure(source.id, { ...m, source: 'ai_suggested' }))
      setDrafts((d) => d?.filter((x) => x.measure.name !== m.name) ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <section className="panel model-panel" id="measures">
      <div className="model-head">
        <div>
          <h2><Calculator size={17} aria-hidden /> Measures</h2>
          <p className="muted small">Calculations defined once, like “Revenue” or “Refund rate”. Every chart, the assistant and the
            analyst use the same formula, so the numbers always agree. In SQL: <code>MEASURE(revenue)</code>.</p>
        </div>
        {!locked && (
          <div className="head-actions">
            <button className="btn ghost small" onClick={suggest} disabled={busy !== null}>
              {busy === 'suggest' ? <LoaderCircle size={14} className="spin" aria-hidden /> : <Sparkles size={14} aria-hidden />} Suggest with AI
            </button>
            <button className="btn primary small" onClick={() => setForm({
              measure: { name: '', label: '', table: firstTable, expression: '', format: 'number', up_is_good: true },
            })}><Plus size={14} aria-hidden /> Add measure</button>
          </div>
        )}
      </div>
      {locked && <UpgradeNote>Measures are part of Stardeck Enterprise.{source.measures.length > 0 && ' The ones below keep working in your charts.'}</UpgradeNote>}
      {error && <p className="error small" role="alert">{error}</p>}

      {drafts && drafts.length > 0 && (
        <div className="draft-list">
          <p className="small muted">Suggestions, each checked against your data. Add the ones that are right.</p>
          {drafts.map(({ measure: m, value }) => (
            <div key={m.name} className="measure-row draft">
              <div className="measure-main">
                <strong>{m.label || m.name}</strong> <span className="muted small">= {show(m, value)} overall</span>
                {m.description && <p className="small muted">{m.description}</p>}
                <code className="formula">{m.expression}</code> <span className="badge">{m.table}</span>
              </div>
              <div className="measure-actions">
                <button className="btn ghost small" onClick={() => setForm({ measure: m })}><Pencil size={13} aria-hidden /> Edit</button>
                <button className="btn primary small" onClick={() => void accept(m)}><Check size={13} aria-hidden /> Add</button>
                <button className="icon-btn" aria-label={`Skip ${m.label || m.name}`}
                  onClick={() => setDrafts((d) => d?.filter((x) => x.measure.name !== m.name) ?? null)}><X size={14} /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      {form && <MeasureForm source={source} initial={form.measure} original={form.original} onCancel={() => setForm(null)}
        onSaved={(s) => { onChange(s); setForm(null); setDrafts((d) => d?.filter((x) => x.measure.name !== form.measure.name) ?? null) }} />}

      {source.measures.length === 0 && !form && !locked && (
        <p className="muted small model-empty">No measures yet. Add the numbers your business tracks, or let the AI suggest them from your glossary.</p>
      )}
      <div className="measure-list">
        {source.measures.map((m) => (
          <div key={m.name} className="measure-row">
            <div className="measure-main">
              <strong>{m.label || m.name}</strong> <code className="muted small">{m.name}</code>
              <span className="badge">{FORMAT_LABEL[m.format]}{m.format === 'currency' && m.currency ? ` · ${m.currency}` : ''}</span>
              {m.up_is_good === false && <span className="badge">Lower is better</span>}
              {m.source === 'ai_suggested' && <span className="badge accent">AI suggested</span>}
              {m.description && <p className="small muted">{m.description}</p>}
              <code className="formula">{m.expression}</code> <span className="badge">{m.table}</span>
            </div>
            <div className="measure-actions">
              {!locked && <button className="icon-btn" title="Edit measure" aria-label={`Edit ${m.label || m.name}`}
                onClick={() => setForm({ measure: m, original: m.name })}><Pencil size={14} /></button>}
              <ConfirmDelete label={`Delete “${m.label || m.name}”?`} detail="Charts that use it will show an error until they're changed."
                title="Delete measure" onConfirm={async () => {
                  try { onChange(await api.deleteMeasure(source.id, m.name)) } catch (e) { setError((e as Error).message) }
                }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function MeasureForm({ source, initial, original, onCancel, onSaved }: {
  source: DataSource; initial: Measure; original?: string; onCancel: () => void; onSaved: (s: DataSource) => void
}) {
  const [m, setM] = useState<Measure>({ ...initial, currency: initial.currency ?? (initial.format === 'currency' ? 'INR' : null) })
  const [value, setValue] = useState<{ v: Cell; ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState<'check' | 'save' | null>(null)
  const set = (patch: Partial<Measure>) => { setM({ ...m, ...patch }); setValue(null) }
  const tables = source.tables.filter((t) => !t.error)
  const columns = tables.find((t) => t.name === m.table)?.columns ?? []

  const check = async () => {
    setBusy('check')
    try {
      const res = await api.checkMeasure(source.id, m)
      setValue({ v: res.value, ok: true, text: `${show(m, res.value)} across all the data` })
    } catch (e) {
      setValue({ v: null, ok: false, text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }
  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy('save')
    try {
      onSaved(await api.saveMeasure(source.id, m, original))
    } catch (err) {
      setValue({ v: null, ok: false, text: (err as Error).message })
    } finally {
      setBusy(null)
    }
  }

  return (
    <form className="measure-form" onSubmit={save}>
      <div className="two-col">
        <label className="field"><span>Name people see</span>
          <input required value={m.label ?? ''} placeholder="e.g. Revenue" onChange={(e) => {
            const label = e.target.value
            set({ label, ...(!original && (!m.name || m.name === slug(m.label ?? '')) ? { name: slug(label) } : {}) })
          }} /></label>
        <label className="field"><span>Short id <em>(used in SQL)</em></span>
          <input required value={m.name} pattern="[a-z][a-z0-9_]*" spellCheck={false} placeholder="revenue"
            onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} /></label>
      </div>
      <label className="field"><span>What it means <em>(optional)</em></span>
        <input value={m.description ?? ''} placeholder="e.g. Money from completed orders, in INR"
          onChange={(e) => set({ description: e.target.value })} /></label>
      <div className="two-col">
        <label className="field"><span>Adds up rows of</span>
          <select value={m.table} onChange={(e) => set({ table: e.target.value })}>
            {tables.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
          </select></label>
        <label className="field"><span>Show as</span>
          <select value={m.format} onChange={(e) => set({ format: e.target.value as Measure['format'], currency: e.target.value === 'currency' ? (m.currency || 'INR') : null })}>
            <option value="number">Number</option><option value="currency">Money</option><option value="percent">Percent</option>
          </select></label>
      </div>
      <label className="field"><span>Formula</span>
        <textarea required rows={2} className="mono" value={m.expression} spellCheck={false}
          placeholder="e.g. SUM(CASE WHEN sts = 1 THEN amt END) / 100.0" onChange={(e) => set({ expression: e.target.value })} />
        <small className="muted">An aggregate over this table’s columns ({columns.slice(0, 8).map((c) => c.name).join(', ')}{columns.length > 8 ? '…' : ''}).
          For a condition, use <code>CASE WHEN … THEN … END</code> inside it.</small>
      </label>
      <div className="measure-form-row">
        {m.format === 'currency' && (
          <label className="field currency-field"><span>Currency</span>
            <input value={m.currency ?? ''} maxLength={3} onChange={(e) => set({ currency: e.target.value.toUpperCase() })} /></label>
        )}
        <label className="checkbox"><input type="checkbox" checked={m.up_is_good !== false}
          onChange={(e) => set({ up_is_good: e.target.checked })} /> Higher is better</label>
      </div>
      {value && <p className={`test-result ${value.ok ? 'ok' : 'bad'}`} role="status"><span>{value.text}</span></p>}
      <div className="form-actions">
        <button type="button" className="btn ghost small" onClick={check} disabled={busy !== null || !m.expression || !m.name}>
          {busy === 'check' && <LoaderCircle size={13} className="spin" aria-hidden />} Check
        </button>
        <button type="submit" className="btn primary small" disabled={busy !== null || !m.expression || !m.name}>
          {busy === 'save' && <LoaderCircle size={13} className="spin" aria-hidden />} Save measure
        </button>
        <button type="button" className="btn text small" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

const slug = (s: string) => s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').replace(/^(\d)/, 'm_$1').slice(0, 40)

/** How tables link: filters and security rules travel along these. */
export function RelationshipsPanel({ source, onChange }: { source: DataSource; onChange: (s: DataSource) => void }) {
  const locked = !usePlan().has('relationships')
  const [adding, setAdding] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [error, setError] = useState<string | null>(null)
  const cols = source.tables.filter((t) => !t.error && !t.prepared).flatMap((t) => t.columns.map((c) => `${t.name}.${c.name}`))

  const run = async (fn: () => Promise<DataSource>) => {
    setError(null)
    try { onChange(await fn()) } catch (e) { setError((e as Error).message) }
  }
  const add = async (e: FormEvent) => {
    e.preventDefault()
    await run(() => api.addRelationship(source.id, from, to))
    setAdding(false)
    setFrom('')
    setTo('')
  }

  return (
    <section className="panel model-panel" id="relationships">
      <div className="model-head">
        <div>
          <h2><Link2 size={17} aria-hidden /> How tables connect</h2>
          <p className="muted small">Filters and data access rules follow these links: filtering customers to Pune also filters
            their orders. Most are found automatically.</p>
        </div>
        {!locked && <button className="btn ghost small" onClick={() => setAdding((a) => !a)}><Plus size={14} aria-hidden /> Add a link</button>}
      </div>
      {locked && <UpgradeNote>Adding or turning off links is part of Stardeck Enterprise. The links found automatically still work.</UpgradeNote>}
      {error && <p className="error small" role="alert">{error}</p>}
      {adding && (
        <form className="rel-form" onSubmit={add}>
          <select aria-label="From column" required value={from} onChange={(e) => setFrom(e.target.value)}>
            <option value="" disabled>Column that points… (e.g. orders.customer_id)</option>
            {cols.map((c) => <option key={c}>{c}</option>)}
          </select>
          <ArrowRight size={15} aria-hidden />
          <select aria-label="To column" required value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="" disabled>…to the column it matches (e.g. customers.id)</option>
            {cols.map((c) => <option key={c}>{c}</option>)}
          </select>
          <button className="btn primary small" disabled={!from || !to}>Add</button>
        </form>
      )}
      {source.relationships.length === 0 && <p className="muted small model-empty">No links found yet.</p>}
      <ul className="rel-list">
        {source.relationships.map((r) => (
          <li key={r.key} className={`rel-row${r.ignored ? ' ignored' : ''}`}>
            <code>{r.table}.{r.column}</code> <ArrowRight size={13} aria-hidden /> <code>{r.refTable}.{r.refColumn}</code>
            <span className={`badge${r.origin === 'user' ? ' accent' : ''}`}>{ORIGIN_LABEL[r.origin]}</span>
            {r.ignored && <span className="badge">Not used</span>}
            {!locked && <span className="rel-actions">
              {r.origin === 'user' ? (
                <button className="btn text small" onClick={() => void run(() => api.toggleRelationship(source.id, r.key, true))}>
                  <X size={13} aria-hidden /> Remove</button>
              ) : r.ignored ? (
                <button className="btn text small" onClick={() => void run(() => api.toggleRelationship(source.id, r.key, false))}>
                  <Link2 size={13} aria-hidden /> Use it</button>
              ) : (
                <button className="btn text small" onClick={() => void run(() => api.toggleRelationship(source.id, r.key, true))}
                  title="Stop filters and rules from following this link"><Unlink size={13} aria-hidden /> Don’t use</button>
              )}
            </span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
