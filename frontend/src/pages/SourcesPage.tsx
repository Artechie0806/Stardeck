import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  ArrowLeft, ArrowRight, BookOpen, ChevronRight, CircleAlert, CircleCheck, Database, Eye, EyeOff, FileSpreadsheet, HardDrive,
  LoaderCircle, PlugZap, Plus, RefreshCw, ShieldCheck, Sparkles, Upload, Wand2, X,
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
        <div>
          <h1>Data sources</h1>
          <p className="muted">The databases and spreadsheets your dashboards are built from.</p>
        </div>
        <button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden /> Add data source</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}

      {adding && <AddSourceDialog onClose={() => setAdding(false)} onCreated={() => { setAdding(false); void load() }} />}

      {sources === null && !error && (
        <div className="card-grid" aria-busy="true">{[0, 1].map((i) => <div key={i} className="dash-card skeleton" />)}</div>
      )}
      {sources?.length === 0 && (
        <section className="empty">
          <span className="icon-tile large"><Database size={26} aria-hidden /></span>
          <h2>No data sources yet</h2>
          <p>Upload a spreadsheet or connect a database. Stardeck reads it, and the AI builds dashboards from it.</p>
          <button className="btn primary" onClick={() => setAdding(true)}><Plus size={16} aria-hidden /> Add data source</button>
        </section>
      )}

      <div className="card-grid">
        {sources?.map((s) => {
          const flagged = s.tables.flatMap((t) => t.columns).filter((c) => c.flag).length
          return (
            <div key={s.id} className="dash-card static">
              <div className="card-top">
                <span className="icon-tile">{s.kind === 'csv' ? <FileSpreadsheet size={19} aria-hidden />
                  : s.kind === 'sqlite' ? <HardDrive size={19} aria-hidden /> : <Database size={19} aria-hidden />}</span>
                <div className="card-top-actions">
                  <button className="icon-btn" title="Re-read tables and columns" aria-label={`Re-scan ${s.name}`} disabled={busy === s.id}
                    onClick={() => refresh(s.id)}>
                    <RefreshCw size={15} className={busy === s.id ? 'spin' : ''} />
                  </button>
                  <ConfirmDelete label={`Remove ${s.name} and its dashboards?`} title="Remove data source" onConfirm={() => remove(s)} />
                </div>
              </div>
              <h2>{s.name}</h2>
              <p className="detail" title={s.detail}><span className="badge">{KIND_LABEL[s.kind]}</span> <span>{s.kind === 'sqlite' ? s.detail.split('/').pop() : s.detail}</span></p>
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
                <Link to={`/new?source=${s.id}`} className="btn ghost small"><Sparkles size={14} aria-hidden /> New dashboard</Link>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

const EMPTY_NET = { host: '', port: '', database: '', username: '', password: '', schema: 'public', sslmode: 'prefer' }

const KIND_INFO: Record<SourceKind, { icon: typeof Database; blurb: string }> = {
  csv: { icon: FileSpreadsheet, blurb: 'Upload .csv or Excel files. The quickest way to start.' },
  postgres: { icon: Database, blurb: 'Connect to a PostgreSQL server with its address and a login.' },
  mysql: { icon: Database, blurb: 'Connect to a MySQL or MariaDB server.' },
  sqlite: { icon: HardDrive, blurb: 'A .sqlite or .db file stored on the Stardeck server.' },
}
const KIND_ORDER: SourceKind[] = ['csv', 'postgres', 'mysql', 'sqlite']

/** "postgres://user:pass@host:5432/db?sslmode=require" -> form fields. */
function parseUrl(text: string): Partial<typeof EMPTY_NET> & { kind?: SourceKind } | null {
  try {
    const u = new URL(text.trim())
    const kind = /^postgres(ql)?:$/.test(u.protocol) ? 'postgres' : /^(mysql|mariadb):$/.test(u.protocol) ? 'mysql' : null
    if (!kind || !u.hostname) return null
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

function AddSourceDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const [kind, setKind] = useState<SourceKind | null>(null)
  const [name, setName] = useState('')
  const [net, setNet] = useState(EMPTY_NET)
  const [path, setPath] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [over, setOver] = useState(false)
  const [showPw, setShowPw] = useState(false)
  const [url, setUrl] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const d = ref.current
    if (d && !d.open) d.showModal()
    return () => d?.close()
  }, [])

  const choose = (k: SourceKind | null) => {
    setKind(k); setTest(null); setError(null)
    if (k === 'mysql' && net.schema === 'public') setNet({ ...net, schema: '' })
    if (k === 'postgres' && !net.schema) setNet({ ...net, schema: 'public' })
  }
  const field = (k: keyof typeof EMPTY_NET) => ({
    value: net[k], name: k,
    onChange: (e: { target: { value: string } }) => { setNet({ ...net, [k]: e.target.value }); setTest(null) },
  })
  const applyUrl = (text: string) => {
    setUrl(text)
    const parsed = parseUrl(text)
    if (!parsed) return
    const { kind: k, ...rest } = parsed
    setNet({ ...net, ...rest }); setTest(null)
    if (k && k !== kind) setKind(k)
  }

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
    setBusy('test'); setError(null)
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
    if (!kind) return
    setBusy('save'); setError(null)
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
  const Icon = kind ? KIND_INFO[kind].icon : Plus

  return (
    <dialog ref={ref} className="modal" aria-labelledby="add-source-title"
      onCancel={(e) => { e.preventDefault(); if (!busy) onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <form className="modal-body" onSubmit={submit}>
        <header className="modal-head">
          {kind && (
            <button type="button" className="icon-btn" onClick={() => choose(null)} aria-label="Back to source types" disabled={busy !== null}>
              <ArrowLeft size={18} />
            </button>
          )}
          <span className="icon-tile"><Icon size={19} aria-hidden /></span>
          <div className="modal-title">
            <h2 id="add-source-title">{kind ? KIND_LABEL[kind] : 'Add a data source'}</h2>
            <p className="muted small">{kind ? KIND_INFO[kind].blurb : 'Where does your data live?'}</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={busy !== null}><X size={18} /></button>
        </header>

        <div className="modal-content">
          {!kind && (
            <div className="kind-grid" role="list">
              {KIND_ORDER.map((k) => {
                const KIcon = KIND_INFO[k].icon
                return (
                  <button type="button" role="listitem" key={k} className="kind-tile" onClick={() => choose(k)}>
                    <span className="icon-tile"><KIcon size={20} aria-hidden /></span>
                    <span className="kind-text">
                      <strong>{KIND_LABEL[k]}</strong>
                      <span className="muted small">{KIND_INFO[k].blurb}</span>
                    </span>
                    {k === 'csv' && <span className="badge accent">Easiest</span>}
                    <ChevronRight size={16} className="kind-chevron" aria-hidden />
                  </button>
                )
              })}
            </div>
          )}

          {kind === 'csv' && (
            <label className={`dropzone${over ? ' over' : ''}${files.length ? ' has-files' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setOver(true) }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); setFiles([...e.dataTransfer.files]) }}>
              <span className="icon-tile"><Upload size={20} aria-hidden /></span>
              {files.length ? (
                <span className="file-list">{files.map((f) => (
                  <span key={f.name} className="file-pill"><FileSpreadsheet size={13} aria-hidden /> {f.name}</span>
                ))}</span>
              ) : <strong>Drop files here, or click to choose</strong>}
              <span className="muted small">.csv, .tsv or .xlsx. Each CSV, and each sheet in an Excel file, becomes a table.</span>
              <input type="file" accept=".csv,.tsv,.xlsx" multiple className="visually-hidden"
                onChange={(e) => setFiles([...(e.target.files ?? [])])} />
            </label>
          )}

          {networked && (
            <>
              <label className="field url-field"><span>Connection URL <em>(optional shortcut)</em></span>
                <input value={url} onChange={(e) => applyUrl(e.target.value)} spellCheck={false} autoComplete="off"
                  placeholder={kind === 'postgres' ? 'postgres://user:password@host:5432/database' : 'mysql://user:password@host:3306/database'} />
                {url && !parseUrl(url) && <small className="warn-text">That doesn’t look like a {KIND_LABEL[kind]} URL. You can fill in the fields below instead.</small>}
                {url && parseUrl(url) && <small className="ok-text">Filled in the fields below.</small>}
              </label>
              <fieldset className="form-section">
                <legend>Server</legend>
                <div className="host-row">
                  <label className="field"><span>Host</span><input required placeholder="db.example.com" autoComplete="off" spellCheck={false} {...field('host')} /></label>
                  <label className="field port"><span>Port</span>
                    <input inputMode="numeric" placeholder={kind === 'postgres' ? '5432' : '3306'} {...field('port')} /></label>
                </div>
                <label className="field"><span>Database name</span><input required placeholder="sales" spellCheck={false} {...field('database')} /></label>
              </fieldset>
              <fieldset className="form-section">
                <legend>Sign in</legend>
                <div className="two-col">
                  <label className="field"><span>Username</span>
                    <input required autoComplete="off" spellCheck={false} placeholder="stardeck_readonly" {...field('username')} /></label>
                  <label className="field"><span>Password</span>
                    <span className="input-affix">
                      <input type={showPw ? 'text' : 'password'} autoComplete="new-password" {...field('password')} />
                      <button type="button" className="icon-btn" onClick={() => setShowPw(!showPw)}
                        aria-label={showPw ? 'Hide password' : 'Show password'}>{showPw ? <EyeOff size={15} /> : <Eye size={15} />}</button>
                    </span></label>
                </div>
              </fieldset>
              {kind === 'postgres' && (
                <details className="advanced">
                  <summary>Advanced: schema and SSL</summary>
                  <div className="two-col">
                    <label className="field"><span>Schema</span><input placeholder="public" {...field('schema')} /></label>
                    <label className="field"><span>SSL</span>
                      <select {...field('sslmode')}>
                        <option value="prefer">Prefer (use it when the server offers it)</option>
                        <option value="require">Require</option>
                        <option value="disable">Disable</option>
                      </select></label>
                  </div>
                </details>
              )}
            </>
          )}

          {kind === 'sqlite' && (
            <label className="field"><span>File path on the Stardeck server</span>
              <input required value={path} spellCheck={false} onChange={(e) => { setPath(e.target.value); setTest(null) }}
                placeholder="/data/shop.sqlite" />
              <small className="muted">The file must be readable by the machine running Stardeck, not your browser.</small></label>
          )}

          {kind && (
            <label className="field"><span>Display name <em>(optional)</em></span>
              <input value={name} onChange={(e) => setName(e.target.value)}
                placeholder={kind === 'csv' ? (files[0]?.name.replace(/\.\w+$/, '') || 'e.g. Q3 sales') : (defaultName(kind, net, path) || 'e.g. Production sales')} /></label>
          )}

          {kind && kind !== 'csv' && (
            <p className="hint"><ShieldCheck size={15} aria-hidden />
              <span>Stardeck only ever reads. Every connection opens in read-only mode, and a read-only database account adds one more layer of safety.</span></p>
          )}
          {test && (
            <p className={`test-result ${test.ok ? 'ok' : 'bad'}`} role="status">
              {test.ok ? <CircleCheck size={16} aria-hidden /> : <CircleAlert size={16} aria-hidden />}<span>{test.message}</span></p>
          )}
          {error && <p className="test-result bad" role="alert"><CircleAlert size={16} aria-hidden /><span>{error}</span></p>}
        </div>

        {kind && (
          <footer className="modal-foot">
            {kind !== 'csv' && (
              <button type="button" className="btn ghost" disabled={!ready || busy !== null} onClick={runTest}>
                {busy === 'test' ? <LoaderCircle size={15} className="spin" aria-hidden /> : <PlugZap size={15} aria-hidden />} Test connection
              </button>
            )}
            <button type="submit" className="btn primary" disabled={!ready || busy !== null}>
              {busy === 'save' ? <LoaderCircle size={15} className="spin" aria-hidden /> : <ArrowRight size={15} aria-hidden />}
              {busy === 'save' ? 'Reading your tables…' : kind === 'csv' ? 'Upload & scan' : 'Connect & scan'}
            </button>
          </footer>
        )}
      </form>
    </dialog>
  )
}

function defaultName(kind: SourceKind, net: typeof EMPTY_NET, path: string) {
  if (kind === 'sqlite') return path.split('/').pop()?.replace(/\.\w+$/, '') || ''
  if (kind === 'csv') return ''
  return net.database || ''
}
