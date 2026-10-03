// ChartSpec + QueryResult -> ECharts option, for every ECharts-drawn chart type.
// Conventions (see CONTRIBUTING): one value axis only; bars <= 24px with 4px rounded data-ends;
// 2px lines; ~10% area wash; hairline solid grid; categorical colors in fixed order and assigned by
// series name (stable across filters); sequential blue ramp for magnitude; a legend whenever there
// are 2+ series; text in text tokens, never in series colors; every chart has a hover layer.
import type { EChartsCoreOption } from 'echarts/core'
import type { Cell, ChartSpec, QueryResult } from '../api/types'
import { deriveRoles, MAX_SERIES, naturalOrder, type Roles } from './chartFit'
import { formatCategory, formatValue, humanize } from './format'
import type { ChartColors } from './themeContext'

const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

/** Rendering context: the chart's pixel width and the display form of long labels. */
export interface ChartContext {
  width: number
  short: (label: string) => string
}

const CHAR_PX = 6.6 // average width of an 11-12px label character
const TILT = 35
type Num = number | null
const num = (v: Cell): Num => (typeof v === 'number' ? v : null)

// ---------- shared pieces ----------

function kit(spec: ChartSpec, c: ChartColors, ctx: ChartContext) {
  const fmt = (v: unknown, compact = false) => formatValue(v as number, spec, compact)
  const text = { fontFamily: FONT, color: c.textSecondary, fontSize: 12 }
  const tooltip = {
    backgroundColor: c.surface, borderColor: c.grid, borderWidth: 1, padding: [8, 12],
    textStyle: { fontFamily: FONT, color: c.text, fontSize: 12 }, confine: true,
    extraCssText: 'box-shadow: 0 4px 16px rgba(0,0,0,0.12); border-radius: 8px;',
  }
  // Value leads, label follows; series keyed with a short line, not a box.
  const row = (label: string, value: string, color: string) =>
    `<div style="display:flex;align-items:baseline;gap:8px;justify-content:space-between">` +
    `<span style="display:inline-flex;align-items:center;gap:6px;color:${c.textSecondary}">` +
    `<span style="display:inline-block;width:12px;height:2px;background:${color};border-radius:1px"></span>` +
    `${esc(label)}</span><strong style="font-weight:600">${esc(value)}</strong></div>`
  const head = (s: string) => `<div style="color:${c.muted};margin-bottom:4px">${esc(s)}</div>`
  const valueAxis = (pct = false) => ({
    type: 'value' as const,
    max: pct ? 100 : undefined,
    axisLabel: { ...text, color: c.muted, formatter: (v: number) => (pct ? `${v}%` : fmt(v, true)) },
    splitLine: { lineStyle: { color: c.grid, width: 1, type: 'solid' as const } },
    axisLine: { show: false }, axisTick: { show: false },
  })
  /**
   * Category axis whose labels always fit: flat when there's room, tilted when not (every label
   * still shown), thinned only when even tilted labels would collide. Long labels use their
   * AI-shortened form; the full name stays in the tooltip.
   *   time: dates/periods, which read fine thinned out, so they're never tilted or shortened.
   *   vertical: a y axis (horizontal bars, heatmap rows): labels get a capped width instead.
   */
  const categoryAxis = (data: string[], opts: { time?: boolean; vertical?: boolean } = {}) => {
    const base = {
      type: 'category' as const, data,
      axisLine: { lineStyle: { color: c.axis } }, axisTick: { show: false },
    }
    const label = { ...text, color: c.muted }
    if (opts.time) return { ...base, axisLabel: { ...label, hideOverlap: true } }
    if (opts.vertical) {
      return { ...base, axisLabel: { ...label, interval: data.length <= 40 ? 0 : 'auto', hideOverlap: data.length > 40,
        width: Math.max(60, Math.min(160, ctx.width * 0.32)), overflow: 'truncate' as const, formatter: ctx.short } }
    }
    const longest = Math.max(0, ...data.map((d) => ctx.short(d).length)) * CHAR_PX + 12
    const slot = Math.max(120, ctx.width - 90) / Math.max(1, data.length)
    const flat = longest <= slot
    // Tilted labels need ~16px of spacing each (one text line, slanted); below that, thin them out.
    const thin = !flat && slot < 16
    return { ...base, axisLabel: {
      ...label, formatter: ctx.short, rotate: flat ? 0 : TILT,
      interval: thin ? 'auto' : 0, hideOverlap: thin,
      width: flat ? undefined : 130, overflow: 'truncate' as const,
    } }
  }
  // At most 8 series, so the legend wraps onto extra rows instead of paging (paging hides names).
  const legend = (show: boolean) => (show ? {
    top: 0, left: 0, icon: 'rect', itemWidth: 10, itemHeight: 10, itemGap: 14, type: 'plain' as const,
    textStyle: { ...text }, formatter: ctx.short,
  } : { show: false })
  /** Vertical space the wrapped legend takes, so the plot starts below it. `extra`: characters added to each name. */
  const legendHeight = (names: string[], extra = 0) => {
    let rows = 1, used = 0
    for (const n of names) {
      const w = (ctx.short(n).length + extra) * CHAR_PX + 24 + 14
      if (used + w > ctx.width && used > 0) { rows++; used = 0 }
      used += w
    }
    return rows * 20 + 12
  }
  return { fmt, text, tooltip, row, head, valueAxis, categoryAxis, legend, legendHeight, short: ctx.short }
}

