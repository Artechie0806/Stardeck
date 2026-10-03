import { ArrowDown, ArrowUp } from 'lucide-react'
import type { ChartSpec, DatePreset, QueryResult } from '../api/types'
import { compareLabel } from '../lib/dates'
import { formatValue } from '../lib/format'

export function KpiTile({ spec, result, preset, dateFiltered = true }: {
  spec: ChartSpec; result: QueryResult; preset: DatePreset; dateFiltered?: boolean
}) {
  // Dashboard queries return [value, previous]; a freshly generated preview may return just [value].
  const [value, previous] = (result.rows[0] ?? [null, null]) as [number | null, number | null | undefined]
  const hasDelta = typeof value === 'number' && typeof previous === 'number' && previous !== 0
  const change = hasDelta ? ((value - previous) / Math.abs(previous)) * 100 : 0
  const up = change >= 0
  const good = up === (spec.upIsGood ?? true)
  // Widgets that ignore the date filter (e.g. year-on-year) have no previous period to compare with.
  const label = dateFiltered ? compareLabel(preset) : null

  return (
    <div className="kpi">
      <div className="kpi-value" title={formatValue(value, spec)}>{formatValue(value, spec, true)}</div>
      {hasDelta ? (
        <div className={`kpi-delta ${good ? 'good' : 'bad'}`}>
          {up ? <ArrowUp size={14} aria-hidden /> : <ArrowDown size={14} aria-hidden />}
          <span>{Math.abs(change).toFixed(1)}%</span>
          {label && <span className="kpi-period">{label}</span>}
        </div>
      ) : (
        <div className="kpi-period">{preset === 'all' || !dateFiltered ? 'All dates' : label ? `No data ${label.replace('vs ', 'for ')}` : ''}</div>
      )}
    </div>
  )
}
