// What a dashboard page needs from the server. Signed in, that's the app's API; on a shared link
// (/s/<token>) it's the link's public API: no SQL, only the dashboard's saved filters and its own
// charts' clicks, no AI. Charts deep in the page (maps, short labels) find the client in context.
import { createContext, useContext } from 'react'
import { api } from '../api'
import { errorFrom, ndjson } from '../api/http'
import type {
  ActiveFilter, Cell, ColumnValues, DashboardDef, DateRange, FilterDef, QueryResult, SharedDashboard, ViewConfig,
} from '../api/types'
import { loadConfig } from './config'

export type RunEvent = { id: string; result?: QueryResult; error?: string; unaffected?: string[] }
type Ranges = { range?: DateRange; compare?: DateRange }

export interface ViewClient {
  /** Caches (e.g. short labels) are kept per client. */
  key: string
  config(): Promise<ViewConfig>
  getDashboardDef(id: string): Promise<DashboardDef>
  runDashboard(id: string, ranges: Ranges, filters: ActiveFilter[], fresh?: boolean): AsyncGenerator<RunEvent>
  crossFilter(id: string, widgetId: string, column: string, value: Cell): Promise<ActiveFilter>
  filterValues(id: string, filter: FilterDef, q?: string): Promise<ColumnValues>
  geocode(places: string[]): Promise<{ places: Record<string, number[] | null>; pending: number }>
  shortenLabels(labels: string[], maxChars: number): Promise<{ labels: Record<string, string>; ai: boolean }>
}

/** The signed-in app. */
export const appClient: ViewClient = {
  key: 'app',
  config: () => loadConfig(),
  getDashboardDef: (id) => api.getDashboardDef(id),
  runDashboard: (id, ranges, filters, fresh) => api.runDashboard(id, ranges, filters, fresh),
  crossFilter: (id, widgetId, column, value) => api.crossFilter(id, widgetId, column, value),
  filterValues: (id, f, q) => api.filterValues(id, f.sourceId, f.table, f.column, q),
  geocode: (places) => api.geocode(places),
  shortenLabels: (labels, maxChars) => api.shortenLabels(labels, maxChars),
}

async function publicRequest<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!res.ok) throw await errorFrom(res)
  return res.json()
}

export interface PublicClient extends ViewClient {
  /** The dashboard with what the page around it needs (badge, demo); fetched once. */
  shared(): Promise<SharedDashboard>
}

/** A shared link: anyone can open it, without signing in. */
export function publicClient(token: string): PublicClient {
  const base = `/api/public/s/${encodeURIComponent(token)}`
  let loaded: Promise<SharedDashboard> | null = null
  const shared = () => (loaded ??= publicRequest<SharedDashboard>(base))
  return {
    key: `s:${token}`,
    shared,
    config: async () => (await shared()).config,
    getDashboardDef: () => shared(),
    async *runDashboard(_id, ranges, filters) {
      // Saved filters go by id with their values; clicks as the chart and column, traced by the server.
      const res = await fetch(`${base}/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start: ranges.range?.start, end: ranges.range?.end, compareStart: ranges.compare?.start, compareEnd: ranges.compare?.end,
          filters: filters.filter((f) => !f.key.startsWith('x:')).map((f) => ({ id: f.key, values: f.values ?? [] })),
          picks: filters.filter((f) => f.key.startsWith('x:'))
            .map((f) => ({ widgetId: f.key.slice(2), column: f.pick ?? f.column, value: f.value ?? null })),
        }),
      })
      if (!res.ok || !res.body) throw await errorFrom(res)
      for await (const line of ndjson(res.body)) {
        const ev = line as Partial<RunEvent>
        if (ev.id) yield ev as RunEvent
      }
    },
    crossFilter: (_id, widgetId, column, value) => publicRequest(`${base}/crossfilter`, { widgetId, column, value }),
    filterValues: (_id, f, q = '') => publicRequest(`${base}/filter-values?filter=${encodeURIComponent(f.id)}&q=${encodeURIComponent(q)}`),
    geocode: (places) => publicRequest(`${base}/geocode`, { places }),
    shortenLabels: (labels, maxChars) => publicRequest(`${base}/labels`, { labels, maxChars }),
  }
}

export const ViewClientContext = createContext<ViewClient>(appClient)
export const useViewClient = () => useContext(ViewClientContext)
