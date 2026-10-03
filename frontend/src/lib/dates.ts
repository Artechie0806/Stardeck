import type { DatePreset, DateRange } from '../api/types'

/** What a preset needs to be resolved: the financial year's first month (4 = April) and, for "custom", the range. */
export interface DateOptions {
  fiscalStart: number
  custom?: DateRange | null
}

export const DEFAULT_DATE_OPTIONS: DateOptions = { fiscalStart: 4 }

export const PRESET_GROUPS: { label: string; items: { value: DatePreset; label: string }[] }[] = [
  {
    label: 'Recent',
    items: [
      { value: 'last_7d', label: 'Last 7 days' },
      { value: 'last_30d', label: 'Last 30 days' },
      { value: 'last_90d', label: 'Last 90 days' },
      { value: 'last_12m', label: 'Last 12 months' },
    ],
  },
  {
    label: 'Calendar',
    items: [
      { value: 'this_month', label: 'This month' },
      { value: 'last_month', label: 'Last month' },
      { value: 'this_quarter', label: 'This quarter' },
      { value: 'last_quarter', label: 'Last quarter' },
      { value: 'this_year', label: 'This year' },
      { value: 'last_year', label: 'Last year' },
    ],
  },
  {
    label: 'Financial year',
    items: [
      { value: 'this_fy', label: 'This financial year' },
      { value: 'last_fy', label: 'Last financial year' },
    ],
  },
  { label: '', items: [{ value: 'all', label: 'All time' }] },
]

export const PRESETS: { value: DatePreset; label: string }[] = [
  ...PRESET_GROUPS.flatMap((g) => g.items),
  { value: 'custom', label: 'Custom range' },
]

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const day = (y: number, m: number, d: number) => new Date(y, m, d) // m is 0-based; JS normalises overflow
const addDays = (d: Date, n: number) => day(d.getFullYear(), d.getMonth(), d.getDate() + n)
const parse = (s: string) => { const [y, m, d] = s.split('-').map(Number); return day(y, m - 1, d) }
const daysBetween = (a: Date, b: Date) => Math.round((b.getTime() - a.getTime()) / 86_400_000)

/** First month (0-based) and year of the quarter containing `t`, with quarters aligned to the financial year. */
function quarterStart(t: Date, fiscalStart: number): Date {
  const offset = (t.getMonth() - (fiscalStart - 1) + 12) % 12
  return day(t.getFullYear(), t.getMonth() - (offset % 3), 1)
}

/** First day of the financial year containing `t`. */
export function fyStart(t: Date, fiscalStart: number): Date {
  const m0 = fiscalStart - 1
  return day(t.getMonth() >= m0 ? t.getFullYear() : t.getFullYear() - 1, m0, 1)
}

/** "FY 2026-27" (or "2026" when the financial year is the calendar year). */
export function fyLabel(start: Date, fiscalStart: number): string {
  const y = start.getFullYear()
  return fiscalStart === 1 ? String(y) : `FY ${y}-${String((y + 1) % 100).padStart(2, '0')}`
}

/** Same stretch, shifted back: `months` months, keeping day-of-month (clamped to month ends). */
function shiftMonths(d: Date, months: number): Date {
  const target = day(d.getFullYear(), d.getMonth() - months, 1)
  const last = day(target.getFullYear(), target.getMonth() + 1, 0).getDate()
  return day(target.getFullYear(), target.getMonth(), Math.min(d.getDate(), last))
}

function window(start: Date, end: Date, back: number): { range: DateRange; compare: DateRange } {
  return {
    range: { start: iso(start), end: iso(end) },
    compare: { start: iso(shiftMonths(start, back)), end: iso(shiftMonths(end, back)) },
  }
}