function esc(s: string) {
  return String(s).replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
}

const col = (r: QueryResult, name?: string) => (name ? r.columns.indexOf(name) : -1)

/** Categories x series -> values. Series names sorted so colors follow the entity, not the rank. */
function pivot(result: QueryResult, roles: Roles) {
  const xi = col(result, roles.x)
  const categories: string[] = []
  const seen = new Set<string>()
  for (const r of result.rows) {
    const k = formatCategory(r[xi])
    if (!seen.has(k)) { seen.add(k); categories.push(k) }
  }
  categories.splice(0, categories.length, ...naturalOrder(categories))
  if (roles.series) {
    const si = col(result, roles.series)
    const yi = col(result, roles.measures[0])
    const names = naturalOrder([...new Set(result.rows.map((r) => formatCategory(r[si])))].sort()).slice(0, MAX_SERIES)
    const values = names.map(() => categories.map((): Num => null))
    for (const r of result.rows) {
      const s = names.indexOf(formatCategory(r[si]))
      if (s >= 0) values[s][categories.indexOf(formatCategory(r[xi]))] = num(r[yi])
    }
    return { categories, names, values }
  }
  const names = roles.measures.map(humanize)
  const values = roles.measures.map((m) => {
    const i = col(result, m)
    const byCat = new Map(result.rows.map((r) => [formatCategory(r[xi]), num(r[i])]))
    return categories.map((cat) => byCat.get(cat) ?? null)
  })
  return { categories, names, values }
}

/** Interpolate the sequential ramp (0..1). */
export function rampColor(ramp: string[], t: number): string {
  const x = Math.max(0, Math.min(1, t)) * (ramp.length - 1)
  const i = Math.floor(x)
  const f = x - i
  const a = hex(ramp[i]), b = hex(ramp[Math.min(i + 1, ramp.length - 1)])
  const mix = a.map((v, k) => Math.round(v + (b[k] - v) * f))
  return `rgb(${mix.join(',')})`
}
const hex = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))

// ---------- dispatcher ----------

export function buildOption(spec: ChartSpec, raw: QueryResult, c: ChartColors,
  ctx: ChartContext = { width: 600, short: (l) => l }): EChartsCoreOption {
  const roles = deriveRoles(raw, spec)
  const yi = col(raw, roles.measures[0])
  // Trends start where the data starts: e.g. a year-on-year line has nothing for its first year.
  const result = (spec.type === 'line' || spec.type === 'area') && !roles.series && roles.measures.length === 1
    ? trimLeadingEmpty(raw, yi) : raw
  switch (spec.type) {
    case 'pie': case 'donut': return pie(spec, result, roles, c, ctx)
    case 'treemap': return treemap(spec, result, roles, c, ctx)
    case 'funnel': return funnel(spec, result, roles, c, ctx)
    case 'waterfall': return waterfall(spec, result, roles, c, ctx)
    case 'scatter': case 'bubble': return scatter(spec, result, roles, c, ctx)
    case 'heatmap': return heatmap(spec, result, roles, c, ctx)
    case 'radar': return radar(spec, result, roles, c, ctx)
    case 'sankey': return sankey(spec, result, roles, c, ctx)
    case 'histogram': return histogram(spec, result, roles, c, ctx)
    case 'boxplot': return boxplot(spec, result, roles, c, ctx)
    case 'gauge': return gauge(spec, result, roles, c, ctx)
    default: return cartesian(spec, result, roles, c, ctx)
  }
}

