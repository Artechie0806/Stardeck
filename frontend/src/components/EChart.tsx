import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BarChart, BoxplotChart, FunnelChart, GaugeChart, HeatmapChart, LineChart, PieChart, RadarChart, SankeyChart,
  ScatterChart, TreemapChart,
} from 'echarts/charts'
import { GridComponent, LegendComponent, RadarComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import type { Cell, ChartSpec, QueryResult } from '../api/types'
import { deriveRoles } from '../lib/chartFit'
import { buildOption, emphasise } from '../lib/chartOptions'
import { formatCategory } from '../lib/format'
import { useShortLabels } from '../lib/labels'
import { usePrintLayout } from '../lib/printing'
import { readChartColors, useTheme } from '../lib/themeContext'

echarts.use([
  BarChart, BoxplotChart, FunnelChart, GaugeChart, HeatmapChart, LineChart, PieChart, RadarChart, SankeyChart,
  ScatterChart, TreemapChart, GridComponent, LegendComponent, RadarComponent, TooltipComponent,
  VisualMapComponent, SVGRenderer,
])

/** Width in 10px steps: small jitter doesn't rebuild the option. */
const step = (px: number) => Math.round(px / 10) * 10

/** Chart types whose marks are categories: clicking one cross-filters the dashboard. */
const PICKABLE = new Set(['bar', 'hbar', 'line', 'area', 'pie', 'donut', 'funnel', 'treemap', 'waterfall'])

export function EChart({ spec, result, onPick, picked }: {
  spec: ChartSpec; result: QueryResult; onPick?: (column: string, value: Cell) => void
  /** The value this chart is filtering the dashboard by (from a click), shown highlighted. */
  picked?: Cell
}) {
  const ref = useRef<HTMLDivElement>(null)
  // The instance is handed over (as state) once the observer has measured it, and the option is applied to
  // whichever instance is current, so drawing never depends on the width *changing* after a (re)mount.
  const [chart, setChart] = useState<echarts.ECharts | null>(null)
  const { resolved } = useTheme()
  const printing = usePrintLayout()
  // Width decides whether category labels fit flat, need tilting, or must thin out.
  const [width, setWidth] = useState(0)
  // Every text value in the result is a potential axis/legend label.
  const labels = useMemo(() => {
    const out = new Set<string>()
    for (const r of result.rows) for (const v of r) if (typeof v === 'string') out.add(formatCategory(v))
    return [...out].slice(0, 200)
  }, [result])
  const short = useShortLabels(labels)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const instance = echarts.init(el, undefined, { renderer: 'svg' })
    const observer = new ResizeObserver((entries) => {
      instance.resize()
      setWidth(step(entries[0].contentRect.width))
      setChart(instance) // the first report hands over a measured instance; later ones are no-ops
    })
    observer.observe(el)
    return () => {
      observer.disconnect()
      instance.dispose()
      setChart((c) => (c === instance ? null : c))
    }
  }, [])

  useEffect(() => {
    // No width yet (e.g. mounted while hidden): the observer reports one once the chart is shown.
    if (!chart || !width) return
    // Re-read tokens whenever the theme flips so dark mode gets its own validated steps.
    const colors = readChartColors()
    const built = buildOption(spec, result, colors, { width, short })
    const option = picked === undefined ? built : emphasise(built, formatCategory(picked), colors)
    // No entry animations for "reduce motion", or when printing (the PDF would catch them halfway).
    const still = printing || window.matchMedia('(prefers-reduced-motion: reduce)').matches
    chart.setOption(still ? { ...option, animation: false } : option, { notMerge: true })
  }, [chart, spec, result, resolved, width, short, picked, printing])

  // Cross-filtering: a click reports the category's raw value (charts show formatted names).
  const x = PICKABLE.has(spec.type) ? deriveRoles(result, spec).x : undefined
  useEffect(() => {
    if (!chart || !onPick || !x) return
    const xi = result.columns.indexOf(x)
    const handler = (p: { componentType?: string; name?: string }) => {
      if (p.componentType !== 'series' || !p.name) return
      const row = result.rows.find((r) => formatCategory(r[xi]) === p.name)
      if (row) onPick(x, row[xi])
    }
    chart.on('click', handler)
    return () => { if (!chart.isDisposed()) chart.off('click', handler) } // unmounting disposes the chart first
  }, [chart, onPick, result, x])

  return <div ref={ref} className={`echart${onPick && x ? ' pickable' : ''}`} role="img" aria-label={`${spec.type} chart`}
    title={onPick && x ? 'Click a bar or slice to filter the dashboard' : undefined} />
}
