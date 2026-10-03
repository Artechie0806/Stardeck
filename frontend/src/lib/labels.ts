// Long category labels -> short display labels. Shortened by the AI on the server (cached there and
// here); until it answers, or if it's offline, labels are trimmed with an ellipsis. Full labels
// stay in tooltips and data tables.
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'

export const MAX_LABEL = 18 // fits a tilted axis label (130px at 11-12px text)

const cache = new Map<string, string>()
const inflight = new Map<string, Promise<void>>()

export function trimLabel(label: string, max = MAX_LABEL): string {
  const s = label.replace(/\s+/g, ' ').trim()
  return s.length <= max ? s : `${s.slice(0, max - 1).replace(/[\s\-,/&(]+$/, '')}…`
}

async function fetchShort(labels: string[], max: number) {
  const key = `${max}|${labels.join('\u0000')}`
  if (!inflight.has(key)) {
    inflight.set(key, api.shortenLabels(labels, max)
      .then((res) => { for (const [full, short] of Object.entries(res.labels)) cache.set(`${max}|${full}`, short) })
      .catch(() => { for (const l of labels) cache.set(`${max}|${l}`, trimLabel(l, max)) })
      .finally(() => inflight.delete(key)))
  }
  return inflight.get(key)
}

/** Returns a function mapping a full label to its display label; re-renders once AI labels arrive. */
export function useShortLabels(labels: string[], max = MAX_LABEL): (label: string) => string {
  const long = useMemo(() => [...new Set(labels)].filter((l) => l.length > max), [labels, max])
  const [version, setVersion] = useState(0)

  useEffect(() => {
    const missing = long.filter((l) => !cache.has(`${max}|${l}`))
    if (!missing.length) return
    let live = true
    void fetchShort(missing.slice(0, 100), max).then(() => { if (live) setVersion((v) => v + 1) })
    return () => { live = false }
  }, [long, max])

  return useMemo(() => {
    void version // recompute when AI labels arrive
    return (label: string) => {
      if (label.length <= max) return label
      return cache.get(`${max}|${label}`) ?? trimLabel(label, max)
    }
  }, [version, max])
}