function trimLeadingEmpty(result: QueryResult, yi: number): QueryResult {
  const first = result.rows.findIndex((r) => r[yi] !== null && r[yi] !== undefined)
  return first > 0 ? { ...result, rows: result.rows.slice(first) } : result
}

// ---------- bar / line / area (single, clustered, stacked, 100%) ----------

function cartesian(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const { categories, names, values } = pivot(result, roles)
  const horizontal = spec.type === 'hbar'
  const isLine = spec.type === 'line' || spec.type === 'area'
  const stack = spec.stack && spec.stack !== 'none' && names.length > 1 ? spec.stack : 'none'
  const pct = stack === 'percent'
  const data = pct
    ? values.map((s) => s.map((v, j) => {
        const total = values.reduce((acc, ser) => acc + Math.abs(ser[j] ?? 0), 0)
        return v === null || !total ? null : (Math.abs(v) / total) * 100
      }))
    : values
  const multi = names.length > 1
  const single = !multi
  const last = categories.length - 1
  const show = (v: number) => (pct ? `${v.toFixed(1)}%` : k.fmt(v, true))

  const series = names.map((name, s) => {
    const color = c.series[s % c.series.length]
    if (isLine) {
      return {
        type: 'line', name, data: data[s], stack: stack !== 'none' ? 'total' : undefined,
        symbol: 'circle', showSymbol: false, symbolSize: 8, connectNulls: false,
        lineStyle: { width: 2, color, cap: 'round', join: 'round' },
        itemStyle: { color, borderColor: c.surface, borderWidth: 2 },
        areaStyle: spec.type === 'area' ? { color, opacity: stack !== 'none' ? 0.35 : 0.1 } : undefined,
        endLabel: single ? {
          show: true, color: c.textSecondary, fontFamily: FONT, fontSize: 11, distance: 6,
          formatter: (p: { value: number }) => show(p.value),
        } : undefined,
        emphasis: { focus: multi ? 'series' : 'none' },
        // Single trends get an end dot; multi-series rely on legend + crosshair.
        ...(single ? { symbol: 'circle', showSymbol: true,
          symbolSize: (_: unknown, p: { dataIndex: number }) => (p.dataIndex === last ? 8 : 0) } : {}),
      }
    }
    const stacked = stack !== 'none'
    return {
      type: 'bar', name, data: data[s], stack: stacked ? 'total' : undefined,
      barMaxWidth: 24, barGap: '10%', barCategoryGap: multi ? '25%' : '30%',
      itemStyle: {
        color,
        // Rounded data-end, square at the baseline; stacks get a 2px surface gap instead.
        borderRadius: stacked ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        borderColor: stacked ? c.surface : undefined, borderWidth: stacked ? 1 : 0,
      },
      emphasis: { focus: multi ? 'series' : 'none' },
      label: {
        show: single && categories.length <= 8, position: horizontal ? 'right' : 'top',
        color: c.textSecondary, fontFamily: FONT, fontSize: 11, formatter: (p: { value: number }) => show(p.value),
      },
    }
  })

  const crosshair = isLine
  return {
    textStyle: k.text,
    color: c.series,
    legend: k.legend(multi),
    grid: { left: 8, right: isLine && single ? 56 : horizontal && single ? 64 : 16, top: multi ? k.legendHeight(names) : 16, bottom: 8,
      outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: {
      ...k.tooltip, trigger: 'axis',
      axisPointer: crosshair ? { type: 'line', lineStyle: { color: c.axis, width: 1 } } : { type: 'shadow', shadowStyle: { color: c.grid, opacity: 0.4 } },
      formatter: (ps: { name: string; value: number | null; seriesName: string; color: string }[]) =>
        k.head(ps[0]?.name ?? '') + ps.filter((p) => p.value !== null && p.value !== undefined)
          .map((p) => k.row(multi ? p.seriesName : humanize(roles.measures[0] ?? ''), show(p.value as number), p.color)).join(''),
    },
    xAxis: horizontal ? { ...k.valueAxis(pct), splitNumber: 3 } : { ...k.categoryAxis(categories, { time: roles.temporalX }), boundaryGap: !isLine },
    yAxis: horizontal ? { ...k.categoryAxis(categories, { vertical: true }), inverse: true } : k.valueAxis(pct),
    series,
  }
}

// ---------- parts of a whole ----------

function pie(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const xi = col(result, roles.x), yi = col(result, roles.measures[0])
  const items = result.rows.map((r) => ({ name: formatCategory(r[xi]), value: num(r[yi]) ?? 0 }))
  const total = items.reduce((a, b) => a + b.value, 0) || 1
  return {
    textStyle: k.text, color: c.series,
    tooltip: { ...k.tooltip, trigger: 'item',
      formatter: (p: { name: string; value: number; color: string }) =>
        k.row(`${p.name} · ${((p.value / total) * 100).toFixed(1)}%`, k.fmt(p.value), p.color) },
    // Wrap rather than page: pie cards are narrow and have only a few parts.
    legend: { ...k.legend(true), type: 'plain', top: undefined, bottom: 0, left: 'center',
      formatter: (name: string) => `${k.short(name)}  ${Math.round(((items.find((i) => i.name === name)?.value ?? 0) / total) * 100)}%` },
    series: [{
      // The pie fills the space above the (possibly wrapped) legend, so the two never overlap.
      bottom: k.legendHeight(items.map((i) => i.name), 6),
      type: 'pie', radius: spec.type === 'donut' ? ['54%', '82%'] : [0, '82%'], center: ['50%', '50%'],
      itemStyle: { borderColor: c.surface, borderWidth: 2, borderRadius: spec.type === 'donut' ? 4 : 0 },
      label: { show: false }, emphasis: { scale: true, scaleSize: 4 }, data: items,
    }],
  }
}

function treemap(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const xi = col(result, roles.x), yi = col(result, roles.measures[0])
  const items = result.rows.map((r) => ({ name: formatCategory(r[xi]), value: num(r[yi]) ?? 0 }))
  const max = Math.max(...items.map((i) => i.value), 1)
  const total = items.reduce((a, b) => a + b.value, 0) || 1
  return {
    textStyle: k.text,
    tooltip: { ...k.tooltip, formatter: (p: { name: string; value: number; color: string }) =>
      k.row(`${p.name} · ${((p.value / total) * 100).toFixed(1)}%`, k.fmt(p.value), p.color) },
    series: [{
      type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false },
      left: 0, right: 0, top: 0, bottom: 0,
      itemStyle: { borderColor: c.surface, borderWidth: 2, gapWidth: 2 },
      label: { show: true, fontFamily: FONT, fontSize: 12,
        formatter: (p: { name: string; value: number }) => `${k.short(p.name)}\n${k.fmt(p.value, true)}` },
      data: items.map((i) => {
        const fill = rampColor(c.seq, 0.25 + 0.75 * (i.value / max))
        return { ...i, itemStyle: { color: fill }, label: { color: inkFor(fill) } }
      }),
    }],
  }
}

