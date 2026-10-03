import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  BookOpen, CircleAlert, Database, FileSpreadsheet, LoaderCircle, Plus, RefreshCw, ShieldCheck, Sparkles, Wand2,
} from 'lucide-react'
import { api, type DataSource, type SourceConfig, type SourceKind, type TestResult } from '../api'
import { ConfirmDelete } from '../components/ConfirmDelete'
import { relativeTime } from '../lib/format'

const KINDS: { value: SourceKind; label: string }[] = [
  { value: 'postgres', label: 'PostgreSQL' },
  { value: 'mysql', label: 'MySQL' },
  { value: 'sqlite', label: 'SQLite file' },
  { value: 'csv', label: 'CSV / Excel upload' },
]
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.value, k.label])) as Record<SourceKind, string>

export function SourcesPage() {
  const [sources, setSources] = useState<DataSource[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => api.listSources().then(setSources).catch((e: Error) => setError(e.message)), [])
  useEffect(() => { void load() }, [load])

  const refresh = async (id: string) => {
    setBusy(id)
    try {
      await api.refreshSource(id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (s: DataSource) => {
    if (busy) return
    setBusy(s.id)
    try {
      await api.deleteSource(s.id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>Data sources</h1>
        {!adding && <button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden /> Add data source</button>}
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      {adding && <AddSourceForm onCancel={() => setAdding(false)} onCreated={() => { setAdding(false); void load() }} />}

      {sources?.length === 0 && !adding && (
        <section className="empty">
          <h2>No data sources yet</h2>
          <p>Connect a database or upload a spreadsheet to start building dashboards.</p>
          <button className="btn primary" onClick={() => setAdding(true)}>Add data source</button>
        </section>
      )}

      <div className="card-grid">
        {sources?.map((s) => {
          const flagged = s.tables.flatMap((t) => t.columns).filter((c) => c.flag).length
          return (
            <div key={s.id} className="dash-card static">
              <div className="card-top">
                {s.kind === 'csv' ? <FileSpreadsheet size={20} className="dash-card-icon" aria-hidden />
                  : <Database size={20} className="dash-card-icon" aria-hidden />}
                <div className="card-top-actions">
                  <button className="icon-btn" title="Re-read tables and columns" disabled={busy === s.id}
                    onClick={() => refresh(s.id)}>
                    <RefreshCw size={15} className={busy === s.id ? 'spin' : ''} />
                  </button>
                  <ConfirmDelete label={`Remove ${s.name} and its dashboards?`} title="Remove data source" onConfirm={() => remove(s)} />
                </div>
              </div>
              <h2>{s.name}</h2>
              <p className="detail" title={s.detail}>{KIND_LABEL[s.kind]} · {s.detail}</p>
              {s.databaseDescription && <p>{s.databaseDescription}</p>}
              {s.profileError && <p className="error small"><CircleAlert size={13} aria-hidden /> {s.profileError}</p>}
              <div className="dash-card-meta">
                <span>{s.tables.length} {s.tables.length === 1 ? 'table' : 'tables'}</span>
                {s.profiledAt && <span>Scanned {relativeTime(s.profiledAt)}</span>}
                {flagged > 0 && <span className="warn-text">{flagged} column{flagged > 1 ? 's' : ''} could be misread</span>}
              </div>
              <div className="card-actions">
                <Link to={`/sources/${s.id}/definitions`} className="btn ghost small"><BookOpen size={14} aria-hidden /> Definitions</Link>
                <Link to={`/sources/${s.id}/transforms`} className="btn ghost small"><Wand2 size={14} aria-hidden /> Transform</Link>
                <Link to="/new" className="btn ghost small"><Sparkles size={14} aria-hidden /> New dashboard</Link>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const EMPTY_NET = { host: '', port: '', database: '', username: '', password: '', schema: 'public', sslmode: 'prefer' }

function AddSourceForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: () => void }) {
  const [kind, setKind] = useState<SourceKind>('postgres')
  const [name, setName] = useState('')
  const [net, setNet] = useState(EMPTY_NET)
  const [path, setPath] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const field = (k: keyof typeof EMPTY_NET) => ({
    value: net[k],
    onChange: (e: { target: { value: string } }) => { setNet({ ...net, [k]: e.target.value }); setTest(null) },
  })

  const config = (): SourceConfig => {
    if (kind === 'postgres') {
      return { kind, host: net.host.trim(), port: Number(net.port) || 5432, database: net.database.trim(),
        username: net.username.trim(), password: net.password, schema: net.schema.trim() || 'public',
        sslmode: net.sslmode as 'disable' | 'prefer' | 'require' }
    }
    if (kind === 'mysql') {
      return { kind, host: net.host.trim(), port: Number(net.port) || 3306, database: net.database.trim(),
        username: net.username.trim(), password: net.password }
    }
    return { kind: 'sqlite', path: path.trim() }
  }

  const runTest = async () => {
    setBusy('test')
    setError(null)
    try {
      setTest(await api.testConnection(config()))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy('save')
    setError(null)
    try {
      if (kind === 'csv') await api.uploadSource(name.trim() || files[0]?.name.replace(/\.\w+$/, '') || 'Upload', files)
      else await api.createSource(name.trim() || defaultName(kind, net, path), config())
      onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const networked = kind === 'postgres' || kind === 'mysql'
  const ready = kind === 'csv' ? files.length > 0
    : kind === 'sqlite' ? path.trim() !== ''
    : net.host.trim() !== '' && net.database.trim() !== '' && net.username.trim() !== ''

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Add a data source</h2>
      <div className="segmented" role="radiogroup" aria-label="Source type">
        {KINDS.map((k) => (
          <button type="button" key={k.value} role="radio" aria-checked={kind === k.value}
            className={kind === k.value ? 'selected' : ''}
            onClick={() => { setKind(k.value); setTest(null); setError(null) }}>{k.label}</button>
        ))}
      </div>

      <label className="field"><span>Name <em>(optional)</em></span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Production sales" /></label>

      {networked && (
        <div className="form-grid">
          <label className="field"><span>Host</span><input required placeholder="db.internal" {...field('host')} /></label>
          <label className="field"><span>Port</span>
            <input inputMode="numeric" placeholder={kind === 'postgres' ? '5432' : '3306'} {...field('port')} /></label>
          <label className="field"><span>Database</span><input required placeholder="sales" {...field('database')} /></label>
          <label className="field"><span>Username</span>
            <input required autoComplete="off" placeholder="stardeck_readonly" {...field('username')} /></label>
          <label className="field"><span>Password</span>
            <input type="password" autoComplete="new-password" {...field('password')} /></label>
          {kind === 'postgres' && (
            <>
              <label className="field"><span>Schema</span><input placeholder="public" {...field('schema')} /></label>
              <label className="field"><span>SSL</span>
                <select {...field('sslmode')}>
                  <option value="prefer">Prefer</option>
                  <option value="require">Require</option>
                  <option value="disable">Disable</option>
                </select></label>
            </>
          )}
        </div>
      )}
      {kind === 'sqlite' && (
        <label className="field"><span>File path on the Stardeck server</span>
          <input required value={path} onChange={(e) => { setPath(e.target.value); setTest(null) }}
            placeholder="/data/shop.sqlite" /></label>
      )}
      {kind === 'csv' && (
        <label className="dropzone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); setFiles([...e.dataTransfer.files]) }}>
          <FileSpreadsheet size={24} aria-hidden />
          <span>{files.length ? files.map((f) => f.name).join(', ') : 'Drop .csv or .xlsx files here, or click to choose'}</span>
          <span className="muted small">Each CSV, and each sheet in an Excel file, becomes a table.</span>
          <input type="file" accept=".csv,.tsv,.xlsx" multiple hidden
            onChange={(e) => setFiles([...(e.target.files ?? [])])} />
        </label>
      )}

      <p className="hint">
        <ShieldCheck size={15} aria-hidden />
        <span>Stardeck only runs read-only queries, and opens every connection in read-only mode.
          Using a database account with read-only access adds one more layer of safety.</span>
      </p>

      {test && (
        <p className={`test-result ${test.ok ? 'ok' : 'bad'}`} role="status">{test.message}</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}

      <div className="form-actions">
        <button type="submit" className="btn primary" disabled={!ready || busy !== null}>
          {busy === 'save' && <LoaderCircle size={15} className="spin" aria-hidden />}
          {kind === 'csv' ? 'Upload & scan' : 'Connect & scan'}
        </button>
        {kind !== 'csv' && (
          <button type="button" className="btn ghost" disabled={!ready || busy !== null} onClick={runTest}>
            {busy === 'test' && <LoaderCircle size={15} className="spin" aria-hidden />} Test connection
          </button>
        )}
        <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function defaultName(kind: SourceKind, net: typeof EMPTY_NET, path: string) {
  if (kind === 'sqlite') return path.split('/').pop()?.replace(/\.\w+$/, '') || 'SQLite'
  return net.database || KIND_LABEL[kind]
}
