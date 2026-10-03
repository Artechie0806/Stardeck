import { useMemo } from 'react'
import ReactGridLayout, { useContainerWidth, type Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import type { ChartType, DatePreset, GridPos, QueryResult, Stack, Widget } from '../api/types'
import { WidgetCard } from './WidgetCard'

const ROW_HEIGHT = 40
const MARGIN = 16
// Measured on the grid itself, so a docked assistant on a laptop still gets the 12-column layout.
const SINGLE_COLUMN_BELOW = 560

interface Props {
  widgets: Widget[]
  preset: DatePreset
  editing: boolean
  readOnly?: boolean
  onLayoutChange: (layout: Record<string, GridPos>) => void
  onRemove: (id: string) => void
  onChangeType: (id: string, type: ChartType, stack: Stack) => void
  onRunSql: (w: Widget, sql: string) => Promise<QueryResult>
  onSaveSql: (id: string, sql: string) => Promise<void>
  onAsk?: (text: string) => void
}

export function DashboardGrid({ widgets, preset, editing, readOnly = false, onLayoutChange, onRemove, onChangeType, onRunSql, onSaveSql, onAsk }: Props) {
  const { width, containerRef, mounted } = useContainerWidth()
  const layout = useMemo<Layout>(
    () => widgets.map((w) => ({
      i: w.id, ...w.layout,
      minW: w.spec.type === 'kpi' ? 2 : 3,
      minH: w.spec.type === 'kpi' ? 3 : 5,
    })),
    [widgets],
  )

  const card = (w: Widget) => (
    <WidgetCard widget={w} preset={preset} editing={editing} readOnly={readOnly}
      onRemove={() => onRemove(w.id)} onChangeType={(t, st) => onChangeType(w.id, t, st)}
      onRunSql={(sql) => onRunSql(w, sql)} onSaveSql={(sql) => onSaveSql(w.id, sql)} onAsk={onAsk} />
  )

  // Narrow screens: a simple stack in reading order (top-to-bottom, left-to-right). KPIs size to their
  // content and sit two to a row; charts keep a readable height.
  if (mounted && width < SINGLE_COLUMN_BELOW) {
    const ordered = [...widgets].sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x)
    return (
      <div ref={containerRef} className="grid-stack">
        {ordered.map((w) => (
          <div key={w.id} className={w.spec.type === 'kpi' ? 'stack-kpi' : 'stack-chart'}
            style={w.spec.type === 'kpi' ? undefined : { height: Math.max(280, Math.min(w.layout.h * ROW_HEIGHT + (w.layout.h - 1) * MARGIN, 420)) }}>
            {card(w)}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div ref={containerRef} className={editing ? 'grid editing' : 'grid'}>
      {mounted && (
        <ReactGridLayout
          layout={layout}
          width={width}
          gridConfig={{ cols: 12, rowHeight: ROW_HEIGHT, margin: [MARGIN, MARGIN], containerPadding: [0, 0] }}
          dragConfig={{ enabled: editing, handle: '.widget-drag', cancel: 'button' }}
          resizeConfig={{ enabled: editing, handles: ['se'] }}
          onLayoutChange={(next) => {
            const out: Record<string, GridPos> = {}
            for (const { i, x, y, w, h } of next) out[i] = { x, y, w, h }
            onLayoutChange(out)
          }}
        >
          {widgets.map((w) => <div key={w.id}>{card(w)}</div>)}
        </ReactGridLayout>
      )}
    </div>
  )
}