function funnel(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const xi = col(result, roles.x), yi = col(result, roles.measures[0])
  const items = result.rows.map((r) => ({ name: formatCategory(r[xi]), value: num(r[yi]) ?? 0 }))
  const first = items[0]?.value || 1
  return {
    textStyle: k.text,
    tooltip: { ...k.tooltip, trigger: 'item', formatter: (p: { name: string; value: number; color: string }) =>
      k.row(`${p.name} · ${((p.value / first) * 100).toFixed(1)}% of first stage`, k.fmt(p.value), p.color) },
    series: [{
      type: 'funnel', sort: 'none', left: '8%', right: '8%', top: 8, bottom: 8, gap: 2, minSize: '8%',
      itemStyle: { borderColor: c.surface, borderWidth: 0 },
      label: { show: true, position: 'inside', fontFamily: FONT, fontSize: 12,
        formatter: (p: { name: string; value: number }) => `${k.short(p.name)}  ${k.fmt(p.value, true)}` },
      // Ordered stages: an ordinal ramp (darker = later), labels inked for contrast.
      data: items.map((i, n) => {
        const fill = rampColor(c.seq, 0.35 + 0.65 * (items.length > 1 ? n / (items.length - 1) : 1))
        return { ...i, itemStyle: { color: fill }, label: { color: inkFor(fill) } }
      }),
    }],
  }
}

// ---------- waterfall ----------

const TOTAL_STEP = /^(total|net|end|ending|closing|final|start|starting|opening|begin)/i

