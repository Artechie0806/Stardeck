import type { ChartSpec, Cell } from '../api/types'

const LOCALE = 'en-IN'

/** Format a value for display. `compact` gives 1.2L / 3.4Cr style short forms for tiles and axes. */
export function formatValue(value: Cell, spec: Pick<ChartSpec, 'format' | 'currency'>, compact = false): string {
  if (value === null || value === undefined) return '–'
  if (typeof value !== 'number') return String(value)
  const notation = compact ? 'compact' : 'standard'
  switch (spec.format) {
    case 'currency':
      return new Intl.NumberFormat(LOCALE, {
        style: 'currency', currency: spec.currency ?? 'INR', notation,
        maximumFractionDigits: compact || Math.abs(value) >= 1000 ? (compact ? 1 : 0) : 2,
      }).format(value)
    case 'percent':
      return `${value.toFixed(1)}%`
    default:
      return new Intl.NumberFormat(LOCALE, {
        notation, maximumFractionDigits: compact ? 1 : Number.isInteger(value) ? 0 : 2,
      }).format(value)
  }
}

/** "2025-03" -> "Mar 2025"; other labels pass through. */
export function formatCategory(value: Cell): string {
  if (typeof value === 'string' && /^\d{4}-\d{2}$/.test(value)) {
    const [y, m] = value.split('-').map(Number)
    return new Date(y, m - 1).toLocaleDateString(LOCALE, { month: 'short', year: 'numeric' })
  }
  return value === null ? '–' : String(value)
}

export function humanize(column: string): string {
  const s = column.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function relativeTime(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  return hrs < 24 ? `${hrs} h ago` : new Date(iso).toLocaleDateString(LOCALE)
}
