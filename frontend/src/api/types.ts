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
  /** The data source this widget queries (older widgets: the dashboard's main source). */
  sourceId?: string
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
  /** Keys of active dashboard filters that don't reach this widget. */
  unaffected?: string[]
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

export type DatePreset =
  | 'last_7d' | 'last_30d' | 'last_90d' | 'last_12m'
  | 'this_month' | 'last_month' | 'this_quarter' | 'last_quarter'
  | 'this_year' | 'last_year' | 'this_fy' | 'last_fy'
  | 'all' | 'custom'

export interface DashboardSummary {
  id: string
  name: string
  description: string
  sourceId: string
  sourceIds: string[]
  widgetCount: number
  updatedAt: string
}

/** A filter (slicer) saved on a dashboard. `values` is its default selection (empty = all). */
export interface FilterDef {
  id: string
  sourceId: string
  table: string
  column: string
  label: string
  values: Cell[]
}

/** A filter in effect while viewing: a slicer's selection, or a cross-filter from clicking a chart. */
export interface ActiveFilter {
  key: string
  sourceId: string
  table: string
  column?: string | null
  values?: Cell[]
  expr?: string | null
  value?: Cell
  label: string
  /** Shared links: the chart column that was clicked (the server traces it again on every run). */
  pick?: string
}

export interface Dashboard extends Omit<DashboardSummary, 'widgetCount'> {
  widgets: Widget[]
  datePreset: DatePreset
  /** The default range when datePreset is "custom". */
  dateRange?: DateRange | null
  filters: FilterDef[]
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

export type SourceKind =
  | 'postgres' | 'mysql' | 'redshift' | 'sqlserver' | 'oracle' | 'clickhouse' | 'trino'
  | 'databricks' | 'snowflake' | 'bigquery' | 'sqlite' | 'duckdb' | 'csv'

export interface Measure {
  name: string
  label?: string | null
  description?: string | null
  table: string
  expression: string
  format: 'number' | 'currency' | 'percent'
  currency?: string | null
  up_is_good?: boolean
  source?: DefinitionSource
}

export interface Relationship {
  table: string
  column: string
  refTable: string
  refColumn: string
  origin: 'database' | 'inferred' | 'user'
  key: string
  ignored: boolean
}

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
  measures: Measure[]
  relationships: Relationship[]
}

type SslMode = 'disable' | 'prefer' | 'require'

/** Connection settings sent to the server. Secrets (password, token, key) may be empty when editing. */
export type SourceConfig =
  | { kind: 'postgres'; host: string; port: number; database: string; username: string; password: string; schema: string; sslmode: SslMode }
  | { kind: 'mysql'; host: string; port: number; database: string; username: string; password: string }
  | { kind: 'redshift'; host: string; port: number; database: string; username: string; password: string; schema: string; sslmode: SslMode }
  | { kind: 'sqlserver'; host: string; port: number; database: string; username: string; password: string; schema: string; encryption: 'off' | 'request' | 'require' }
  | { kind: 'oracle'; host: string; port: number; service_name: string; username: string; password: string; schema: string }
  | { kind: 'clickhouse'; host: string; port: number; database: string; username: string; password: string; secure: boolean }
  | { kind: 'trino'; host: string; port: number; catalog: string; schema: string; username: string; password: string; https: boolean }
  | { kind: 'databricks'; host: string; http_path: string; token: string; catalog: string; schema: string }
  | { kind: 'snowflake'; account: string; username: string; password: string; private_key: string; warehouse: string; database: string; schema: string; role: string }
  | { kind: 'bigquery'; project: string; dataset: string; credentials_json: string; location: string; max_gb_billed: number }
  | { kind: 'sqlite'; path: string }
  | { kind: 'duckdb'; path: string }

export interface TestResult {
  ok: boolean
  message: string
  tables: number | null
}