function waterfall(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const xi = col(result, roles.x)
  const cats: string[] = [], base: Num[] = [], up: Num[] = [], down: Num[] = [], totals: Num[] = []
  const total = (label: string, v: number) => { cats.push(label); base.push(0); up.push(null); down.push(null); totals.push(v) }
  const step = (label: string, from: number, delta: number) => {
    cats.push(label); totals.push(null); base.push(Math.min(from, from + delta))
    if (delta >= 0) { up.push(delta); down.push(null) } else { up.push(null); down.push(-delta) }
  }
  let running = 0
  if (roles.measures.length >= 2) {
    // Bridge form: step, value_before, value_after -> start total, per-step change, end total.
    const [bi, ai] = roles.measures.slice(0, 2).map((m) => col(result, m))
    const before = result.rows.reduce((a, r) => a + (num(r[bi]) ?? 0), 0)
    total(humanize(roles.measures[0]), before)
    running = before
    for (const r of result.rows) {
      const delta = (num(r[ai]) ?? 0) - (num(r[bi]) ?? 0)
      step(formatCategory(r[xi]), running, delta)
      running += delta
    }
    total(humanize(roles.measures[1]), running)
  } else {
    // Delta form: first row (or rows named like "Total"/"Opening") are absolute; the rest are changes.
    const yi = col(result, roles.measures[0])
    result.rows.forEach((r, n) => {
      const label = formatCategory(r[xi])
      const v = num(r[yi]) ?? 0
      if (TOTAL_STEP.test(label) || (n === 0 && result.rows.length > 2)) { running = v; total(label, v); return }
      step(label, running, v)
      running += v
    })
    total('Total', running)
  }
  const bar = (name: string, data: Num[], color: string) => ({
    type: 'bar', name, stack: 'w', data, barMaxWidth: 24, barCategoryGap: '30%',
    itemStyle: { color, borderRadius: 2 },
    label: { show: cats.length <= 12, position: 'top', color: c.textSecondary, fontFamily: FONT, fontSize: 11,
      formatter: (p: { value: number | null }) => (p.value ? `${name === 'Decrease' ? '−' : ''}${k.fmt(p.value, true)}` : '') },
  })
  return {
    textStyle: k.text,
    legend: { ...k.legend(true), data: ['Increase', 'Decrease', 'Total'] },
    grid: { left: 8, right: 16, top: 34, bottom: 8, outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: { ...k.tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: c.grid, opacity: 0.4 } },
      formatter: (ps: { name: string; seriesName: string; value: number | null; color: string }[]) =>
        k.head(ps[0]?.name ?? '') + ps.filter((p) => p.seriesName !== 'base' && p.value)
          .map((p) => k.row(p.seriesName, `${p.seriesName === 'Decrease' ? '−' : ''}${k.fmt(p.value)}`, p.color)).join('') },
    xAxis: k.categoryAxis(cats),
    yAxis: k.valueAxis(),
    series: [
      { type: 'bar', name: 'base', stack: 'w', data: base, itemStyle: { color: 'transparent' }, emphasis: { disabled: true }, tooltip: { show: false } },
      bar('Increase', up, c.series[0]),
      bar('Decrease', down, c.series[1]),
      bar('Total', totals, c.muted),
    ],
  }
}

// ---------- relationships ----------

