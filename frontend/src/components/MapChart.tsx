import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { Cell, ChartSpec, QueryResult } from '../api/types'
import { deriveRoles, mapColumns } from '../lib/chartFit'
import { formatCategory, formatValue, humanize } from '../lib/format'
import { readChartColors, useTheme } from '../lib/themeContext'
import { useViewClient } from '../lib/viewClient'

interface Point { label: string; lat: number; lon: number; value: number | null; raw: Cell }

/** Bubble map on OpenStreetMap tiles (Leaflet). Place names are geocoded by the backend (cached). */
export function MapChart({ spec, result, onPick }: {
  spec: ChartSpec; result: QueryResult; onPick?: (column: string, value: Cell) => void
}) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layer = useRef<L.LayerGroup | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [legend, setLegend] = useState<{ value: number; radius: number }[] | null>(null)
  const { resolved } = useTheme()
  const client = useViewClient() // the app's API, or a shared link's (cached places only)

  // Coordinates come straight from lat/lon columns, or from geocoding a place column.
  const plan = useMemo(() => planPoints(spec, result), [spec, result])
  const pickColumn = plan.pickColumn
  // Kept in a ref: a new callback each render mustn't redraw the markers (and re-fit the map).
  const pickRef = useRef(onPick)
  useEffect(() => { pickRef.current = onPick })
  const [geocoded, setGeocoded] = useState<Point[] | null>(null)
  const points = plan.direct ?? geocoded

  useEffect(() => {
    if (plan.direct) return
    let cancelled = false
    const { names, rowsFor } = plan
    const run = async () => {
      const coords: Record<string, number[] | null> = {}
      for (let round = 0; round < 40; round++) {
        if (cancelled) return
        setStatus(`Finding ${names.length - Object.keys(coords).length} places on the map…`)
        const known = Object.keys(coords).length
        const res = await client.geocode(names.filter((n) => !(n in coords)))
        Object.assign(coords, res.places)
        if (!res.pending) break
        // The lookup service is busy with another map: wait a moment instead of asking right away.
        if (Object.keys(coords).length === known) await new Promise((r) => setTimeout(r, 1500))
      }
      if (cancelled) return
      const unknown = names.filter((n) => !coords[n])
      setStatus(unknown.length ? `Couldn't place: ${unknown.slice(0, 5).join(', ')}${unknown.length > 5 ? '…' : ''}` : null)
      setGeocoded(rowsFor(coords))
    }
    run().catch((e: Error) => { if (!cancelled) setStatus(`Map lookup failed: ${e.message}`) })
    return () => { cancelled = true }
  }, [plan, client])

  // Create the map once.
  useEffect(() => {
    if (!el.current) return
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, scrollWheelZoom: false })
    map.current = m
    layer.current = L.layerGroup().addTo(m)
    m.setView([20, 0], 2)
    client.config().then((cfg) => L.tileLayer(cfg.mapTiles, { attribution: cfg.mapAttribution, maxZoom: 18 }).addTo(m))
      .catch(() => {})
    const ro = new ResizeObserver(() => m.invalidateSize())
    ro.observe(el.current)
    return () => { ro.disconnect(); m.remove(); map.current = null }
  }, [])

  // Draw markers. With a quantity, a proportional-symbol map: circle AREA tracks the value, so a city with
  // twice the revenue gets twice the ink. Without one (or when every value is the same), small dots.
  useEffect(() => {
    const m = map.current, g = layer.current
    if (!m || !g || !points) return
    g.clearLayers()
    const c = readChartColors()
    const sized = markerScale(points, el.current?.clientWidth ?? 600)
    // Biggest first, so small bubbles are drawn on top and stay hoverable.
    const ordered = sized ? [...points].sort((a, b) => Math.abs(b.value ?? 0) - Math.abs(a.value ?? 0)) : points
    for (const p of ordered) {
      const radius = sized ? sized.radius(p.value) : DOT_RADIUS
      L.circleMarker([p.lat, p.lon], sized
        ? { radius, color: c.surface, weight: 1.5, fillColor: c.series[0], fillOpacity: 0.62 }
        : { radius, color: c.surface, weight: 1.5, fillColor: c.series[0], fillOpacity: 0.95 },
      ).bindTooltip(
        `${p.value === null ? '' : `<strong>${formatValue(p.value, spec)}</strong><br>`}${escapeHtml(p.label)}`,
        { direction: 'top', offset: [0, -radius] },
      ).on('click', () => { if (pickColumn) pickRef.current?.(pickColumn, p.raw) }).addTo(g)
    }
    setLegend(sized ? sized.legend : null)
    if (points.length) m.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon])), { padding: [28, 28], maxZoom: 9 })
  }, [points, spec, resolved, pickColumn])

  return (
    <div className="map-wrap">
      <div ref={el} className={`map ${resolved === 'dark' ? 'map-dark' : ''}`} role="img"
        aria-label={`Map of ${humanize(spec.y ?? 'values')}`} />
      {status && <div className="map-status">{status}</div>}
      {legend && (
        <div className="map-legend" aria-hidden>
          {/* Nested circles sharing a baseline, each with a leader line to its value (classic size legend). */}
          <svg width={legend[0].radius * 2 + 64} height={legendLayout(legend).height}>
            {legendLayout(legend).items.map((l) => (
              <g key={l.value}>
                <circle cx={l.cx} cy={l.cy} r={l.radius} />
                <polyline points={`${l.cx},${l.top} ${l.lineEnd - 3},${l.top} ${l.lineEnd},${l.labelY}`} />
                <text x={l.lineEnd + 3} y={l.labelY} dominantBaseline="middle">{formatValue(l.value, spec, true)}</text>
              </g>
            ))}
          </svg>
        </div>
      )}
    </div>
  )
}

