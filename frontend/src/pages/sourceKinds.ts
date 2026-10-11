import { Database, FileSpreadsheet, HardDrive, Server, Warehouse } from 'lucide-react'
import type { SourceConfig, SourceKind } from '../api'

export type KindGroup = 'files' | 'databases' | 'warehouses'

export interface KindInfo {
  label: string
  icon: typeof Database
  blurb: string
  group: KindGroup
  /** A file on the Stardeck server rather than a server address (not offered when hosted). */
  file?: boolean
  /** The connection itself is opened read-only (otherwise only the SELECT check and the account protect the data). */
  readOnly?: boolean
}

export const KINDS: Record<SourceKind, KindInfo> = {
  csv: { label: 'CSV / Excel upload', icon: FileSpreadsheet, group: 'files', readOnly: true,
    blurb: 'Upload .csv or Excel files. The quickest way to start.' },
  sqlite: { label: 'SQLite file', icon: HardDrive, group: 'files', file: true, readOnly: true,
    blurb: 'A .sqlite or .db file stored on the Stardeck server.' },
  duckdb: { label: 'DuckDB file', icon: HardDrive, group: 'files', file: true, readOnly: true,
    blurb: 'A .duckdb file stored on the Stardeck server.' },
  postgres: { label: 'PostgreSQL', icon: Database, group: 'databases', readOnly: true,
    blurb: 'Also Supabase, Neon, TimescaleDB, CockroachDB and other PostgreSQL-compatible servers.' },
  mysql: { label: 'MySQL / MariaDB', icon: Database, group: 'databases', readOnly: true,
    blurb: 'Also Amazon Aurora MySQL, PlanetScale and TiDB.' },
  sqlserver: { label: 'SQL Server', icon: Database, group: 'databases',
    blurb: 'Microsoft SQL Server and Azure SQL Database.' },
  oracle: { label: 'Oracle', icon: Database, group: 'databases', readOnly: true,
    blurb: 'Oracle Database 12c or later.' },
  snowflake: { label: 'Snowflake', icon: Warehouse, group: 'warehouses',
    blurb: 'Sign in with a password or a key pair.' },
  bigquery: { label: 'Google BigQuery', icon: Warehouse, group: 'warehouses',
    blurb: 'A dataset, read with a service account key.' },
  redshift: { label: 'Amazon Redshift', icon: Warehouse, group: 'warehouses',
    blurb: 'A Redshift cluster or Redshift Serverless workgroup.' },
  databricks: { label: 'Databricks', icon: Warehouse, group: 'warehouses',
    blurb: 'A SQL warehouse, with a personal access token.' },
  clickhouse: { label: 'ClickHouse', icon: Server, group: 'warehouses', readOnly: true,
    blurb: 'ClickHouse server or ClickHouse Cloud, over HTTP(S).' },
  trino: { label: 'Trino', icon: Server, group: 'warehouses',
    blurb: 'Trino or Starburst: query any of its catalogs.' },
}

export const GROUPS: { id: KindGroup; label: string; kinds: SourceKind[] }[] = [
  { id: 'files', label: 'Files', kinds: ['csv', 'sqlite', 'duckdb'] },
  { id: 'databases', label: 'Databases', kinds: ['postgres', 'mysql', 'sqlserver', 'oracle'] },
  { id: 'warehouses', label: 'Warehouses and query engines', kinds: ['snowflake', 'bigquery', 'redshift', 'databricks', 'clickhouse', 'trino'] },
]

export interface FieldSpec {
  key: string
  label: string
  section: 'server' | 'signin' | 'advanced'
  placeholder?: string
  required?: boolean
  /** Stored encrypted; shown as a password box. */
  secret?: boolean
  /** A long secret pasted whole (a key file). */
  multiline?: boolean
  kind?: 'text' | 'number' | 'select' | 'checkbox'
  options?: [string, string][]
  hint?: string
  /** Shares a row with the next field (host + port). */
  narrow?: boolean
}

