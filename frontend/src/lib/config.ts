import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AppConfig } from '../api/types'

let cached: Promise<AppConfig> | null = null

/** The workspace's settings and plan (map tiles, financial year start...). Fetched once; `reload`
 *  after changing them. */
export function loadConfig(reload = false): Promise<AppConfig> {
  if (reload || !cached) cached = api.clientConfig()
  return cached
}

/** Forget the cached config (someone else signed in: another workspace, another plan). */
export function resetConfig(): void {
  cached = null
}

export function useAppConfig(): AppConfig | null {
  const [config, setConfig] = useState<AppConfig | null>(null)
  useEffect(() => {
    let live = true
    loadConfig().then((c) => { if (live) setConfig(c) }).catch(() => {})
    return () => { live = false }
  }, [])
  return config
}
