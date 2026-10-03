import { useSyncExternalStore } from 'react'
import { api } from '../api'
import type { Health } from '../api/types'

export interface AiStatus {
  /** null while the first check is running. */
  online: boolean | null
  health: Health | null
  backendDown: boolean
}

const EVERY_MS = 60_000
let status: AiStatus = { online: null, health: null, backendDown: false }
const listeners = new Set<() => void>()
let timer: number | undefined

async function check(): Promise<void> {
  try {
    const health = await api.getHealth()
    status = { online: health.llm.reachable && health.llm.model_available !== false, health, backendDown: false }
  } catch {
    status = { ...status, backendDown: true }
  }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) {
    void check()
    timer = window.setInterval(() => void check(), EVERY_MS)
  }
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) window.clearInterval(timer)
  }
}

/** Whether the AI is up, checked every minute (one check shared by everything on the page). */
export function useAiStatus(): AiStatus {
  return useSyncExternalStore(subscribe, () => status)
}

/** Check again now, e.g. right after an AI request failed. */
export function recheckAi(): Promise<void> {
  return check()
}