const SSL: [string, string][] = [['prefer', 'Prefer (use it when the server offers it)'], ['require', 'Require'], ['disable', 'Disable']]
const host = (placeholder = 'db.example.com'): FieldSpec => ({ key: 'host', label: 'Host', section: 'server', required: true, placeholder })
const port = (p: number): FieldSpec => ({ key: 'port', label: 'Port', section: 'server', kind: 'number', placeholder: String(p), narrow: true })
const database = (placeholder = 'sales'): FieldSpec => ({ key: 'database', label: 'Database name', section: 'server', required: true, placeholder })
const user = (placeholder = 'stardeck_readonly', required = true): FieldSpec => ({ key: 'username', label: 'Username', section: 'signin', required, placeholder })
const password: FieldSpec = { key: 'password', label: 'Password', section: 'signin', secret: true }

/** Form fields and their defaults for every kind that isn't an upload or a file path. */
export const FORMS: Partial<Record<SourceKind, { fields: FieldSpec[]; defaults: Record<string, string> }>> = {
  postgres: {
    fields: [host(), port(5432), database(), user(), password,
      { key: 'schema', label: 'Schema', section: 'advanced', placeholder: 'public' },
      { key: 'sslmode', label: 'SSL', section: 'advanced', kind: 'select', options: SSL }],
    defaults: { schema: 'public', sslmode: 'prefer' },
  },
  mysql: { fields: [host(), port(3306), database(), user(), password], defaults: {} },
  redshift: {
    fields: [host('cluster.abc123.eu-west-1.redshift.amazonaws.com'), port(5439), database('dev'), user(), password,
      { key: 'schema', label: 'Schema', section: 'advanced', placeholder: 'public' },
      { key: 'sslmode', label: 'SSL', section: 'advanced', kind: 'select', options: SSL }],
    defaults: { schema: 'public', sslmode: 'require' },
  },
  sqlserver: {
    fields: [host('sql.example.com or myserver.database.windows.net'), port(1433), database(), user(), password,
      { key: 'schema', label: 'Schema', section: 'advanced', placeholder: 'dbo' },
      { key: 'encryption', label: 'Encryption', section: 'advanced', kind: 'select',
        options: [['request', 'When the server supports it'], ['require', 'Require (Azure SQL needs this)'], ['off', 'Off']] }],
    defaults: { schema: 'dbo', encryption: 'request' },
  },
  oracle: {
    fields: [host(), port(1521),
      { key: 'service_name', label: 'Service name', section: 'server', required: true, placeholder: 'ORCLPDB1' },
      user(), password,
      { key: 'schema', label: 'Schema', section: 'advanced', placeholder: 'The user’s own',
        hint: 'Show another schema’s tables, e.g. SALES.' }],
    defaults: {},
  },
  clickhouse: {
    fields: [host('abc123.eu-west-1.aws.clickhouse.cloud'), port(8123),
      { key: 'database', label: 'Database name', section: 'server', placeholder: 'default' },
      user('default', false), password,
      { key: 'secure', label: 'Use HTTPS (port 8443; ClickHouse Cloud)', section: 'advanced', kind: 'checkbox' }],
    defaults: { database: 'default', username: 'default', secure: 'false' },
  },
  trino: {
    fields: [host('trino.example.com'), port(8080),
      { key: 'catalog', label: 'Catalog', section: 'server', required: true, placeholder: 'hive' },
      { key: 'schema', label: 'Schema', section: 'server', required: true, placeholder: 'sales' },
      user('stardeck'), { ...password, hint: 'Only sent over HTTPS.' },
      { key: 'https', label: 'Use HTTPS', section: 'advanced', kind: 'checkbox' }],
    defaults: { https: 'false' },
  },
  databricks: {
    fields: [host('dbc-a1b2c3d4-e5f6.cloud.databricks.com'),
      { key: 'http_path', label: 'HTTP path', section: 'server', required: true, placeholder: '/sql/1.0/warehouses/abc123',
        hint: 'From the SQL warehouse’s Connection details.' },
      { key: 'catalog', label: 'Catalog', section: 'server', placeholder: 'main' },
      { key: 'schema', label: 'Schema', section: 'server', placeholder: 'default' },
      { key: 'token', label: 'Access token', section: 'signin', secret: true, required: true }],
    defaults: { schema: 'default' },
  },
  snowflake: {
    fields: [
      { key: 'account', label: 'Account identifier', section: 'server', required: true, placeholder: 'myorg-myaccount' },
      { key: 'database', label: 'Database', section: 'server', required: true, placeholder: 'ANALYTICS' },
      { key: 'schema', label: 'Schema', section: 'server', placeholder: 'PUBLIC' },
      { key: 'warehouse', label: 'Warehouse', section: 'server', placeholder: 'COMPUTE_WH' },
      user('STARDECK_READONLY'), password,
      { key: 'private_key', label: 'Private key (instead of a password)', section: 'advanced', secret: true, multiline: true,
        placeholder: '-----BEGIN PRIVATE KEY-----', hint: 'An unencrypted PKCS#8 key for key-pair sign-in.' },
      { key: 'role', label: 'Role', section: 'advanced', placeholder: 'The user’s default' }],
    defaults: { schema: 'PUBLIC' },
  },
  bigquery: {
    fields: [
      { key: 'project', label: 'Project ID', section: 'server', required: true, placeholder: 'my-project-123' },
      { key: 'dataset', label: 'Dataset', section: 'server', required: true, placeholder: 'sales' },
      { key: 'credentials_json', label: 'Service account key (JSON)', section: 'signin', secret: true, multiline: true,
        placeholder: '{ "type": "service_account", … }',
        hint: 'Give it the BigQuery Data Viewer and Job User roles. Leave empty to use the server’s own Google sign-in.' },
      { key: 'location', label: 'Location', section: 'advanced', placeholder: 'e.g. US or asia-south1' },
      { key: 'max_gb_billed', label: 'Most data one query may scan (GB)', section: 'advanced', kind: 'number', placeholder: '10',
        hint: 'BigQuery bills by data scanned; bigger queries fail instead.' }],
    defaults: { max_gb_billed: '10' },
  },
}

