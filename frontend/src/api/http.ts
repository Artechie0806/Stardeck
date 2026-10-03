// Real Stardeck API calls. Errors surface FastAPI's `detail` message, which is written for users.
import type {
  AccessRule, ActiveFilter, Analysis, AppConfig, AuthStatus, Cell, ChatAgent, ChatProposal, ColumnDefinition, ColumnValues,
  CurrentUser, DashboardDef, DashboardSummary, DataSource, DatePreset, DateRange, FilterDef, GeneratedWidget, GenerateEvent,
  GlossaryTerm, GridPos, Health, InviteInfo, Measure, QueryRequest, QueryResult, Role, ShareLink, SourceConfig, TestResult, Usage,
  UserAccount, Widget,
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
  if (!res.ok) throw await errorFrom(res)
  return res.status === 204 ? (undefined as T) : res.json()
}

/** The server's `detail` message for a failed response. */
export async function errorFrom(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') message = body.detail
    else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join('; ')
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(message, res.status)
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })
const enc = encodeURIComponent

export const httpApi = {
  getHealth: () => request<Health>('/health'),

  clientConfig: () => request<AppConfig>('/config'),
  usage: () => request<Usage>('/usage'),
  updateSettings: (patch: { fiscalYearStart?: number }) => request<AppConfig>('/settings', json('PUT', patch)),
  geocode: (places: string[]) =>
    request<{ places: Record<string, number[] | null>; pending: number }>('/geocode', json('POST', { places })),

  shortenLabels: (labels: string[], maxChars: number) =>
    request<{ labels: Record<string, string>; ai: boolean }>('/labels/shorten', json('POST', { labels, maxChars })),

  authStatus: () => request<AuthStatus>('/auth/status'),
  login: (password: string, username?: string) =>
    request<{ authenticated: boolean; user: CurrentUser | null }>('/auth/login', json('POST', { username, password })),
  inviteInfo: (token: string) => request<InviteInfo>(`/auth/invite/${enc(token)}`),
  /** Accept an invitation (name + password) or set a new password; signs in. */
  acceptInvite: (token: string, name: string, password: string) =>
    request<{ authenticated: boolean; user: CurrentUser }>(`/auth/invite/${enc(token)}`, json('POST', { name, password })),
  changePassword: (current: string, next: string) =>
    request<{ ok: boolean }>('/auth/password', json('POST', { current, new: next })),
  /** Turn on sign-in by creating the first admin account. */
  setupAuth: (username: string, name: string, password: string) =>
    request<{ authenticated: boolean; user: CurrentUser }>('/auth/setup', json('POST', { username, name, password })),
  logout: () => request<{ authenticated: boolean }>('/auth/logout', { method: 'POST' }),

  // ----- people (admins) -----
  listUsers: () => request<UserAccount[]>('/users'),
  createUser: (u: { username: string; name: string; password: string; role: Role; rules: AccessRule[] }) =>
    request<UserAccount>('/users', json('POST', u)),
  updateUser: (id: string, patch: { name?: string; password?: string; role?: Role; rules?: AccessRule[]; disabled?: boolean }) =>
    request<UserAccount>(`/users/${enc(id)}`, json('PATCH', patch)),
  deleteUser: (id: string) => request<void>(`/users/${enc(id)}`, { method: 'DELETE' }),
  ruleCoverage: (sourceId: string, table: string) =>
    request<{ restricted: string[]; unrestricted: string[] }>(`/sources/${enc(sourceId)}/coverage?table=${enc(table)}`),

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

  // ----- dashboards -----

  listDashboards: () => request<DashboardSummary[]>('/dashboards'),
  getDashboardDef: (id: string) => request<DashboardDef>(`/dashboards/${enc(id)}`),
  createDashboard: (name: string, sourceIds: string[], description = '') =>
    request<DashboardDef>('/dashboards', json('POST', { name, description, sourceIds })),
  updateDashboard: (id: string, patch: {
    name?: string; description?: string; datePreset?: DatePreset; dateRange?: DateRange; sourceIds?: string[]; filters?: FilterDef[]
  }) => request<DashboardDef>(`/dashboards/${enc(id)}`, json('PATCH', patch)),
  deleteDashboard: (id: string) => request<void>(`/dashboards/${enc(id)}`, { method: 'DELETE' }),
  saveLayout: (id: string, layout: Record<string, GridPos>) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/layout`, json('PUT', layout)),
  addWidget: (id: string, w: WidgetDraft) => request<DashboardDef>(`/dashboards/${enc(id)}/widgets`, json('POST', {
    sourceId: w.sourceId, title: w.title, summary: w.summary, sql: w.sql, spec: w.spec, dateFilter: w.dateFilter,
  })),
  updateWidget: (id: string, widgetId: string, patch: WidgetPatch) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/widgets/${enc(widgetId)}`, json('PATCH', patch)),
  removeWidget: (id: string, widgetId: string) =>
    request<DashboardDef>(`/dashboards/${enc(id)}/widgets/${enc(widgetId)}`, { method: 'DELETE' }),

  /** Run every widget with a date range and filters, yielding each result as soon as it's ready. */
  async *runDashboard(id: string, range: { range?: DateRange; compare?: DateRange }, filters: ActiveFilter[], fresh = false):
    AsyncGenerator<{ id: string; result?: QueryResult; error?: string; unaffected?: string[] }> {
    const res = await fetch(`/api/dashboards/${enc(id)}/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start: range.range?.start, end: range.range?.end, compareStart: range.compare?.start,
        compareEnd: range.compare?.end, fresh, filters, stream: true,
      }),
    })
    if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    if (!res.ok || !res.body) {
      let message = `Couldn't run the dashboard (${res.status})`
      try { message = (await res.json()).detail ?? message } catch { /* not JSON */ }
      throw new ApiError(message, res.status)
    }
    for await (const line of ndjson(res.body)) {
      const ev = line as { id?: string; done?: boolean; result?: QueryResult; error?: string; unaffected?: string[] }
      if (ev.id) yield ev as { id: string }
    }
  },

  /** Public, read-only links to a dashboard (editors). */
  listShares: (id: string) => request<ShareLink[]>(`/dashboards/${enc(id)}/shares`),
  createShare: (id: string, expiresInDays: number | null) =>
    request<ShareLink>(`/dashboards/${enc(id)}/shares`, json('POST', { expiresInDays })),
  revokeShare: (id: string, token: string) =>
    request<void>(`/dashboards/${enc(id)}/shares/${enc(token)}`, { method: 'DELETE' }),

  /** What clicking `value` in a widget's `column` filters (a cross-filter for the whole dashboard). */
  crossFilter: (id: string, widgetId: string, column: string, value: Cell) =>
    request<ActiveFilter>(`/dashboards/${enc(id)}/crossfilter`, json('POST', { widgetId, column, value })),
  /** A filter's choices, as the signed-in person may see them. */
  filterValues: (id: string, sourceId: string, table: string, column: string, q = '') =>
    request<ColumnValues>(`/dashboards/${enc(id)}/filter-values?source_id=${enc(sourceId)}&table=${enc(table)}`
      + `&column=${enc(column)}&q=${enc(q)}`),

  /** Run one widget's (possibly edited) SQL with the dashboard's range, without saving. Throws on failure. */
  previewWidget: (sourceId: string, w: Pick<Widget, 'sql' | 'spec' | 'dateFilter'>, range: { range?: DateRange; compare?: DateRange }) =>
    httpApi.runQuery(sourceId, {
      sql: w.sql, kpi: w.spec.type === 'kpi', dateFilter: w.dateFilter,
      range: w.dateFilter ? range.range : undefined, compare: w.dateFilter ? range.compare : undefined,
    }),

  /** AI dashboard generation over one or more sources; yields progress events streamed as NDJSON. */
  async *generateDashboard(sourceIds: string[], goal: string): AsyncGenerator<GenerateEvent> {
    const res = await fetch('/api/dashboards/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sourceIds, goal }),
    })
    if (!res.ok || !res.body) throw await errorFrom(res)
    for await (const ev of ndjson(res.body)) {
      if ((ev as { type?: string }).type !== 'ping') yield ev as GenerateEvent // pings only keep the stream open
    }
  },

  /** `pending` = the request the assistant last asked a question about (sent back with the answer). */
  chat: (id: string, message: string, history: { role: string; text: string }[],
    range: { range?: DateRange; compare?: DateRange }, filters: ActiveFilter[], pending?: string,
    agent?: ChatAgent, basisSql?: string, basisSourceId?: string) =>
    request<{ text: string; proposal?: ChatProposal; options?: string[]; pending?: string; agent?: ChatAgent;
      analysis?: Analysis }>(
      `/dashboards/${enc(id)}/chat`, json('POST', {
        message, history, pending, agent, basisSql, basisSourceId, filters, start: range.range?.start,
        end: range.range?.end, compareStart: range.compare?.start, compareEnd: range.compare?.end,
      })),

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

  // ----- measures and relationships -----
  checkMeasure: (sourceId: string, measure: Measure) =>
    request<{ value: Cell }>(`/sources/${enc(sourceId)}/measures/check`, json('POST', { measure })),
  saveMeasure: (sourceId: string, measure: Measure, originalName?: string) =>
    request<DataSource>(`/sources/${enc(sourceId)}/measures/${enc(measure.name)}`, json('PUT', { measure, originalName })),
  deleteMeasure: (sourceId: string, name: string) =>
    request<DataSource>(`/sources/${enc(sourceId)}/measures/${enc(name)}`, { method: 'DELETE' }),
  suggestMeasures: (sourceId: string) =>
    request<{ measures: { measure: Measure; value: Cell }[] }>(`/sources/${enc(sourceId)}/measures/suggest`, { method: 'POST' }),
  addRelationship: (sourceId: string, from: string, to: string) =>
    request<DataSource>(`/sources/${enc(sourceId)}/relationships`, json('POST', { from, to })),
  toggleRelationship: (sourceId: string, key: string, ignored: boolean) =>
    request<DataSource>(`/sources/${enc(sourceId)}/relationships`, json('PUT', { key, ignored })),
  /** A column's values for setting up filters and security rules (editors and admins). */
  sourceValues: (sourceId: string, table: string, column: string, q = '') =>
    request<ColumnValues>(`/sources/${enc(sourceId)}/values?table=${enc(table)}&column=${enc(column)}&q=${enc(q)}`),

  definitionsExportUrl: (sourceId: string) => `/api/sources/${enc(sourceId)}/definitions.yaml`,
  importDefinitions: (sourceId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DataSource>(`/sources/${enc(sourceId)}/definitions.yaml`, { method: 'PUT', body: form })
  },
}

export async function* ndjson(body: NonNullable<Response['body']>): AsyncGenerator<unknown> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) if (line.trim()) yield JSON.parse(line)
  }
  if (buffer.trim()) yield JSON.parse(buffer)
}
