import type { DatePreset, DateRange } from '../api/types'

export const PRESETS: { value: DatePreset; label: string }[] = [
  { value: 'last_30d', label: 'Last 30 days' },
  { value: 'last_90d', label: 'Last 90 days' },
  { value: 'this_year', label: 'This year' },
  { value: 'last_year', label: 'Last year' },
  { value: 'all', label: 'All time' },
]

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)

/** The range a preset covers and the equally long period before it (for KPI deltas). */
export function presetRanges(preset: DatePreset, today = new Date()): { range?: DateRange; compare?: DateRange } {
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const y = t.getFullYear()
  switch (preset) {
    case 'last_30d':
    case 'last_90d': {
      const days = preset === 'last_30d' ? 30 : 90
      const start = addDays(t, -(days - 1))
      return {
        range: { start: iso(start), end: iso(t) },
        compare: { start: iso(addDays(start, -days)), end: iso(addDays(start, -1)) },
      }
    }
    case 'this_year':
      // Year to date vs the same stretch of last year.
      return {
        range: { start: `${y}-01-01`, end: iso(t) },
        compare: { start: `${y - 1}-01-01`, end: iso(new Date(y - 1, t.getMonth(), t.getDate())) },
      }
    case 'last_year':
      return {
        range: { start: `${y - 1}-01-01`, end: `${y - 1}-12-31` },
        compare: { start: `${y - 2}-01-01`, end: `${y - 2}-12-31` },
      }
    default:
      return {}
  }
}

/** "vs 2024", "vs previous 90 days"... for KPI tiles. */
export function compareLabel(preset: DatePreset, today = new Date()): string | null {
  switch (preset) {
    case 'last_30d': return 'vs previous 30 days'
    case 'last_90d': return 'vs previous 90 days'
    case 'this_year': return `vs same period ${today.getFullYear() - 1}`
    case 'last_year': return `vs ${today.getFullYear() - 2}`
    default: return null
  }
}
