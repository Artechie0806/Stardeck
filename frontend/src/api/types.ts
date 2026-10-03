// Contracts shared with the backend. The mock layer implements these today;
// the FastAPI endpoints will return the same shapes.

export type ChartType =
  | 'kpi' | 'gauge' | 'line' | 'area' | 'bar' | 'hbar' | 'pie' | 'donut' | 'treemap' | 'funnel' | 'waterfall'
  | 'scatter' | 'bubble' | 'heatmap' | 'matrix' | 'radar' | 'sankey' | 'histogram' | 'boxplot' | 'map' | 'table'
export type Stack = 'none' | 'stacked' | 'percent'
export type ValueFormat = 'number' | 'currency' | 'percent'

/** Declarative chart spec: the LLM picks these fields, Stardeck renders deterministically. */
export interface ChartSpec {
  type: ChartType
  x?: string
  y?: string
  /** Column that splits rows into several series (long format). */
  series?: string
  /** Several number columns plotted side by side (wide format). */
  measures?: string[]
  stack?: Stack
  /** Bubble size column. */
  size?: string
  /** Point / marker label column (scatter, bubble, map). */
  label?: string
  /** Gauge maximum column. */
  target?: string
  /** Map: coordinate columns, or a place-name column to geocode. */
  lat?: string
  lon?: string
  location?: string
  format?: ValueFormat
  currency?: string
  /** KPI only: whether an increase is good news (false for refund rate, churn...). */
  upIsGood?: boolean
}

export type Cell = string | number | null

export interface QueryResult {
  columns: string[]
  rows: Cell[][]
  truncated?: boolean
}

export interface GridPos {
  x: number
  y: number
  w: number
  h: number
}

/** Which date column the dashboard's date filter restricts for this widget. */
export interface DateFilter {
  table: string
  column: string
}

export interface DateRange {
  start: string // YYYY-MM-DD, inclusive
  end: string // YYYY-MM-DD, inclusive
}

export interface Widget {
  id: string
  title: string
  /** Plain-English description of what the widget shows. */
  summary: string
  sql: string
  spec: ChartSpec
  layout: GridPos
  /** Null when the widget ignores the dashboard date filter. */
  dateFilter?: DateFilter | null
  result?: QueryResult
  error?: string
}

/** What the widget engine returns for a plain-English request. */
export interface GeneratedWidget {
  title: string
  summary: string
  sql: string
  spec: ChartSpec
  dateFilter: DateFilter | null
  result: QueryResult
  attempts: number
  notes: string[]
}

export interface QueryRequest {
  sql: string
  kpi?: boolean
  dateFilter?: DateFilter | null
  range?: DateRange
  compare?: DateRange
}

export type DatePreset = 'last_30d' | 'last_90d' | 'this_year' | 'last_year' | 'all'

export interface DashboardSummary {
  id: string
  name: string
  description: string
  sourceId: string
  widgetCount: number
  updatedAt: string
}

export interface Dashboard extends Omit<DashboardSummary, 'widgetCount'> {
  widgets: Widget[]
  datePreset: DatePreset
}

export type DefinitionSource = 'user' | 'chat' | 'ai_suggested'

export interface ColumnDefinition {
  description?: string
  display_name?: string
  unit?: string
  values?: Record<string, string>
  hidden?: boolean
}

export interface ColumnInfo {
  name: string
  type: string
  samples: string[]
  definition: ColumnDefinition | null
  source: DefinitionSource | null
  /** Why the AI may misread this column, if it has no definition yet. */
  flag: string | null
}

export interface TableInfo {
  name: string
  rowCount: number | null
  description: string | null
  foreignKeys: string[]
  isView?: boolean
  /** A Stardeck prepared table (a transformation recipe), not a table in the database. */
  prepared?: boolean
  /** Set when this table couldn't be read (permissions, unsupported types). */
  error?: string | null
  columns: ColumnInfo[]
}

export interface GlossaryTerm {
  term: string
  definition: string
}

export type SourceKind = 'postgres' | 'mysql' | 'sqlite' | 'csv'

