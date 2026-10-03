import type { ChartSpec, QueryResult } from '../api/types'
import { formatCategory, formatValue, humanize } from '../lib/format'

/** Plain table of a widget's result. Doubles as the accessible view of every chart. */
export function DataTable({ result, spec }: { result: QueryResult; spec: ChartSpec }) {
  // Only money-like columns get currency formatting; counts stay plain numbers.
  const isMoney = (col: string) =>
    spec.format === 'currency' && /revenue|amount|amt|value|price|sales|aov/i.test(col)
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>{result.columns.map((c, j) => (
            <th key={c} className={typeof result.rows[0]?.[j] === 'number' ? 'num' : undefined}>{humanize(c)}</th>
          ))}</tr>
        </thead>
        <tbody>
          {result.rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className={typeof cell === 'number' ? 'num' : undefined}>
                  {typeof cell === 'number'
                    ? formatValue(cell, isMoney(result.columns[j]) ? spec : { format: spec.format === 'percent' ? 'percent' : 'number' })
                    : formatCategory(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