function scatter(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const nums = roles.nums
  const xName = spec.x && nums.includes(spec.x) ? spec.x : nums[0]
  const yName = spec.y && nums.includes(spec.y) && spec.y !== xName ? spec.y : nums.find((n) => n !== xName)!
  const sizeName = spec.type === 'bubble' ? (spec.size && nums.includes(spec.size) ? spec.size : nums.find((n) => n !== xName && n !== yName)) : undefined
  const labelName = spec.label ?? roles.dims[0]
  const [xi, yi, si, li] = [xName, yName, sizeName, labelName].map((n) => col(result, n))
  const sizes = si >= 0 ? result.rows.map((r) => num(r[si]) ?? 0) : []
  const maxSize = Math.max(...sizes, 1)
  const plain = (v: number) => formatValue(v, { format: 'number' }, true)
  const axis = (name: string, vertical = false) => ({
    type: 'value' as const, name: humanize(name), nameLocation: 'middle' as const, nameGap: vertical ? 46 : 28, scale: true,
    nameTextStyle: { ...k.text, color: c.muted },
    axisLabel: { ...k.text, color: c.muted, formatter: plain },
    splitLine: { lineStyle: { color: c.grid } }, axisLine: { show: false }, axisTick: { show: false },
  })
  return {
    textStyle: k.text,
    grid: { left: 40, right: 16, top: 16, bottom: 28, outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: { ...k.tooltip, trigger: 'item', formatter: (p: { data: [number, number, number, string] }) =>
      k.head(p.data[3] ?? '') + k.row(humanize(xName), plain(p.data[0]), c.series[0]) + k.row(humanize(yName), plain(p.data[1]), c.series[0]) +
      (sizeName ? k.row(humanize(sizeName), plain(p.data[2]), c.series[0]) : '') },
    xAxis: axis(xName), yAxis: axis(yName, true),
    series: [{
      type: 'scatter',
      data: result.rows.map((r) => [num(r[xi]), num(r[yi]), si >= 0 ? num(r[si]) : 0, li >= 0 ? formatCategory(r[li]) : '']),
      // Dots >= 8px with a 2px surface ring; bubbles scale by area.
      symbolSize: (d: number[]) => (si >= 0 ? 8 + 40 * Math.sqrt((d[2] ?? 0) / maxSize) : 9),
      itemStyle: { color: c.series[0], opacity: si >= 0 ? 0.7 : 0.85, borderColor: c.surface, borderWidth: 2 },
      emphasis: { scale: 1.3 },
    }],
  }
}

function heatmap(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const xi = col(result, roles.x), si = col(result, roles.series), yi = col(result, roles.measures[0])
  const xs = naturalOrder([...new Set(result.rows.map((r) => formatCategory(r[xi])))])
  // Rows read top-down, so reverse: the first weekday/month sits at the top.
  const ys = naturalOrder([...new Set(result.rows.map((r) => formatCategory(r[si])))].sort()).reverse()
  const data = result.rows.map((r) => [xs.indexOf(formatCategory(r[xi])), ys.indexOf(formatCategory(r[si])), num(r[yi])])
  const vals = data.map((d) => d[2] as number).filter((v) => v !== null)
  return {
    textStyle: k.text,
    grid: { left: 8, right: 8, top: 8, bottom: 44, outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: { ...k.tooltip, formatter: (p: { data: [number, number, number]; color: string }) =>
      k.head(`${xs[p.data[0]]} · ${ys[p.data[1]]}`) + k.row(humanize(roles.measures[0]), k.fmt(p.data[2]), p.color) },
    xAxis: { ...k.categoryAxis(xs, { time: roles.temporalX }), splitArea: { show: false } },
    yAxis: { ...k.categoryAxis(ys, { vertical: true }), splitArea: { show: false } },
    visualMap: {
      min: Math.min(...vals, 0), max: Math.max(...vals, 1), calculable: false, orient: 'horizontal',
      left: 'center', bottom: 0, itemWidth: 10, itemHeight: 120, inRange: { color: c.seq },
      textStyle: { ...k.text, color: c.muted }, formatter: (v: number) => k.fmt(v, true),
    },
    series: [{ type: 'heatmap', data, itemStyle: { borderColor: c.surface, borderWidth: 2 },
      label: { show: xs.length * ys.length <= 48, fontSize: 10, fontFamily: FONT, formatter: (p: { data: number[] }) => k.fmt(p.data[2], true) },
      emphasis: { itemStyle: { borderColor: c.text, borderWidth: 1 } } }],
  }
}

function radar(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const { categories, names, values } = pivot(result, roles)
  const max = Math.max(...values.flat().map((v) => v ?? 0), 1) * 1.1
  return {
    textStyle: k.text, color: c.series,
    legend: k.legend(names.length > 1),
    tooltip: { ...k.tooltip, trigger: 'item', formatter: (p: { name: string; value: number[]; color: string }) =>
      k.head(p.name) + categories.map((cat, i) => k.row(cat, k.fmt(p.value[i]), p.color)).join('') },
    radar: {
      indicator: categories.map((name) => ({ name, max })), radius: '62%', center: ['50%', names.length > 1 ? '56%' : '52%'],
      axisName: { color: c.textSecondary, fontFamily: FONT, fontSize: 11, formatter: (name: string) => k.short(name) },
      splitLine: { lineStyle: { color: c.grid } }, splitArea: { show: false }, axisLine: { lineStyle: { color: c.grid } },
    },
    series: [{ type: 'radar', symbolSize: 6,
      data: names.map((name, s) => ({ name, value: values[s].map((v) => v ?? 0),
        lineStyle: { width: 2, color: c.series[s] }, itemStyle: { color: c.series[s] }, areaStyle: { color: c.series[s], opacity: 0.1 } })) }],
  }
}

function sankey(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const si = col(result, roles.x), ti = col(result, roles.series), vi = col(result, roles.measures[0])
  // Same label on both sides would create a cycle; suffix the target side.
  const sources = new Set(result.rows.map((r) => formatCategory(r[si])))
  const target = (v: Cell) => { const t = formatCategory(v); return sources.has(t) ? `${t} ` : t }
  const links = result.rows.filter((r) => (num(r[vi]) ?? 0) > 0)
    .map((r) => ({ source: formatCategory(r[si]), target: target(r[ti]), value: num(r[vi]) as number }))
  const sourceNames = naturalOrder([...sources].sort())
  const nodes = [...new Set(links.flatMap((l) => [l.source, l.target]))]
  // Sources get categorical colors in the same name order as every other chart; targets stay neutral.
  const nodeColor = (name: string) => {
    const i = sourceNames.indexOf(name)
    return i >= 0 ? c.series[i % c.series.length] : c.muted
  }
  return {
    textStyle: k.text,
    tooltip: { ...k.tooltip, trigger: 'item', formatter: (p: { data: { source?: string; target?: string; value?: number; name?: string }; value: number; color: string }) =>
      p.data.source ? k.row(`${p.data.source} → ${p.data.target}`, k.fmt(p.data.value), c.series[0])
        : k.row(p.data.name ?? '', k.fmt(p.value), p.color) },
    series: [{
      type: 'sankey', left: 8, right: 96, top: 8, bottom: 8, nodeWidth: 12, nodeGap: 8, emphasis: { focus: 'adjacency' },
      data: nodes.map((name) => ({ name, itemStyle: { color: nodeColor(name), borderColor: c.surface } })),
      links, lineStyle: { color: 'source', opacity: 0.25, curveness: 0.5 },
      label: { color: c.textSecondary, fontFamily: FONT, fontSize: 11, formatter: (p: { name: string }) => k.short(p.name.trim()) },
    }],
  }
}

// ---------- distributions ----------

function histogram(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const yi = col(result, roles.measures[0] ?? roles.nums[0])
  const vals = result.rows.map((r) => num(r[yi])).filter((v): v is number => v !== null)
  const min = Math.min(...vals), max = Math.max(...vals)
  const bins = Math.min(30, Math.max(5, Math.ceil(Math.log2(vals.length) + 1))) // Sturges
  const width = (max - min) / bins || 1
  const counts = Array.from({ length: bins }, () => 0)
  for (const v of vals) counts[Math.min(bins - 1, Math.floor((v - min) / width))]++
  const labels = counts.map((_, i) => `${k.fmt(min + i * width, true)}–${k.fmt(min + (i + 1) * width, true)}`)
  return {
    textStyle: k.text,
    grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: { ...k.tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: c.grid, opacity: 0.4 } },
      formatter: (ps: { name: string; value: number }[]) => k.head(ps[0].name) + k.row('Count', String(ps[0].value), c.series[0]) },
    xAxis: k.categoryAxis(labels, { time: true }), // numeric ranges: thin out rather than tilt
    yAxis: { ...k.valueAxis(), axisLabel: { ...k.text, color: c.muted } },
    // Adjacent bins touch, separated only by the 2px surface gap.
    series: [{ type: 'bar', data: counts, barCategoryGap: 0, itemStyle: { color: c.series[0], borderColor: c.surface, borderWidth: 1, borderRadius: [2, 2, 0, 0] } }],
  }
}

