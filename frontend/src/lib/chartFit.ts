// Which chart types can show a result. Mirrors backend/src/stardeck/charts.py so the chart menu
// can grey out types that don't fit, and a user-picked type that doesn't fit falls back cleanly.
import type { ChartSpec, ChartType, QueryResult, Stack } from '../api/types'

export const MAX_SERIES = 8

const TEMPORAL = new RegExp(
  String.raw`^\d{4}(-\d{2}(-\d{2})?)?([ T].*)?$|^\d{4}-?(Q|W)\d{1,2}$|^Q[1-4]( \d{4})?$|^\d{1,2}$|` +
  String.raw`^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?( \d{4})?$|^(mon|tue|wed|thu|fri|sat|sun)[a-z]*$`, 'i')
const LAT = /(^|_)(lat|latitude)$/i
const LON = /(^|_)(lon|lng|long|longitude)$/i
const PLACE = /(city|town|village|state|province|country|region|location|place|address|district|county|pincode|pin_code|zip|postcode|postal_code|area|locality|store|branch)/i
const ID_LIKE = /(^|_)(id|code|cd|key|no|num|sts|status|type|sgmt|segment)$/i

export interface Roles {
  dims: string[]
  nums: string[]
  x?: string
  series?: string
  measures: string[]
  rows: number
  distinct: Record<string, number>
  temporalX: boolean
  nonneg: boolean
}

const isNum = (v: unknown) => typeof v === 'number'

export function numericColumns(r: QueryResult): string[] {
  return r.columns.filter((_, i) => {
    const vals = r.rows.map((row) => row[i]).filter((v) => v !== null && v !== undefined)
    return vals.length > 0 && vals.every(isNum)
  })
}

export function deriveRoles(result: QueryResult, spec: Partial<ChartSpec> = {}): Roles {
  const cols = result.columns
  const nums = numericColumns(result)
  const dims = cols.filter((c) => !nums.includes(c))
  let x = spec.x && cols.includes(spec.x) ? spec.x : undefined
  if ((!x || nums.includes(x)) && dims.length) x = dims[0]
  x = x ?? cols[0]
  const series = spec.series && dims.includes(spec.series) && spec.series !== x ? spec.series : dims.find((d) => d !== x)
  let measures = (spec.measures ?? []).filter((m) => nums.includes(m) && m !== x)
  if (!measures.length && spec.y && nums.includes(spec.y) && spec.y !== x) measures = [spec.y]
  if (!measures.length) {
    const candidates = nums.filter((n) => n !== x)
    const plain = candidates.filter((n) => !ID_LIKE.test(n))
    measures = (plain.length ? plain : candidates).slice(-1)
  }
  const distinct: Record<string, number> = {}
  cols.forEach((c, i) => { distinct[c] = new Set(result.rows.map((r) => r[i])).size })
  const xi = cols.indexOf(x ?? '')
  const labels = xi >= 0 ? result.rows.map((r) => r[xi]) : []
  const temporalX = labels.length > 0 && labels.every((v) => v === null || (typeof v === 'string' && TEMPORAL.test(v)))
  const nonneg = measures.every((m) => {
    const i = cols.indexOf(m)
    return result.rows.every((r) => ((r[i] as number | null) ?? 0) >= 0)
  })
  return { dims, nums, x, series, measures, rows: result.rows.length, distinct, temporalX, nonneg }
}

export function mapColumns(r: Roles): { lat?: string; lon?: string; location?: string } | null {
  const lat = r.nums.find((n) => LAT.test(n))
  const lon = r.nums.find((n) => LON.test(n))
  if (lat && lon) return { lat, lon }
  const location = r.dims.find((d) => PLACE.test(d))
  return location ? { location } : null
}

/** Why `type` can't show this result, or null if it fits. */
export function fitProblem(type: ChartType, r: Roles): string | null {
  const m = r.measures.length > 0
  const nSeries = r.series ? r.distinct[r.series] ?? 0 : r.measures.length
  switch (type) {
    case 'table': return null
    case 'kpi': case 'gauge': return r.rows === 1 && r.nums.length ? null : 'Needs a single number'
    case 'line': case 'area': case 'bar': case 'hbar':
      if (!(r.x && m)) return 'Needs a category or date column and a number'
      if ((type === 'line' || type === 'area') && !r.temporalX) return 'Needs a time axis (dates, months, years)'
      return nSeries > MAX_SERIES ? `Has more than ${MAX_SERIES} series` : null
    case 'pie': case 'donut':
      if (r.series || !(r.x && m)) return 'Needs one category column and one number'
      return r.rows <= 6 && r.nonneg ? null : 'Works for up to 6 positive parts'
    case 'treemap':
      if (r.series) return 'Needs one category column, not two'
      return r.x && m && r.nonneg && r.rows <= 200 ? null : 'Needs categories with positive numbers'
    case 'funnel':
      if (r.series) return 'Needs one stage column, not two'
      return r.x && m && r.nonneg && r.rows <= 12 ? null : 'Needs up to 12 stages with positive numbers'
    case 'waterfall': return r.x && m && !r.series && r.rows <= 40 ? null : 'Needs up to 40 steps with one number each'
    case 'scatter': return r.nums.length >= 2 ? null : 'Needs two number columns'
    case 'bubble': return r.nums.length >= 3 ? null : 'Needs three number columns (x, y, size)'
    case 'heatmap': case 'matrix': case 'sankey':
      return r.dims.length >= 2 && m ? null : 'Needs two category columns and a number'
    case 'radar':
      if (!(r.x && m) || (r.series && nSeries > MAX_SERIES)) return 'Needs a category column and a number'
      return r.rows >= 3 && r.rows <= 40 ? null : 'Works for 3 to 12 categories'
    case 'histogram': case 'boxplot': return r.nums.length && r.rows >= 5 ? null : 'Needs at least 5 rows of numbers'
    case 'map': return mapColumns(r) ? null : 'Needs a place column (city, state, country…) or latitude/longitude'
  }
  return 'Unknown chart type'
}

