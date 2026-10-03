// Print-ready dashboards (Export PDF). The screen grid places widgets absolutely, which browsers can't
// paginate: a chart that crosses a page edge gets cut in two. For printing, widgets are laid out in
// normal flow as horizontal bands, each kept on one page, at exactly the printable width so nothing
// is resized while the print dialog renders.
import { createContext, useContext } from 'react'
import type { Widget } from '../api/types'

/** Inside a print-ready layout: charts skip entry animations, so a PDF never catches one halfway. */
export const PrintContext = createContext(false)
export const usePrintLayout = () => useContext(PrintContext)

/** A4 landscape with 10 mm margins (@page in index.css): 277 × 190 mm at 96 px per inch. */
export const SHEET_WIDTH = 1047
const PAGE_HEIGHT = 690 // ~718 px printable, with some slack
const MAX_ROWS = 12 // taller widgets (long tables) are shortened to fit a page
/** Grid rows are shorter on paper than on screen (40 px, 16 px gaps): two rows of charts fit on a page. */
export const PRINT_ROW = 32
export const PRINT_GAP = 12

interface Box { x: number; y: number; w: number; h: number }
export interface PrintItem extends Box { widget: Widget }
export interface PrintBand { items: PrintItem[]; rows: number; rowHeight: number }

const collides = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

/** Moves every widget up as far as it goes, the way the screen grid does (react-grid-layout's vertical compaction). */
function compact(items: PrintItem[]): PrintItem[] {
  const placed: PrintItem[] = []
  for (const it of [...items].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const l = { ...it, y: Math.min(it.y, Math.max(0, ...placed.map((p) => p.y + p.h))) }
    while (l.y > 0 && !placed.some((p) => collides(p, l))) l.y--
    for (let hit = placed.find((p) => collides(p, l)); hit; hit = placed.find((p) => collides(p, l))) l.y = hit.y + hit.h
    placed.push(l)
  }
  return placed
}

/** Widgets grouped into bands: a new band starts where nothing above reaches down any further. A band
 *  taller than a page gets shorter rows, so it still fits. */
export function printBands(widgets: Widget[], rowHeight: number, gap: number): PrintBand[] {
  const items = compact(widgets.map((widget) => ({
    widget, x: widget.layout.x, y: widget.layout.y, w: widget.layout.w, h: Math.min(widget.layout.h, MAX_ROWS),
  })))
  const bands: { top: number; bottom: number; items: PrintItem[] }[] = []
  for (const it of items.sort((a, b) => a.y - b.y || a.x - b.x)) {
    const band = bands.at(-1)
    if (band && it.y < band.bottom) {
      band.items.push(it)
      band.bottom = Math.max(band.bottom, it.y + it.h)
    } else {
      bands.push({ top: it.y, bottom: it.y + it.h, items: [it] })
    }
  }
  return bands.map(({ top, bottom, items: inBand }) => {
    const rows = bottom - top
    const fits = rows * rowHeight + (rows - 1) * gap <= PAGE_HEIGHT
    return {
      rows,
      rowHeight: fits ? rowHeight : Math.max(18, Math.floor((PAGE_HEIGHT - (rows - 1) * gap) / rows)),
      items: inBand.map((it) => ({ ...it, y: it.y - top })),
    }
  })
}