function quantiles(sorted: number[]) {
  const q = (p: number) => {
    const i = (sorted.length - 1) * p, lo = Math.floor(i), hi = Math.ceil(i)
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo)
  }
  return [sorted[0], q(0.25), q(0.5), q(0.75), sorted[sorted.length - 1]]
}

function boxplot(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const yi = col(result, roles.measures[0] ?? roles.nums[0])
  const xi = col(result, spec.x ?? roles.dims[0])
  const groups = new Map<string, number[]>()
  for (const r of result.rows) {
    const v = num(r[yi])
    if (v === null) continue
    const key = xi >= 0 ? formatCategory(r[xi]) : humanize(roles.measures[0] ?? '')
    groups.set(key, [...(groups.get(key) ?? []), v])
  }
  const names = [...groups.keys()]
  const stats = names.map((n) => quantiles([...groups.get(n)!].sort((a, b) => a - b)))
  const names5 = ['Min', 'Lower quartile', 'Median', 'Upper quartile', 'Max']
  return {
    textStyle: k.text,
    grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: 'same' as const, outerBoundsContain: 'axisLabel' as const },
    tooltip: { ...k.tooltip, trigger: 'item', formatter: (p: { name: string; data: number[] }) =>
      k.head(p.name) + p.data.slice(-5).map((v, i) => k.row(names5[i], k.fmt(v), c.series[0])).join('') },
    xAxis: k.categoryAxis(names),
    yAxis: k.valueAxis(),
    series: [{ type: 'boxplot', data: stats, boxWidth: [8, 28],
      itemStyle: { color: c.surface, borderColor: c.series[0], borderWidth: 2 } }],
  }
}