export interface ChartOption { type: ChartType; stack?: Stack; label: string; group: string }

export const CHART_OPTIONS: ChartOption[] = [
  { type: 'bar', label: 'Column', group: 'Compare' },
  { type: 'hbar', label: 'Bar', group: 'Compare' },
  { type: 'bar', stack: 'stacked', label: 'Stacked column', group: 'Compare' },
  { type: 'hbar', stack: 'stacked', label: 'Stacked bar', group: 'Compare' },
  { type: 'bar', stack: 'percent', label: '100% stacked column', group: 'Compare' },
  { type: 'hbar', stack: 'percent', label: '100% stacked bar', group: 'Compare' },
  { type: 'line', label: 'Line', group: 'Trend' },
  { type: 'area', label: 'Area', group: 'Trend' },
  { type: 'area', stack: 'stacked', label: 'Stacked area', group: 'Trend' },
  { type: 'waterfall', label: 'Waterfall', group: 'Trend' },
  { type: 'pie', label: 'Pie', group: 'Part of a whole' },
  { type: 'donut', label: 'Donut', group: 'Part of a whole' },
  { type: 'treemap', label: 'Treemap', group: 'Part of a whole' },
  { type: 'funnel', label: 'Funnel', group: 'Part of a whole' },
  { type: 'scatter', label: 'Scatter', group: 'Relationship' },
  { type: 'bubble', label: 'Bubble', group: 'Relationship' },
  { type: 'heatmap', label: 'Heatmap', group: 'Relationship' },
  { type: 'sankey', label: 'Sankey', group: 'Relationship' },
  { type: 'radar', label: 'Radar', group: 'Relationship' },
  { type: 'histogram', label: 'Histogram', group: 'Distribution' },
  { type: 'boxplot', label: 'Box plot', group: 'Distribution' },
  { type: 'map', label: 'Map', group: 'Location' },
  { type: 'matrix', label: 'Matrix', group: 'Tables' },
  { type: 'table', label: 'Table', group: 'Tables' },
]

export const SINGLE_OPTIONS: ChartOption[] = [
  { type: 'kpi', label: 'Number card', group: 'Single value' },
  { type: 'gauge', label: 'Gauge', group: 'Single value' },
]

export function chartLabel(type: ChartType, stack: Stack = 'none'): string {
  const o = [...CHART_OPTIONS, ...SINGLE_OPTIONS].find((x) => x.type === type && (x.stack ?? 'none') === (stack ?? 'none'))
  return o?.label ?? type
}

const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

/**
 * Put category labels in their natural order when they have one (weekdays, months, quarters,
 * numbers); otherwise keep the given order (usually the query's ORDER BY).
 */
export function naturalOrder(keys: string[]): string[] {
  // "Jan 2025", "Sept 2024"...: chronological (year, then month). Sorting these by month name
  // alone would interleave the years.
  const monthYear = keys.map((k) => /^([a-z]{3})[a-z]*\.?\s+(\d{4})$/i.exec(k.trim()))
  if (keys.length && monthYear.every((m) => m && MONTHS.includes(m[1].toLowerCase()))) {
    const at = (i: number) => Number(monthYear[i]![2]) * 12 + MONTHS.indexOf(monthYear[i]![1].toLowerCase())
    return keys.map((k, i) => [k, at(i)] as const).sort((a, b) => a[1] - b[1]).map(([k]) => k)
  }
  const lower = keys.map((k) => k.trim().toLowerCase())
  // Bare names only ("Mon", "January"): anything with more in it isn't a plain weekday/month.
  const rank = (list: string[]) => lower.every((k) => /^[a-z]+\.?$/.test(k) && list.some((x) => k.startsWith(x)))
    ? (k: string) => list.findIndex((x) => k.trim().toLowerCase().startsWith(x)) : null
  const byList = rank(WEEKDAYS) ?? rank(MONTHS)
  if (byList) return [...keys].sort((a, b) => byList(a) - byList(b))
  if (keys.every((k) => k.trim() !== '' && !Number.isNaN(Number(k)))) return [...keys].sort((a, b) => Number(a) - Number(b))
  if (keys.every((k) => /^Q[1-4]\b/i.test(k))) return [...keys].sort()
  return keys
}
