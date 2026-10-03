import { CalendarDays } from 'lucide-react'
import type { DatePreset } from '../api/types'
import { PRESETS } from '../lib/dates'

/** One row above the grid; scopes every date-aware widget below it. */
export function FilterBar({ value, onChange }: { value: DatePreset; onChange: (p: DatePreset) => void }) {
  return (
    <div className="filter-bar">
      <span className="filter-label"><CalendarDays size={15} aria-hidden /> Date</span>
      <div className="segmented" role="radiogroup" aria-label="Date range">
        {PRESETS.map((p) => (
          <button key={p.value} role="radio" aria-checked={value === p.value}
            className={value === p.value ? 'selected' : ''} onClick={() => onChange(p.value)}>
            {p.label}
          </button>
        ))}
      </div>
    </div>
  )
}
