import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import type { TableInfo, TransformStep } from '../api/types'
import {
  cleanStep, formatMapping, missingFields, OPERATOR_OPTIONS, operatorNeedsValue, parseMapping, stepDef, type FieldDef, type Rule,
} from '../lib/steps'

interface Props {
  step: TransformStep
  /** Columns available at this point in the recipe. */
  columns: string[]
  /** Other tables (for merge/append), with their columns. */
  tables: TableInfo[]
  onSave: (step: TransformStep) => void
  onCancel: () => void
  /** Distinct values of a column at this point in the recipe (for pivot). */
  loadValues?: (column: string) => Promise<{ values: string[]; more: boolean }>
}

/** A form for any step type, driven by the catalogue in lib/steps.ts. */
export function StepForm({ step: initial, columns, tables, onSave, onCancel, loadValues }: Props) {
  const def = stepDef(initial.op)!
  const [step, setStep] = useState<TransformStep>({ ...def.defaults, ...initial })
  const [mappingText, setMappingText] = useState(formatMapping(initial.mapping))
  const set = (key: string, value: unknown) => setStep((s) => ({ ...s, [key]: value }))
  const other = tables.find((t) => t.name === step.table)
  const otherColumns = other?.columns.map((c) => c.name) ?? []
  const final = cleanStep({ ...step, ...(def.fields.some((f) => f.kind === 'mapping') ? { mapping: parseMapping(mappingText) } : {}) })
  const missing = missingFields(final)
  // Pivot: offer the column's actual values as choices.
  const [pivotChoices, setPivotChoices] = useState<{ column: string; values: string[]; more: boolean } | null>(null)
  const pivotColumn = step.op === 'pivot' ? String(step.column ?? '') : ''
  useEffect(() => {
    if (!pivotColumn || !loadValues) return
    let live = true
    loadValues(pivotColumn).then((r) => { if (live) setPivotChoices({ column: pivotColumn, ...r }) })
      .catch(() => { if (live) setPivotChoices({ column: pivotColumn, values: [], more: false }) })
    return () => { live = false }
  }, [pivotColumn, loadValues])

  const field = (f: FieldDef) => {
    if (f.showIf && !f.showIf(step)) return null
    const id = `step-${f.key}`
    const value = step[f.key]
    switch (f.kind) {
      case 'column': case 'tableColumn': {
        const opts = f.kind === 'column' ? columns : otherColumns
        return (
          <select id={id} value={String(value ?? '')} onChange={(e) => set(f.key, e.target.value)}>
            <option value="" disabled>Choose…</option>
            {opts.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )
      }
      case 'columns': case 'tableColumns': {
        const opts = f.kind === 'columns' ? columns : otherColumns
        const picked = new Set((value as string[]) ?? [])
        return (
          <div className="col-picker" role="group" aria-labelledby={`${id}-label`}>
            {opts.map((c) => (
              <label key={c} className={`col-chip ${picked.has(c) ? 'on' : ''}`}>
                <input type="checkbox" checked={picked.has(c)} onChange={() => {
                  const next = new Set(picked)
                  if (next.has(c)) next.delete(c); else next.add(c)
                  set(f.key, opts.filter((o) => next.has(o)))
                }} />{c}
              </label>
            ))}
            {!opts.length && <span className="muted small">{f.kind === 'tableColumns' ? 'Pick a table first' : 'No columns'}</span>}
          </div>
        )
      }
      case 'table':
        return (
          <select id={id} value={String(value ?? '')} onChange={(e) => setStep((s) => ({ ...s, table: e.target.value, right_on: '', columns: [] }))}>
            <option value="" disabled>Choose…</option>
            {tables.map((t) => <option key={t.name} value={t.name}>{t.name}{t.prepared ? ' (prepared)' : ''}</option>)}
          </select>
        )
      case 'choice':
        return (
          <select id={id} value={String(value ?? '')} onChange={(e) => set(f.key, e.target.value)}>
            {f.options!.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        )
      case 'bool':
        return <label className="checkbox"><input id={id} type="checkbox" checked={!!value} onChange={(e) => set(f.key, e.target.checked)} /> Yes</label>
      case 'number':
        return <input id={id} type="number" min={1} value={String(value ?? '')} onChange={(e) => set(f.key, e.target.value)} />
      case 'mapping':
        return <textarea id={id} rows={4} value={mappingText} placeholder={f.placeholder} onChange={(e) => setMappingText(e.target.value)} />
      case 'expression':
        return <input id={id} className="mono" value={String(value ?? '')} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />
      case 'names':
        return <input id={id} value={Array.isArray(value) ? value.join(', ') : String(value ?? '')} placeholder={f.placeholder}
          onChange={(e) => set(f.key, e.target.value)} />
      case 'pivotValues': {
        if (!pivotColumn) return <span className="muted small">Pick the column first</span>
        if (!pivotChoices || pivotChoices.column !== pivotColumn) return <span className="muted small">Loading values…</span>
        const picked = new Set((value as string[]) ?? [])
        return (
          <div className="col-picker">
            {pivotChoices.values.map((v) => (
              <label key={v} className={`col-chip ${picked.has(v) ? 'on' : ''}`}>
                <input type="checkbox" checked={picked.has(v)} onChange={() => {
                  const next = new Set(picked)
                  if (next.has(v)) next.delete(v); else next.add(v)
                  set(f.key, pivotChoices.values.filter((x) => next.has(x)))
                }} />{v}
              </label>
            ))}
            {pivotChoices.more && <span className="muted small">More than 50 values: pick the ones you need.</span>}
          </div>
        )
      }
      case 'rules': {
        const rules = (value as Rule[]) ?? []
        const update = (i: number, patch: Partial<Rule>) => set(f.key, rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
        return (
          <div className="rule-list">
            {rules.map((r, i) => (
              <div key={i} className="rule-row">
                <span className="rule-kw">{i === 0 ? 'If' : 'Else if'}</span>
                <select aria-label="Column" value={r.column} onChange={(e) => update(i, { column: e.target.value })}>
                  <option value="" disabled>column…</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <select aria-label="Operator" value={r.operator} onChange={(e) => update(i, { operator: e.target.value })}>
                  {OPERATOR_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                {operatorNeedsValue(r.operator) && (
                  <input aria-label="Value" className="rule-value" value={r.value ?? ''} placeholder="value"
                    onChange={(e) => update(i, { value: e.target.value })} />
                )}
                {r.operator === 'between' && (
                  <input aria-label="And" className="rule-value" value={r.value2 ?? ''} placeholder="and"
                    onChange={(e) => update(i, { value2: e.target.value })} />
                )}
                <span className="rule-kw">then</span>
                {r.result_column !== undefined ? (
                  <select aria-label="Result column" value={r.result_column} onChange={(e) => update(i, { result_column: e.target.value })}>
                    <option value="" disabled>column…</option>
                    {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : (
                  <input aria-label="Result" className="rule-value" value={r.result ?? ''} placeholder="result"
                    onChange={(e) => update(i, { result: e.target.value })} />
                )}
                <button className="btn ghost small" title={r.result_column !== undefined ? 'Use a fixed value' : "Use another column's value"}
                  onClick={() => update(i, r.result_column !== undefined ? { result_column: undefined, result: '' } : { result_column: '', result: undefined })}>
                  {r.result_column !== undefined ? 'value' : 'column'}
                </button>
                <button className="icon-btn" title="Remove rule" disabled={rules.length === 1}
                  onClick={() => set(f.key, rules.filter((_, j) => j !== i))}><X size={14} /></button>
              </div>
            ))}
            <button className="btn ghost small" onClick={() => set(f.key, [...rules, { column: rules[rules.length - 1]?.column ?? '', operator: 'eq', value: '', result: '' }])}>
              <Plus size={13} aria-hidden /> Add rule
            </button>
          </div>
        )
      }
      case 'aggregations': {
        const rows = (value as { column: string | null; func: string; name: string }[]) ?? []
        const update = (i: number, patch: object) => set(f.key, rows.map((r, j) => (j === i ? { ...r, ...patch } : r)))
        return (
          <div className="agg-list">
            {rows.map((r, i) => (
              <div key={i} className="agg-row">
                <select aria-label="Summary" value={r.func} onChange={(e) => update(i, { func: e.target.value, column: e.target.value === 'count' ? r.column : r.column ?? columns[0] })}>
                  {['sum', 'avg', 'count', 'count_distinct', 'min', 'max'].map((fn) => (
                    <option key={fn} value={fn}>{{ sum: 'Sum', avg: 'Average', count: 'Count', count_distinct: 'Count distinct', min: 'Minimum', max: 'Maximum' }[fn]}</option>
                  ))}
                </select>
                <span className="muted">of</span>
                <select aria-label="Column" value={r.column ?? ''} onChange={(e) => update(i, { column: e.target.value || null })}>
                  {r.func === 'count' && <option value="">rows</option>}
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <span className="muted">as</span>
                <input aria-label="Name" value={r.name} onChange={(e) => update(i, { name: e.target.value })} />
                <button className="icon-btn" title="Remove" onClick={() => set(f.key, rows.filter((_, j) => j !== i))}><X size={14} /></button>
              </div>
            ))}
            <button className="btn ghost small" onClick={() => set(f.key, [...rows, { column: columns[0] ?? null, func: 'sum', name: `total_${rows.length + 1}` }])}>
              <Plus size={13} aria-hidden /> Add summary
            </button>
          </div>
        )
      }
      default:
        return <input id={id} value={String(value ?? '')} placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />
    }
  }

  return (
    <div className="step-form">
      <h3>{def.label}</h3>
      <div className="step-fields">
        {def.fields.map((f) => (!f.showIf || f.showIf(step)) && (
          <div key={f.key} className={`field ${['columns', 'tableColumns', 'aggregations', 'mapping', 'expression', 'rules', 'pivotValues'].includes(f.kind) ? 'wide' : ''}`}>
            <span id={`step-${f.key}-label`}>{f.label}{f.optional && <em> (optional)</em>}</span>
            {field(f)}
            {f.help && <span className="muted small">{f.help}</span>}
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button className="btn primary small" disabled={missing.length > 0} title={missing.length ? `Fill in: ${missing.join(', ')}` : undefined}
          onClick={() => onSave(final)}>Apply step</button>
        <button className="btn ghost small" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}
