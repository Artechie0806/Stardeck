import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Allowance, Feature, PlanInfo, Usage } from '../api/types'
import { useAppConfig } from './config'

/** The workspace's plan. Until it has loaded, nothing is shown as locked (the server enforces anyway). */
export function usePlan(): { plan: PlanInfo | null; loaded: boolean; has: (f: Feature) => boolean; upgradeUrl: string | null } {
  const config = useAppConfig()
  const plan = config?.plan ?? null
  return {
    plan,
    loaded: config !== null,
    has: (f: Feature) => !plan || plan.features.includes(f),
    upgradeUrl: config?.upgradeUrl ?? null,
  }
}

/** How much of the plan is in use; `reload` after adding or removing something. */
export function useUsage(): { usage: Usage | null; reload: () => void } {
  const [usage, setUsage] = useState<Usage | null>(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    api.usage().then((u) => { if (live) setUsage(u) }).catch(() => {})
    return () => { live = false }
  }, [n])
  return { usage, reload: useCallback(() => setN((x) => x + 1), []) }
}

export const isFull = (a: Allowance | undefined | null): boolean => !!a && a.limit !== null && a.used >= a.limit

/** How many are left (null = no limit). */
export const left = (a: Allowance | undefined | null): number | null =>
  !a || a.limit === null ? null : Math.max(0, a.limit - a.used)