/** The range a preset covers and the period KPIs compare it with. */
export function presetRanges(preset: DatePreset, opts: DateOptions = DEFAULT_DATE_OPTIONS, today = new Date()):
  { range?: DateRange; compare?: DateRange } {
  const t = day(today.getFullYear(), today.getMonth(), today.getDate())
  const fs = opts.fiscalStart || 4
  switch (preset) {
    case 'last_7d':
    case 'last_30d':
    case 'last_90d': {
      const days = preset === 'last_7d' ? 7 : preset === 'last_30d' ? 30 : 90
      const start = addDays(t, -(days - 1))
      return {
        range: { start: iso(start), end: iso(t) },
        compare: { start: iso(addDays(start, -days)), end: iso(addDays(start, -1)) },
      }
    }
    case 'last_12m': {
      const start = addDays(shiftMonths(t, 12), 1)
      return { range: { start: iso(start), end: iso(t) }, compare: { start: iso(shiftMonths(start, 12)), end: iso(shiftMonths(t, 12)) } }
    }
    case 'this_month': // month to date vs the same days of last month
      return window(day(t.getFullYear(), t.getMonth(), 1), t, 1)
    case 'last_month': {
      const start = day(t.getFullYear(), t.getMonth() - 1, 1)
      const end = day(t.getFullYear(), t.getMonth(), 0)
      return { range: { start: iso(start), end: iso(end) },
        compare: { start: iso(day(start.getFullYear(), start.getMonth() - 1, 1)), end: iso(day(start.getFullYear(), start.getMonth(), 0)) } }
    }
    case 'this_quarter': // quarter to date vs the same days of last quarter
      return window(quarterStart(t, fs), t, 3)
    case 'last_quarter': {
      const start = day(quarterStart(t, fs).getFullYear(), quarterStart(t, fs).getMonth() - 3, 1)
      const end = addDays(quarterStart(t, fs), -1)
      return { range: { start: iso(start), end: iso(end) },
        compare: { start: iso(day(start.getFullYear(), start.getMonth() - 3, 1)), end: iso(addDays(start, -1)) } }
    }
    case 'this_year': // year to date vs the same stretch of last year
      return window(day(t.getFullYear(), 0, 1), t, 12)
    case 'last_year':
      return window(day(t.getFullYear() - 1, 0, 1), day(t.getFullYear() - 1, 11, 31), 12)
    case 'this_fy':
      return window(fyStart(t, fs), t, 12)
    case 'last_fy': {
      const start = fyStart(day(t.getFullYear() - 1, t.getMonth(), 1), fs)
      return window(start, addDays(day(start.getFullYear() + 1, start.getMonth(), 1), -1), 12)
    }
    case 'custom': {
      if (!opts.custom) return {}
      const start = parse(opts.custom.start), end = parse(opts.custom.end)
      const len = daysBetween(start, end) + 1
      return { range: { ...opts.custom }, compare: { start: iso(addDays(start, -len)), end: iso(addDays(start, -1)) } }
    }
    default:
      return {}
  }
}

const SHORT = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })

/** "1 Apr 2026 – 3 Oct 2026". */
export function formatRange(r?: DateRange): string {
  return r ? `${SHORT.format(parse(r.start))} – ${SHORT.format(parse(r.end))}` : 'All dates'
}

/** Short name for the selected period, e.g. "This financial year" or "FY 2026-27". */
export function presetLabel(preset: DatePreset, opts: DateOptions = DEFAULT_DATE_OPTIONS, today = new Date()): string {
  if (preset === 'custom') return opts.custom ? formatRange(opts.custom) : 'Custom range'
  if (preset === 'this_fy' || preset === 'last_fy') {
    const r = presetRanges(preset, opts, today).range
    return r ? fyLabel(parse(r.start), opts.fiscalStart) : ''
  }
  return PRESETS.find((p) => p.value === preset)?.label ?? preset
}

/** "vs same days last month", "vs FY 2025-26"... for KPI tiles. */
export function compareLabel(preset: DatePreset, opts: DateOptions = DEFAULT_DATE_OPTIONS, today = new Date()): string | null {
  const y = today.getFullYear()
  switch (preset) {
    case 'last_7d': return 'vs previous 7 days'
    case 'last_30d': return 'vs previous 30 days'
    case 'last_90d': return 'vs previous 90 days'
    case 'last_12m': return 'vs the 12 months before'
    case 'this_month': return 'vs same days last month'
    case 'last_month': return 'vs the month before'
    case 'this_quarter': return 'vs same days last quarter'
    case 'last_quarter': return 'vs the quarter before'
    case 'this_year': return `vs same period ${y - 1}`
    case 'last_year': return `vs ${y - 2}`
    case 'this_fy':
    case 'last_fy': {
      const c = presetRanges(preset, opts, today).compare
      return c ? `vs ${preset === 'this_fy' ? 'same period ' : ''}${fyLabel(parse(c.start), opts.fiscalStart)}` : null
    }
    case 'custom': return 'vs the period before'
    default: return null
  }
}