/** A widget built by the engine, before it has an id or a place on a dashboard. */
export interface WidgetDraft {
  sourceId?: string
  title: string
  summary: string
  sql: string
  spec: ChartSpec
  dateFilter: DateFilter | null
  result?: QueryResult
}

/** Something the assistant proposes; nothing changes until the user confirms. */
export type ChatProposal =
  | { kind: 'definition'; sourceId?: string; table: string; column: string; definition: ColumnDefinition }
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
  /** `dates`: the dashboard's period, applied to a query that didn't set dates itself. */
  queries: { sql: string; rows: number; dates?: { table: string; column: string; start: string; end: string } }[]
  evidence: QueryResult | null
  /** A chart request the builder can turn into a widget ("Add as chart"). */
  chartRequest: string | null
  /** The source the evidence came from (the chart is built there). */
  sourceId?: string | null
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

// ---------- People and settings ----------

export type Role = 'viewer' | 'editor' | 'admin'
export type AuthMode = 'open' | 'password' | 'users'

export interface CurrentUser {
  id: string
  username: string
  name: string
  role: Role
}

export interface WorkspaceInfo {
  id: string
  name: string
  plan: 'community' | 'enterprise' | 'self_hosted'
}

export interface AuthStatus {
  mode: AuthMode
  required: boolean
  authenticated: boolean
  user: CurrentUser | null
  /** Hosted mode: people sign in with their email, into their own workspace. */
  hosted?: boolean
  workspace?: WorkspaceInfo | null
}

/** An invitation or password-reset link. */
export interface InviteInfo {
  kind: 'invite' | 'reset'
  email: string
  workspace: string
}

/** Viewers only see rows of `table` where `column` is one of `values` (and rows linked to them). */
export interface AccessRule {
  sourceId: string
  table: string
  column: string
  values: Cell[]
}

export interface UserAccount {
  id: string
  username: string
  name: string
  role: Role
  rules: AccessRule[]
  disabled: boolean
  createdAt: string
  lastLoginAt: string | null
}

export type Feature = 'people' | 'measures' | 'relationships' | 'prepared_tables' | 'multi_source'

/** What the workspace's plan includes (null limit = no limit; sample data never counts). */
export interface PlanInfo {
  id: 'community' | 'enterprise' | 'self_hosted'
  name: string
  features: Feature[]
  /** "Made with Stardeck" on public links. */
  badge: boolean
  limits: {
    sources: number | null
    dashboards: number | null
    sourcesPerDashboard: number
    uploadMb: number | null
    aiGenerationsPerDay: number | null
    aiMessagesPerDay: number | null
  }
}

export interface Allowance { used: number; limit: number | null }
export interface Usage {
  sources: Allowance
  dashboards: Allowance
  /** Today's AI use (resets at midnight, India time). */
  ai: { generation: Allowance; assistant: Allowance; resetsAt: string }
}

export interface AppConfig extends ViewConfig {
  hosted?: boolean
  plan?: PlanInfo
  /** Where "Upgrade" links go. */
  upgradeUrl?: string | null
  /** Where public share links live (hosted: a separate host); null = this app's address. */
  shareUrl?: string | null
}

/** What showing a dashboard needs from the server's settings. */
export interface ViewConfig {
  mapTiles: string
  mapAttribution: string
  geocoding: boolean
  /** Month the financial year starts (4 = April). */
  fiscalYearStart: number
}

/** A public, read-only link to a dashboard. */
export interface ShareLink {
  token: string
  createdAt: string
  expiresAt: string | null
  views: number
  lastViewedAt: string | null
  active: boolean
}

/** A dashboard opened through a share link: no SQL, plus what the page needs. */
export interface SharedDashboard extends DashboardDef {
  share: { badge: boolean; demo: boolean; siteUrl: string | null }
  config: ViewConfig
}

export interface ColumnValues {
  values: Cell[]
  more: boolean
  /** Documented meanings of coded values, e.g. {"1": "Completed"}. */
  labels: Record<string, string>
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