type Row = QueryResult['rows'][number]

const DOT_RADIUS = 4.5
const MIN_RADIUS = 3

/**
 * Bubble sizing for a quantity map, or null when there's no quantity to show (no value column, or every
 * value identical, e.g. one row per store). The largest bubble shrinks as points get denser and the map
 * gets smaller, so busy maps stay readable.
 */
function markerScale(points: { value: number | null }[], width: number) {
  const values = points.map((p) => p.value).filter((v): v is number => v !== null).map(Math.abs)
  if (values.length < 2) return null
  const max = Math.max(...values), min = Math.min(...values)
  if (max <= 0 || max === min) return null
  const density = points.length > 150 ? 0.45 : points.length > 60 ? 0.6 : points.length > 25 ? 0.8 : 1
  const maxR = Math.max(7, Math.min(14, width / 48) * density)
  const radius = (v: number | null) => (v === null ? MIN_RADIUS : Math.max(MIN_RADIUS, maxR * Math.sqrt(Math.abs(v) / max)))
  const nice = niceStep(max / 2)
  const legend = [max, nice].filter((v, i, a) => v > 0 && a.indexOf(v) === i).map((v) => ({ value: v, radius: radius(v) }))
  return { radius, legend }
}

/** Size legend geometry: circles share a baseline; labels keep at least 12px apart even when circles are close. */
function legendLayout(legend: { value: number; radius: number }[]) {
  const R = legend[0].radius, cx = R + 2, base = R * 2 + 3, lineEnd = R * 2 + 10
  let prev = -Infinity
  const items = legend.map((l) => {
    const top = base - l.radius * 2
    const labelY = Math.max(top, prev + 12, 6)
    prev = labelY
    return { ...l, cx, cy: base - l.radius, top, labelY, lineEnd }
  })
  return { items, height: Math.max(base, prev + 7) + 1 }
}

/** A round number near x (1, 2 or 5 x 10^n) for the size legend. */
function niceStep(x: number) {
  const p = 10 ** Math.floor(Math.log10(x))
  const f = x / p
  return (f >= 5 ? 5 : f >= 2 ? 2 : 1) * p
}

/** Work out where each row goes: direct coordinates, or place names to look up. */
function planPoints(spec: ChartSpec, result: QueryResult): {
  direct?: Point[]; names: string[]; rowsFor: (coords: Record<string, number[] | null>) => Point[]
  /** The column a click on a point filters by (its label or place column). */
  pickColumn?: string
} {
  const roles = deriveRoles(result, spec)
  const cols = spec.lat && spec.lon ? { lat: spec.lat, lon: spec.lon } : spec.location ? { location: spec.location } : mapColumns(roles)
  const idx = (n?: string) => (n ? result.columns.indexOf(n) : -1)
  const yName = spec.y ?? roles.measures.find((m) => m !== cols?.lat && m !== cols?.lon)
  const yi = idx(yName), li = idx(spec.label ?? cols?.location ?? roles.dims[0])
  const value = (r: Row) => (typeof r[yi] === 'number' ? (r[yi] as number) : null)
  const label = (r: Row) => (li >= 0 ? formatCategory(r[li]) : '')
  const raw = (r: Row) => (li >= 0 ? r[li] : null)
  const pickColumn = li >= 0 ? result.columns[li] : undefined
  if (cols?.lat && cols.lon) {
    const [la, lo] = [idx(cols.lat), idx(cols.lon)]
    const direct = result.rows.filter((r) => typeof r[la] === 'number' && typeof r[lo] === 'number')
      .map((r) => ({ label: label(r), lat: r[la] as number, lon: r[lo] as number, value: value(r), raw: raw(r) }))
    return { direct, names: [], rowsFor: () => direct, pickColumn }
  }
  const pi = idx(cols?.location)
  const names = [...new Set(result.rows.map((r) => formatCategory(r[pi])).filter(Boolean))]
  return {
    names,
    pickColumn,
    rowsFor: (coords) => result.rows.flatMap((r) => {
      const c = coords[formatCategory(r[pi])]
      return c ? [{ label: label(r), lat: c[0], lon: c[1], value: value(r), raw: raw(r) }] : []
    }),
  }
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)
}
