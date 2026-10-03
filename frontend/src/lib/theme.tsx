import { useEffect, useState, type ReactNode } from 'react'
import { ThemeContext, type Resolved, type ThemeChoice } from './themeContext'

const STORAGE_KEY = 'stardeck.theme'
const media = () => window.matchMedia('(prefers-color-scheme: dark)')

function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

/**
 * Put the choice on <html> right away. Doing it in an effect was too late: React runs children's effects
 * first, so charts re-read the colour tokens while the old theme was still applied (fixed only by a reload).
 */
function applyChoice(choice: ThemeChoice) {
  const root = document.documentElement
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(() => {
    const c = readChoice()
    applyChoice(c)
    return c
  })
  const setChoice = (c: ThemeChoice) => {
    applyChoice(c)
    setChoiceState(c)
  }
  const [systemDark, setSystemDark] = useState(() => media().matches)

  useEffect(() => {
    const m = media()
    const onChange = () => setSystemDark(m.matches)
    m.addEventListener('change', onChange)
    return () => m.removeEventListener('change', onChange)
  }, [])

  const resolved: Resolved = choice === 'system' ? (systemDark ? 'dark' : 'light') : choice

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, choice)
    } catch {
      /* storage unavailable: theme still applies for this session */
    }
  }, [choice])

  return (
    <ThemeContext.Provider value={{ choice, resolved, setChoice }}>{children}</ThemeContext.Provider>
  )
}
