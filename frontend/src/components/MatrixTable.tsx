import type { ChartSpec, QueryResult } from '../api/types'
import { deriveRoles, naturalOrder } from '../lib/chartFit'
import { inkFor, rampColor } from '../lib/chartOptions'
import { formatCategory, formatValue, humanize } from '../lib/format'
import { readChartColors } from '../lib/themeContext'

// Averages, rates and percentages can't be added up, so they get no totals row/column.
const NON_ADDITIVE = /avg|average|mean|median|rate|ratio|pct|percent|share|margin|aov/i

/** Pivot table (Power BI "Matrix"): rows x columns, heat-shaded cells, totals when they make sense. */
export function MatrixTable({ spec, result }: { spec: ChartSpec; result: QueryResult }) {
  const roles = deriveRoles(result, spec)
  const xi = result.columns.indexOf(roles.x ?? '')
  const si = result.columns.indexOf(roles.series ?? '')
  const yi = result.columns.indexOf(roles.measures[0] ?? '')
  const rowKeys = naturalOrder([...new Set(result.rows.map((r) => formatCategory(r[xi])))])
  const colKeys = naturalOrder([...new Set(result.rows.map((r) => formatCategory(r[si])))].sort())
  const cell = new Map<string, number>()
  for (const r of result.rows) {
    const v = r[yi]
    if (typeof v === 'number') cell.set(`${formatCategory(r[xi])}\u0000${formatCategory(r[si])}`, v)
  }
  const values = [...cell.values()]
  const lo = Math.min(...values, 0), hi = Math.max(...values, 1)
  const additive = spec.format !== 'percent' && !NON_ADDITIVE.test(roles.measures[0] ?? '')
  const ramp = readChartColors().seq
  const fmt = (v: number | undefined) => (v === undefined ? '–' : formatValue(v, spec, true))
  const rowTotal = (rk: string) => colKeys.reduce((a, ck) => a + (cell.get(`${rk}\u0000${ck}`) ?? 0), 0)
  const colTotal = (ck: string) => rowKeys.reduce((a, rk) => a + (cell.get(`${rk}\u0000${ck}`) ?? 0), 0)

  return (
    <div className="table-wrap">
      <table className="data-table matrix">
        <thead>
          <tr>
            <th>{humanize(roles.x ?? '')} / {humanize(roles.series ?? '')}</th>
            {colKeys.map((ck) => <th key={ck} className="num">{ck}</th>)}
            {additive && <th className="num total">Total</th>}
          </tr>
        </thead>
        <tbody>
          {rowKeys.map((rk) => (
            <tr key={rk}>
              <th scope="row">{rk}</th>
              {colKeys.map((ck) => {
                const v = cell.get(`${rk}\u0000${ck}`)
                const bg = v === undefined ? undefined : rampColor(ramp, 0.08 + 0.85 * ((v - lo) / (hi - lo || 1)))
                return (
                  <td key={ck} className="num" title={v === undefined ? '' : formatValue(v, spec)}
                    style={bg ? { background: bg, color: inkFor(bg) } : undefined}>{fmt(v)}</td>
                )
              })}
              {additive && <td className="num total">{fmt(rowTotal(rk))}</td>}
            </tr>
          ))}
        </tbody>
        {additive && (
          <tfoot>
            <tr>
              <th scope="row">Total</th>
              {colKeys.map((ck) => <td key={ck} className="num total">{fmt(colTotal(ck))}</td>)}
              <td className="num total">{fmt(values.reduce((a, b) => a + b, 0))}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}
