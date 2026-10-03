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

export function EChart({ spec, result }: { spec: ChartSpec; result: QueryResult }) {
  const ref = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
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
    if (!ref.current) return
    const instance = echarts.init(ref.current, undefined, { renderer: 'svg' })
    chart.current = instance
    const observer = new ResizeObserver((entries) => {
      instance.resize()
      setWidth(Math.round(entries[0].contentRect.width / 10) * 10)
    })
    observer.observe(ref.current)
    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    // Re-read tokens whenever the theme flips so dark mode gets its own validated steps.
    if (!width) return
    const option = buildOption(spec, result, readChartColors(), { width, short })
    // Respect the OS "reduce motion" setting: no entry animations.
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    chart.current?.setOption(still ? { ...option, animation: false } : option, { notMerge: true })
  }, [spec, result, resolved, width, short])

  return <div ref={ref} className="echart" role="img" aria-label={`${spec.type} chart`} />
}
