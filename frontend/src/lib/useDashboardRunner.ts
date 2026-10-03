import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { ActiveFilter, Cell, DashboardDef, DatePreset, DateRange, QueryResult, ViewConfig, Widget } from '../api/types'
import { presetRanges, type DateOptions } from './dates'
import { appClient, type ViewClient } from './viewClient'

interface WidgetRun { result?: QueryResult; error?: string; unaffected?: string[] }

const sameValues = (a: Cell[] = [], b: Cell[] = []) => a.length === b.length && a.every((v, i) => v === b[i])

/**
 * Loads a dashboard and runs its widgets with the chosen dates and filters.
 *
 * Results stream in widget by widget; while new ones are on their way the previous results stay up
 * (dimmed). The date range and filter selections are view state: they change what you see, and only
 * "Make default" saves them for everyone. `client` is the app's API, or a shared link's (keep it stable).
 */
export function useDashboardRunner(id: string, client: ViewClient = appClient) {
  const [config, setConfig] = useState<ViewConfig | null>(null)
  const [def, setDef] = useState<DashboardDef | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, WidgetRun>>({})
  const [pending, setPending] = useState<Set<string>>(new Set())
  const [view, setView] = useState<{ preset: DatePreset; custom: DateRange | null } | null>(null)
  const [selections, setSelections] = useState<Record<string, Cell[]>>({})
  const [cross, setCross] = useState<ActiveFilter[]>([])
  const [token, setToken] = useState({ n: 0, fresh: false })
  const run = useRef(0)

  /** (Re)load the definition: widgets, layout, saved filters. The first load also sets the view. */
  useEffect(() => {
    let live = true
    client.config().then((c) => { if (live) setConfig(c) }).catch(() => {})
    return () => { live = false }
  }, [client])

  const reload = useCallback(async () => {
    try {
      const d = await client.getDashboardDef(id)
      setDef(d)
      setView((v) => v ?? { preset: d.datePreset as DatePreset, custom: d.dateRange ?? null })
      setSelections((s) => {
        const next: Record<string, Cell[]> = {}
        for (const f of d.filters ?? []) next[f.id] = f.id in s ? s[f.id] : (f.values ?? [])
        return next
      })
      setToken((t) => ({ n: t.n + 1, fresh: false }))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [id, client])

  useEffect(() => { void reload() }, [reload])

  const dateOpts: DateOptions = useMemo(
    () => ({ fiscalStart: config?.fiscalYearStart ?? 4, custom: view?.custom ?? null }),
    [config?.fiscalYearStart, view?.custom],
  )
  const ranges = useMemo(() => (view ? presetRanges(view.preset, dateOpts) : {}), [view, dateOpts])

  const active = useMemo<ActiveFilter[]>(() => [
    ...(def?.filters ?? []).filter((f) => (selections[f.id] ?? []).length > 0).map((f) => ({
      key: f.id, sourceId: f.sourceId, table: f.table, column: f.column, values: selections[f.id], label: f.label || f.column,
    })),
    ...cross,
  ], [def?.filters, selections, cross])

  // Run every widget whenever the definition, dates or filters change.
  const ready = !!def && !!view && !!config
  const filterKey = JSON.stringify(active)
  useEffect(() => {
    if (!ready || !def) return
    const n = ++run.current
    const ids = new Set(def.widgets.map((w) => w.id))
    setPending(ids)
    void (async () => {
      try {
        for await (const r of client.runDashboard(id, ranges, active, token.fresh)) {
          if (n !== run.current) return
          setResults((prev) => ({ ...prev, [r.id]: { result: r.result, error: r.error, unaffected: r.unaffected } }))
          setPending((prev) => { const next = new Set(prev); next.delete(r.id); return next })
        }
      } catch (e) {
        if (n === run.current) setError((e as Error).message)
      } finally {
        if (n === run.current) setPending(new Set())
      }
    })()
    // `active` and `ranges` are captured through filterKey/ranges; the token forces re-runs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, def, id, ranges, filterKey, token, client])

  const widgets: Widget[] = useMemo(
    () => (def?.widgets ?? []).map((w) => ({ ...w, ...results[w.id] })) as Widget[],
    [def?.widgets, results],
  )

  const dirty = !!def && !!view && (
    view.preset !== def.datePreset
    || (view.preset === 'custom' && (view.custom?.start !== def.dateRange?.start || view.custom?.end !== def.dateRange?.end))
    || (def.filters ?? []).some((f) => !sameValues(selections[f.id], f.values))
  )

  const picked = useMemo(() => Object.fromEntries(cross.map((c) => [c.key.replace(/^x:/, ''), c.value ?? null])), [cross])
  const filterLabels = useMemo(() => Object.fromEntries(active.map((f) => [f.key, f.label])), [active])

  return {
    def, error, setError, widgets, pending, loading: pending.size > 0,
    view: view ?? { preset: 'all' as DatePreset, custom: null }, dateOpts, ranges, active, filterLabels, dirty,
    selections, cross, picked,
    setDate: (preset: DatePreset, custom?: DateRange) => setView((v) => ({ preset, custom: custom ?? v?.custom ?? null })),
    select: (filterId: string, values: Cell[]) => setSelections((s) => ({ ...s, [filterId]: values })),
    /** Click on a chart: filter by it, or clear it when the same thing is clicked again. */
    toggleCross: (f: ActiveFilter) => setCross((c) => {
      const same = c.find((x) => x.key === f.key)
      return same && same.value === f.value ? c.filter((x) => x.key !== f.key) : [...c.filter((x) => x.key !== f.key), f]
    }),
    clearCross: (key: string) => setCross((c) => c.filter((x) => x.key !== key)),
    clearAll: () => { setCross([]); setSelections((s) => Object.fromEntries(Object.keys(s).map((k) => [k, []]))) },
    reload,
    refresh: () => setToken((t) => ({ n: t.n + 1, fresh: true })),
    /** Save the current dates and filter selections as the dashboard's defaults. */
    makeDefault: async () => {
      if (!def || !view) return
      await api.updateDashboard(id, {
        datePreset: view.preset, ...(view.preset === 'custom' && view.custom ? { dateRange: view.custom } : {}),
        filters: (def.filters ?? []).map((f) => ({ ...f, values: selections[f.id] ?? [] })),
      })
      await reload()
    },
  }
}
