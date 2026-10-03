import { createContext, useContext } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
export type Resolved = 'light' | 'dark'

export const ThemeContext = createContext<{
  choice: ThemeChoice
  resolved: Resolved
  setChoice: (c: ThemeChoice) => void
}>({ choice: 'system', resolved: 'light', setChoice: () => {} })

export const useTheme = () => useContext(ThemeContext)

export interface ChartColors {
  surface: string
  text: string
  textSecondary: string
  muted: string
  grid: string
  axis: string
  series: string[]
  /** Sequential ramp, low -> high magnitude (light/dark steps differ). */
  seq: string[]
  good: string
  bad: string
}

/** Chart colors come from CSS tokens so charts follow the same light/dark steps as the UI. */
export function readChartColors(): ChartColors {
  const css = getComputedStyle(document.documentElement)
  const v = (name: string) => css.getPropertyValue(name).trim()
  return {
    surface: v('--surface-solid'), // charts need an opaque colour for tooltips and segment gaps
    text: v('--text'),
    textSecondary: v('--text-secondary'),
    muted: v('--muted'),
    grid: v('--grid'),
    axis: v('--axis'),
    series: Array.from({ length: 8 }, (_, i) => v(`--series-${i + 1}`)),
    seq: Array.from({ length: 5 }, (_, i) => v(`--seq-${i + 1}`)),
    good: v('--delta-good'),
    bad: v('--delta-bad'),
  }
}
