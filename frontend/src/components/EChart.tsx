import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BarChart, BoxplotChart, FunnelChart, GaugeChart, HeatmapChart, LineChart, PieChart, RadarChart, SankeyChart,
  ScatterChart, TreemapChart,
} from 'echarts/charts'
import { GridComponent, LegendComponent, RadarComponent, TooltipComponent, VisualMapComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import type { ChartSpec, QueryResult } from '../api/types'
import { buildOption } from '../lib/chartOptions'
import { formatCategory } from '../lib/format'
import { useShortLabels } from '../lib/labels'
import { readChartColors, useTheme } from '../lib/themeContext'

echarts.use([
  BarChart, BoxplotChart, FunnelChart, GaugeChart, HeatmapChart, LineChart, PieChart, RadarChart, SankeyChart,
  ScatterChart, TreemapChart, GridComponent, LegendComponent, RadarComponent, TooltipComponent,
  VisualMapComponent, SVGRenderer,
])

/** Width in 10px steps: small jitter doesn't rebuild the option. */
const step = (px: number) => Math.round(px / 10) * 10

export function EChart({ spec, result }: { spec: ChartSpec; result: QueryResult }) {
  const ref = useRef<HTMLDivElement>(null)
  // The instance is handed over (as state) once the observer has measured it, and the option is applied to
  // whichever instance is current, so drawing never depends on the width *changing* after a (re)mount.
  const [chart, setChart] = useState<echarts.ECharts | null>(null)
  const { resolved } = useTheme()
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
    const option = buildOption(spec, result, readChartColors(), { width, short })
    // Respect the OS "reduce motion" setting: no entry animations.
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    chart.setOption(still ? { ...option, animation: false } : option, { notMerge: true })
  }, [chart, spec, result, resolved, width, short])

  return <div ref={ref} className="echart" role="img" aria-label={`${spec.type} chart`} />
}
