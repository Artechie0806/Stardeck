import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { api } from '../api'
import type { ChartSpec, QueryResult } from '../api/types'
import { deriveRoles, mapColumns } from '../lib/chartFit'
import { formatCategory, formatValue, humanize } from '../lib/format'
import { readChartColors, useTheme } from '../lib/themeContext'

interface Point { label: string; lat: number; lon: number; value: number | null }

let configPromise: Promise<{ mapTiles: string; mapAttribution: string; geocoding: boolean }> | null = null
const clientConfig = () => (configPromise ??= api.clientConfig())

/** Bubble map on OpenStreetMap tiles (Leaflet). Place names are geocoded by the backend (cached). */
export function MapChart({ spec, result }: { spec: ChartSpec; result: QueryResult }) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const { resolved } = useTheme()

  // Coordinates come straight from lat/lon columns, or from geocoding a place column.
  const plan = useMemo(() => planPoints(spec, result), [spec, result])
  const [geocoded, setGeocoded] = useState<Point[] | null>(null)
  const points = plan.direct ?? geocoded

  useEffect(() => {
    if (plan.direct) return
    let cancelled = false
    const { names, rowsFor } = plan
    const run = async () => {
      const coords: Record<string, number[] | null> = {}
      for (let round = 0; round < 10; round++) {
        if (cancelled) return
        setStatus(`Finding ${names.length - Object.keys(coords).length} places on the map…`)
        const res = await api.geocode(names.filter((n) => !(n in coords)))
        Object.assign(coords, res.places)
        if (!res.pending) break
      }
      if (cancelled) return
      const unknown = names.filter((n) => !coords[n])
      setStatus(unknown.length ? `Couldn't place: ${unknown.slice(0, 5).join(', ')}${unknown.length > 5 ? '…' : ''}` : null)
      setGeocoded(rowsFor(coords))
    }
    run().catch((e: Error) => { if (!cancelled) setStatus(`Map lookup failed: ${e.message}`) })
    return () => { cancelled = true }
  }, [plan])

  // Create the map once.
  useEffect(() => {
    if (!el.current) return
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: false })
    map.current = m
    layer.current = L.layerGroup().addTo(m)
    m.setView([20, 0], 2)
    clientConfig().then((cfg) => L.tileLayer(cfg.mapTiles, { attribution: cfg.mapAttribution, maxZoom: 18 }).addTo(m))
      .catch(() => {})
    const ro = new ResizeObserver(() => m.invalidateSize())
    ro.observe(el.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
  }, [])

  // Draw bubbles: area-scaled radius, series-1 fill, 2px surface ring, tooltip with label + value.
  useEffect(() => {
    const m = map.current, g = layer.current
    if (!m || !g || !points) return
    g.clearLayers()
    const c = readChartColors()
    const max = Math.max(...points.map((p) => Math.abs(p.value ?? 0)), 1)
    for (const p of points) {
      const radius = p.value === null ? 6 : 6 + 22 * Math.sqrt(Math.abs(p.value) / max)
      L.circleMarker([p.lat, p.lon], {
        radius, color: c.surface, weight: 2, fillColor: c.series[0], fillOpacity: 0.7,
      }).bindTooltip(`<strong>${p.value === null ? '' : formatValue(p.value, spec)}</strong><br>${escapeHtml(p.label)}`,
        { direction: 'top' }).addTo(g)
    }
    if (points.length) m.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon])), { padding: [28, 28], maxZoom: 9 })
  }, [points, spec, resolved])

  return (
    <div className="map-wrap">
      <div ref={el} className={`map ${resolved === 'dark' ? 'map-dark' : ''}`} role="img"
        aria-label={`Map of ${humanize(spec.y ?? 'values')}`} />
      {status && <div className="map-status">{status}</div>}
    </div>
  )
}

type Row = QueryResult['rows'][number]

/** Work out where each row goes: direct coordinates, or place names to look up. */
function planPoints(spec: ChartSpec, result: QueryResult): {
  direct?: Point[]; names: string[]; rowsFor: (coords: Record<string, number[] | null>) => Point[]
} {
  const roles = deriveRoles(result, spec)
  const cols = spec.lat && spec.lon ? { lat: spec.lat, lon: spec.lon } : spec.location ? { location: spec.location } : mapColumns(roles)
  const idx = (n?: string) => (n ? result.columns.indexOf(n) : -1)
  const yName = spec.y ?? roles.measures.find((m) => m !== cols?.lat && m !== cols?.lon)
  const yi = idx(yName), li = idx(spec.label ?? cols?.location ?? roles.dims[0])
  const value = (r: Row) => (typeof r[yi] === 'number' ? (r[yi] as number) : null)
  const label = (r: Row) => (li >= 0 ? formatCategory(r[li]) : '')
  if (cols?.lat && cols.lon) {
    const [la, lo] = [idx(cols.lat), idx(cols.lon)]
    const direct = result.rows.filter((r) => typeof r[la] === 'number' && typeof r[lo] === 'number')
      .map((r) => ({ label: label(r), lat: r[la] as number, lon: r[lo] as number, value: value(r) }))
    return { direct, names: [], rowsFor: () => direct }
  }
  const pi = idx(cols?.location)
  const names = [...new Set(result.rows.map((r) => formatCategory(r[pi])).filter(Boolean))]
  return {
    names,
    rowsFor: (coords) => result.rows.flatMap((r) => {
      const c = coords[formatCategory(r[pi])]
      return c ? [{ label: label(r), lat: c[0], lon: c[1], value: value(r) }] : []
    }),
  }
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
}