export interface DataSource {
  id: string
  name: string
  kind: SourceKind
  /** Human-readable location, never includes credentials. */
  detail: string
  /** Connection settings without the password. */
  config?: Record<string, string | number | string[]>
  databaseDescription: string | null
  glossary: GlossaryTerm[]
  tables: TableInfo[]
  profiledAt?: string | null
  profileError?: string | null
}

export type SourceConfig =
  | { kind: 'postgres'; host: string; port: number; database: string; username: string; password: string; schema: string; sslmode: 'disable' | 'prefer' | 'require' }
  | { kind: 'mysql'; host: string; port: number; database: string; username: string; password: string }
  | { kind: 'sqlite'; path: string }

export interface TestResult {
  ok: boolean
  message: string
  tables: number | null
}

/** A widget built by the engine, before it has an id or a place on a dashboard. */
export interface WidgetDraft {
  title: string
  summary: string
  sql: string
  spec: ChartSpec
  dateFilter: DateFilter | null
  result?: QueryResult
}

/** Something the assistant proposes; nothing changes until the user confirms. */
export type ChatProposal =
  | { kind: 'definition'; table: string; column: string; definition: ColumnDefinition }
  | { kind: 'add_widget'; widget: WidgetDraft }
  | { kind: 'replace_widget'; widgetId: string; widgetTitle: string; widget: WidgetDraft }
  | { kind: 'change_type'; widgetId: string; widgetTitle: string; type: ChartType; stack?: Stack }
  | { kind: 'rename_widget'; widgetId: string; widgetTitle: string; title: string }
  | { kind: 'remove_widget'; widgetId: string; widgetTitle: string }

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  proposal?: ChatProposal
  proposalState?: 'pending' | 'applied' | 'dismissed'
  /** Tappable answers when the assistant asks a clarifying question (or the analyst's follow-ups). */
  options?: string[]
  /** Which agent answered: the dashboard builder or the data analyst. */
  agent?: ChatAgent
  analysis?: Analysis
}

export type ChatAgent = 'builder' | 'analyst'

/** The analyst's working: the queries it ran and the last result, so its numbers can be checked. */
export interface Analysis {
  queries: { sql: string; rows: number }[]
  evidence: QueryResult | null
  /** A chart request the builder can turn into a widget ("Add as chart"). */
  chartRequest: string | null
}

/** Streamed while a dashboard is generated, so progress shows widget by widget. */
export type GenerateEvent =
  | { type: 'plan'; name: string; titles: string[] }
  | { type: 'widget'; index: number; title: string }
  | { type: 'widget_failed'; index: number; reason: string }
  | { type: 'done'; dashboardId: string }
  | { type: 'error'; message: string }

/** A saved dashboard as stored (widgets without results). */
export interface DashboardDef extends Omit<Dashboard, 'widgets'> {
  widgets: Omit<Widget, 'result' | 'error'>[]
}

export interface WidgetPatch {
  title?: string
  summary?: string
  sql?: string
  spec?: ChartSpec
  chartType?: ChartType
  stack?: Stack
  dateFilter?: DateFilter
  clearDateFilter?: boolean
}

export interface Health {
  status: string
  version: string
  llm: {
    base_url: string
    model: string
    reachable: boolean
    model_available?: boolean
    error?: string
  }
}

// ---------- Prepared tables (data transformation) ----------

/** One step of a recipe. `op` picks the step type; other fields depend on it (see lib/steps.ts). */
export interface TransformStep {
  op: string
  [field: string]: unknown
}

export interface PreparedTable {
  name: string
  base: string
  steps: TransformStep[]
  labels: string[]
  columns: string[]
  rowCount: number | null
  error: string | null
  /** "Dashboard: widget" for each widget that reads this table. */
  usedBy: string[]
}

export interface TransformPreview {
  columns: string[]
  rows: Cell[][]
  truncated?: boolean
  sql?: string
  /** Columns before any step, then after each step. */
  stepColumns?: string[][]
  labels: string[]
  error: string | null
  errorStep: number | null
}

export type StepSuggestion =
  | { step: TransformStep; label: string; preview: TransformPreview }
  | { question: string }
