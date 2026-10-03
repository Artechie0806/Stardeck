import { lazy, Suspense } from 'react'
import type { ChartSpec, DatePreset, QueryResult } from '../api/types'
import { chartLabel, deriveRoles, fitProblem } from '../lib/chartFit'
import { DataTable } from './DataTable'
import { EChart } from './EChart'
import { KpiTile } from './KpiTile'
import { MatrixTable } from './MatrixTable'

// Leaflet only loads for dashboards that have a map.
const MapChart = lazy(() => import('./MapChart').then((m) => ({ default: m.MapChart })))

/** The data part of a widget, shared by dashboard cards and chat previews. */
export function WidgetVisual({ spec, result, preset, asTable = false, dateFiltered = true }: {
  spec: ChartSpec; result: QueryResult; preset: DatePreset; asTable?: boolean; dateFiltered?: boolean
}) {
  if (spec.type === 'kpi') return <KpiTile spec={spec} result={result} preset={preset} dateFiltered={dateFiltered} />
  if (!result.rows.length) return <p className="widget-empty">No data for this period.</p>
  if (asTable || spec.type === 'table') return <DataTable result={result} spec={spec} />
  const problem = fitProblem(spec.type, deriveRoles(result, spec))
  if (problem) {
    // A chart type picked by hand that can't show this data: say why, show the numbers anyway.
    return (
      <div className="fit-fallback">
        <p className="widget-empty small">{chartLabel(spec.type, spec.stack)}: {problem.toLowerCase()}. Showing a table.</p>
        <DataTable result={result} spec={spec} />
      </div>
    )
  }
  if (spec.type !== 'gauge' && allEmpty(spec, result)) {
    return (
      <p className="widget-empty">
        Every value is empty for this period. If this widget compares periods (like year on year),
        try a wider date range or check its SQL.
      </p>
    )
  }
  if (spec.type === 'matrix') return <MatrixTable spec={spec} result={result} />
  if (spec.type === 'map') {
    return <Suspense fallback={<p className="widget-empty">Loading map…</p>}><MapChart spec={spec} result={result} /></Suspense>
  }
  return <EChart spec={spec} result={result} />
}

/** True when the measure column has no values at all (e.g. a comparison with nothing to compare). */
function allEmpty(spec: ChartSpec, result: QueryResult) {
  const roles = deriveRoles(result, spec)
  const cols = roles.measures.map((m) => result.columns.indexOf(m)).filter((i) => i >= 0)
  if (!cols.length) return false
  return result.rows.every((r) => cols.every((i) => r[i] === null || r[i] === undefined))
}
