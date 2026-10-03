// Real Stardeck API calls. Errors surface FastAPI's `detail` message, which is written for users.
import { presetRanges } from '../lib/dates'
import type {
  Analysis, ChatAgent, ChatProposal, ColumnDefinition, Dashboard, DashboardDef, DashboardSummary, DataSource, DatePreset, GeneratedWidget,
  GenerateEvent, GlossaryTerm, GridPos, Health, QueryRequest, QueryResult, SourceConfig, TestResult, Widget,
  WidgetDraft, WidgetPatch, PreparedTable, StepSuggestion, TransformPreview, TransformStep,
} from './types'

export const UNAUTHORIZED_EVENT = 'stardeck:unauthorized'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT)) // the auth gate shows the sign-in screen
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, res.status)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })
const enc = encodeURIComponent

export const httpApi = {
  getHealth: () => request<Health>('/health'),

  clientConfig: () => request<{ mapTiles: string; mapAttribution: string; geocoding: boolean }>('/config'),
  geocode: (places: string[]) =>
    request<{ places: Record<string, number[] | null>; pending: number }>('/geocode', json('POST', { places })),

  shortenLabels: (labels: string[], maxChars: number) =>
    request<{ labels: Record<string, string>; ai: boolean }>('/labels/shorten', json('POST', { labels, maxChars })),

  authStatus: () => request<{ required: boolean; authenticated: boolean }>('/auth/status'),
  login: (password: string) => request<{ authenticated: boolean }>('/auth/login', json('POST', { password })),
  logout: () => request<{ authenticated: boolean }>('/auth/logout', { method: 'POST' }),

  listSources: () => request<DataSource[]>('/sources'),
  getSource: (id: string) => request<DataSource>(`/sources/${enc(id)}`),
  testConnection: (config: SourceConfig) => request<TestResult>('/sources/test', json('POST', { name: '', config })),
  createSource: (name: string, config: SourceConfig) => request<DataSource>('/sources', json('POST', { name, config })),
  updateSource: (id: string, name: string, config: SourceConfig) =>
    request<DataSource>(`/sources/${enc(id)}`, json('PUT', { name, config })),
  uploadSource: (name: string, files: File[]) => {
    const form = new FormData()
    form.append('name', name)
    for (const f of files) form.append('files', f)
    return request<DataSource>('/sources/upload', { method: 'POST', body: form })
  },
  deleteSource: (id: string) => request<void>(`/sources/${enc(id)}`, { method: 'DELETE' }),
  refreshSource: (id: string) => request<DataSource>(`/sources/${enc(id)}/refresh`, { method: 'POST' }),

  saveColumnDefinition: (
    sourceId: string, table: string, column: string,
    definition: ColumnDefinition | null, source: 'user' | 'chat' = 'user',
  ) => request<DataSource>(
    `/sources/${enc(sourceId)}/columns/${enc(table)}/${enc(column)}`, json('PUT', { definition, source }),
  ),
  saveTableDescription: (sourceId: string, table: string, description: string) =>
    request<DataSource>(`/sources/${enc(sourceId)}/tables/${enc(table)}`, json('PUT', { description })),
  saveDatabaseContext: (sourceId: string, description: string) =>
    request<DataSource>(`/sources/${enc(sourceId)}/context`, json('PUT', { description })),
  saveGlossary: (sourceId: string, glossary: GlossaryTerm[]) =>
    request<DataSource>(`/sources/${enc(sourceId)}/context`, json('PUT', { glossary })),
  suggestDefinitions: (sourceId: string) =>
    request<{ suggested: number; source: DataSource }>(`/sources/${enc(sourceId)}/suggest`, { method: 'POST' }),
  generateWidget: (sourceId: string, text: string) =>
    request<GeneratedWidget>(`/sources/${enc(sourceId)}/widgets/generate`, json('POST', { request: text })),
  runQuery: (sourceId: string, body: QueryRequest) =>
    request<QueryResult>(`/sources/${enc(sourceId)}/query`, json('POST', body)),

  // ----- dashboards (M3) -----

  listDashboards: () => request<DashboardSummary[]>('/dashboards'),
  getDashboardDef: (id: string) => request<DashboardDef>(`/dashboards/${enc(id)}`),
  updateDashboard: (id: string, patch: { name?: string; description?: string; datePreset?: DatePreset }) =>
    request<DashboardDef>(`/dashboards/${enc(id)}`, json('PATCH', patch)),
  deleteDashboard: (id: string) => request<void>(`/dashboards/${enc(id)}`, { method: 'DELETE' }),
  saveLayout: (id: string, layout: Record<string, GridPos>) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/layout`, json('PUT', layout)),
  addWidget: (id: string, w: WidgetDraft) => request<DashboardDef>(`/dashboards/${enc(id)}/widgets`, json('POST', {
    title: w.title, summary: w.summary, sql: w.sql, spec: w.spec, dateFilter: w.dateFilter,
  })),
  updateWidget: (id: string, widgetId: string, patch: WidgetPatch) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/widgets/${enc(widgetId)}`, json('PATCH', patch)),
  removeWidget: (id: string, widgetId: string) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/widgets/${enc(widgetId)}`, { method: 'DELETE' }),

  /** Run every widget with a date preset. Values: {result} or {error}. */
  runDashboard: (id: string, preset: DatePreset, fresh = false) => {
    const { range, compare } = presetRanges(preset)
    return request<Record<string, { result?: QueryResult; error?: string }>>(`/dashboards/${enc(id)}/run`, json('POST', {
      start: range?.start, end: range?.end, compareStart: compare?.start, compareEnd: compare?.end, fresh,
    }))
  },

  /** Load a dashboard and run all of its widgets with its saved date preset. */
  async getDashboard(id: string, fresh = false): Promise<Dashboard> {
    const def = await httpApi.getDashboardDef(id)
    const preset = def.datePreset as DatePreset
    const results = await httpApi.runDashboard(id, preset, fresh)
    const widgets: Widget[] = def.widgets.map((w) => ({ ...w, ...results[w.id] }))
    return { ...def, datePreset: preset, widgets }
  },

  /** Run one widget's (possibly edited) SQL with a preset, without saving. Throws on failure. */
  previewWidget: (sourceId: string, w: Pick<Widget, 'sql' | 'spec' | 'dateFilter'>, preset: DatePreset) => {
    const { range, compare } = presetRanges(preset)
    return httpApi.runQuery(sourceId, {
      sql: w.sql, kpi: w.spec.type === 'kpi', dateFilter: w.dateFilter,
      range: w.dateFilter ? range : undefined, compare: w.dateFilter ? compare : undefined,
    })
  },

  /** AI dashboard generation; yields progress events streamed as NDJSON. */
  async *generateDashboard(sourceId: string, goal: string): AsyncGenerator<GenerateEvent> {
    const res = await fetch('/api/dashboards/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sourceId, goal }),
    })
    if (!res.ok || !res.body) throw new ApiError(`Generation failed (${res.status})`, res.status)
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) if (line.trim()) yield JSON.parse(line) as GenerateEvent
    }
    if (buffer.trim()) yield JSON.parse(buffer) as GenerateEvent
  },

  /** `pending` = the request the assistant last asked a question about (sent back with the answer). */
  /** `agent` skips the router when a button already knows who should answer (e.g. "Add as chart"). */
  chat: (id: string, message: string, history: { role: string; text: string }[], preset: DatePreset, pending?: string,
    agent?: ChatAgent, basisSql?: string) => {
    const { range, compare } = presetRanges(preset)
    return request<{ text: string; proposal?: ChatProposal; options?: string[]; pending?: string; agent?: ChatAgent;
      analysis?: Analysis }>(
      `/dashboards/${enc(id)}/chat`, json('POST', {
        message, history, pending, agent, basisSql, start: range?.start, end: range?.end, compareStart: compare?.start, compareEnd: compare?.end,
      }))
  },

  // ----- prepared tables (data transformation) -----

  listTransforms: (sourceId: string) => request<PreparedTable[]>(`/sources/${enc(sourceId)}/transforms`),
  previewTransform: (sourceId: string, base: string, steps: TransformStep[], opts: { upto?: number; editing?: string } = {}) =>
    request<TransformPreview>(`/sources/${enc(sourceId)}/transforms/preview`, json('POST', { base, steps, ...opts })),
  saveTransform: (sourceId: string, name: string, base: string, steps: TransformStep[], originalName?: string) =>
    request<PreparedTable>(`/sources/${enc(sourceId)}/transforms/${enc(name)}`, json('PUT', { base, steps, originalName })),
  deleteTransform: (sourceId: string, name: string) =>
    request<void>(`/sources/${enc(sourceId)}/transforms/${enc(name)}`, { method: 'DELETE' }),
  distinctValues: (sourceId: string, base: string, steps: TransformStep[], upto: number, column: string, editing?: string) =>
    request<{ values: string[]; more: boolean }>(`/sources/${enc(sourceId)}/transforms/distinct`, json('POST', { base, steps, upto, column, editing })),
  suggestStep: (sourceId: string, base: string, steps: TransformStep[], requestText: string, editing?: string) =>
    request<StepSuggestion>(`/sources/${enc(sourceId)}/transforms/suggest`, json('POST', { base, steps, request: requestText, editing })),

  definitionsExportUrl: (sourceId: string) => `/api/sources/${enc(sourceId)}/definitions.yaml`,
  importDefinitions: (sourceId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DataSource>(`/sources/${enc(sourceId)}/definitions.yaml`, { method: 'PUT', body: form })
  },
}