// ---------- gauge ----------

function niceCeil(v: number) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((x) => x >= v) ?? v
}

function gauge(spec: ChartSpec, result: QueryResult, roles: Roles, c: ChartColors, ctx: ChartContext): EChartsCoreOption {
  const k = kit(spec, c, ctx)
  const row = result.rows[0] ?? []
  const value = num(row[col(result, spec.y ?? roles.nums[0])]) ?? 0
  const target = spec.target ? num(row[col(result, spec.target)]) : null
  const max = target ?? (spec.format === 'percent' ? 100 : niceCeil(value * 1.25))
  return {
    textStyle: k.text,
    tooltip: { ...k.tooltip, formatter: () => k.row('Value', k.fmt(value), c.series[0]) + (target !== null ? k.row('Target', k.fmt(target), c.muted) : '') },
    series: [{
      type: 'gauge', min: 0, max, startAngle: 210, endAngle: -30, radius: '92%', center: ['50%', '58%'],
      progress: { show: true, width: 14, roundCap: true, itemStyle: { color: c.series[0] } },
      axisLine: { lineStyle: { width: 14, color: [[1, c.grid]] }, roundCap: true },
      axisTick: { show: false }, splitLine: { show: false }, pointer: { show: false },
      axisLabel: { show: true, distance: -34, color: c.muted, fontSize: 10, fontFamily: FONT,
        formatter: (v: number) => (v === 0 || v === max ? k.fmt(v, true) : '') },
      title: { show: target !== null, offsetCenter: [0, '38%'], color: c.muted, fontSize: 11, fontFamily: FONT },
      detail: { valueAnimation: true, offsetCenter: [0, '4%'], fontSize: 26, fontWeight: 650, color: c.text, fontFamily: FONT,
        formatter: () => k.fmt(value, true) },
      data: [{ value, name: target !== null ? `of ${k.fmt(target, true)}` : '' }],
    }],
  }
}

/** Black or white text, whichever reads better on `rgb(...)`/hex fill. */
export function inkFor(fill: string): string {
  const m = fill.match(/\d+/g)
  const [r, g, b] = fill.startsWith('#') ? hex(fill) : (m ?? ['0', '0', '0']).map(Number)
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
  return lum > 0.55 ? '#0b0b0b' : '#ffffff'
}

/**
 * The chart someone clicked to filter the dashboard keeps every mark; the picked one stays bright and the rest
 * fade (lines get a marker at the picked point instead), so it's clear what's filtering and another pick is a click away.
 */
export function emphasise(option: EChartsCoreOption, label: string, c: ChartColors): EChartsCoreOption {
  type Axis = { type?: string; data?: unknown[] } | undefined
  const first = (a: unknown): Axis => (Array.isArray(a) ? a[0] : a) as Axis
  const cats = [first(option.xAxis), first(option.yAxis)].find((a) => a?.type === 'category')?.data
  const idx = cats ? cats.indexOf(label) : -1
  const dim = (d: unknown) => {
    const item = d !== null && typeof d === 'object' && !Array.isArray(d) ? d as Record<string, unknown> : { value: d }
    return { ...item, itemStyle: { ...(item.itemStyle as object | undefined), opacity: 0.28 } }
  }
  let marked = false
  const series = (Array.isArray(option.series) ? option.series : [option.series]).map((s) => {
    const ser = s as { type?: string; data?: unknown[] } | undefined
    if (!ser || !Array.isArray(ser.data)) return s
    if (ser.type === 'bar' && idx >= 0) return { ...ser, data: ser.data.map((d, i) => (i === idx ? d : dim(d))) }
    if (ser.type === 'line' && idx >= 0 && !marked) {
      marked = true
      return { ...ser, markLine: { silent: true, symbol: 'none', label: { show: false },
        lineStyle: { color: c.textSecondary, type: 'dashed', width: 1.5 }, data: [{ xAxis: label }] } }
    }
    if (ser.type === 'pie' || ser.type === 'funnel' || ser.type === 'treemap') {
      return { ...ser, data: ser.data.map((d) => ((d as { name?: string } | null)?.name === label ? d : dim(d))) }
    }
    return s
  })
  return { ...option, series }
}