/** The form's values as the config the server expects. */
export function toConfig(kind: SourceKind, values: Record<string, string>, path: string): SourceConfig {
  const form = FORMS[kind]
  if (!form) return { kind, path: path.trim() } as SourceConfig
  const out: Record<string, unknown> = { kind }
  for (const f of form.fields) {
    const v = values[f.key] ?? ''
    if (f.kind === 'number') {
      if (v.trim() !== '') out[f.key] = Number(v)
    } else if (f.kind === 'checkbox') {
      out[f.key] = v === 'true'
    } else {
      out[f.key] = f.secret ? v : v.trim()
    }
  }
  return out as SourceConfig
}

export function isReady(kind: SourceKind, values: Record<string, string>, path: string): boolean {
  const form = FORMS[kind]
  if (!form) return path.trim() !== ''
  return form.fields.every((f) => !f.required || (values[f.key] ?? '').trim() !== '')
}

const SCHEMES: Record<string, SourceKind> = {
  'postgres:': 'postgres', 'postgresql:': 'postgres', 'mysql:': 'mysql', 'mariadb:': 'mysql',
  'sqlserver:': 'sqlserver', 'mssql:': 'sqlserver', 'clickhouse:': 'clickhouse', 'redshift:': 'redshift',
}
/** Kinds whose connection can be pasted as a URL. */
export const URL_KINDS: SourceKind[] = ['postgres', 'mysql', 'redshift', 'sqlserver', 'clickhouse']

/** "postgres://user:pass@host:5432/db?sslmode=require" -> form values. */
export function parseUrl(text: string, current: SourceKind): (Record<string, string> & { kind: SourceKind }) | null {
  try {
    const u = new URL(text.trim())
    let kind = SCHEMES[u.protocol]
    if (!kind || !u.hostname) return null
    if (kind === 'postgres' && current === 'redshift') kind = 'redshift'  // Redshift speaks PostgreSQL
    const ssl = u.searchParams.get('sslmode')
    return {
      kind, host: u.hostname, port: u.port, database: decodeURIComponent(u.pathname.replace(/^\//, '')),
      username: decodeURIComponent(u.username), password: decodeURIComponent(u.password),
      ...(ssl && ['prefer', 'require', 'disable'].includes(ssl) ? { sslmode: ssl } : {}),
    }
  } catch {
    return null
  }
}

export function defaultName(kind: SourceKind, values: Record<string, string>, path: string) {
  if (KINDS[kind].file) return path.split(/[\\/]/).pop()?.replace(/\.\w+$/, '') || ''
  if (kind === 'csv') return ''
  return values.database || values.dataset || values.catalog || values.service_name || ''
}
