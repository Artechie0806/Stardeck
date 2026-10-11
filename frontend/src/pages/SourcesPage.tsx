import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  ArrowLeft, ArrowRight, BookOpen, ChevronRight, CircleAlert, CircleCheck, Database, Eye, EyeOff, FileSpreadsheet,
  LoaderCircle, PlugZap, Plus, RefreshCw, ShieldCheck, Sparkles, Upload, Wand2, X,
} from 'lucide-react'
import { api, type DataSource, type SourceKind, type TestResult } from '../api'
import { ConfirmDelete } from '../components/ConfirmDelete'
import { PortalHost } from '../lib/portalHost'
import { UpgradeNote } from '../components/Upgrade'
import { useAppConfig } from '../lib/config'
import { relativeTime } from '../lib/format'
import { isFull, usePlan, useUsage } from '../lib/plan'
import { defaultName, FORMS, GROUPS, isReady, KINDS, parseUrl, toConfig, URL_KINDS, type FieldSpec } from './sourceKinds'

export function SourcesPage() {
  const [sources, setSources] = useState<DataSource[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { plan } = usePlan()
  const { usage, reload: reloadUsage } = useUsage()
  const full = isFull(usage?.sources)

  const load = useCallback(() => {
    reloadUsage()
    return api.listSources().then(setSources).catch((e: Error) => setError(e.message))
  }, [reloadUsage])
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
        <button className="btn primary" onClick={() => setAdding(true)} disabled={full}
          title={full ? 'Your plan’s data sources are all in use' : undefined}><Plus size={16} aria-hidden /> Add data source</button>
      </div>
      {full && usage?.sources.limit !== null && (
        <UpgradeNote>Your {plan?.name ?? ''} plan includes {usage?.sources.limit} data source{usage?.sources.limit === 1 ? '' : 's'},
          and the sample doesn’t count. Remove one to add another, or upgrade for more.</UpgradeNote>
      )}
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
          <button className="btn primary" onClick={() => setAdding(true)} disabled={full}><Plus size={16} aria-hidden /> Add data source</button>
        </section>
      )}

      <div className="card-grid">
        {sources?.map((s) => {
          const flagged = s.tables.flatMap((t) => t.columns).filter((c) => c.flag).length
          return (
            <div key={s.id} className="dash-card static">
              <div className="card-top">
                <span className="icon-tile"><KindIcon kind={s.kind} /></span>
                <div className="card-top-actions">
                  <button className="icon-btn" title="Re-read tables and columns" aria-label={`Re-scan ${s.name}`} disabled={busy === s.id}
                    onClick={() => refresh(s.id)}>
                    <RefreshCw size={15} className={busy === s.id ? 'spin' : ''} />
                  </button>
                  <ConfirmDelete label={`Remove “${s.name}”?`} title="Remove data source" confirmText="Remove"
                    detail="Its dashboards and prepared tables are deleted too. Your database or files aren’t touched."
                    onConfirm={() => remove(s)} />
                </div>
              </div>
              <h2>{s.name}</h2>
              <p className="detail" title={s.detail}><span className="badge">{KINDS[s.kind]?.label ?? s.kind}</span> <span>{KINDS[s.kind]?.file ? s.detail.split(/[\\/]/).pop() : s.detail}</span></p>
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

function KindIcon({ kind, size = 19 }: { kind: SourceKind; size?: number }) {
  const Icon = KINDS[kind]?.icon ?? FileSpreadsheet
  return <Icon size={size} aria-hidden />
}

function AddSourceDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const appConfig = useAppConfig()
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null)
  const [kind, setKind] = useState<SourceKind | null>(null)
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [path, setPath] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [over, setOver] = useState(false)
  const [shown, setShown] = useState<Record<string, boolean>>({})
  const [url, setUrl] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [dialog])

  // Switching kind keeps what was typed into fields both kinds share (host, username...).
  const choose = (k: SourceKind | null) => {
    setKind(k); setTest(null); setError(null); setUrl('')
    dialog?.querySelector('.modal-content')?.scrollTo(0, 0)
    if (k) {
      const keys = new Set(FORMS[k]?.fields.map((f) => f.key))
      const kept = Object.fromEntries(Object.entries(values).filter(([key, v]) => keys.has(key) && v && key !== 'port'))
      setValues({ ...FORMS[k]?.defaults, ...kept })
    }
  }
  const set = (key: string, value: string) => { setValues({ ...values, [key]: value }); setTest(null) }
  const applyUrl = (text: string) => {
    setUrl(text)
    const parsed = kind && parseUrl(text, kind)
    if (!parsed) return
    const { kind: k, ...rest } = parsed
    if (k !== kind) {
      setKind(k); setValues({ ...FORMS[k]?.defaults, ...rest })
    } else {
      setValues({ ...values, ...rest })
    }
    setTest(null)
  }

  const runTest = async () => {
    if (!kind) return
    setBusy('test'); setError(null)
    try {
      setTest(await api.testConnection(toConfig(kind, values, path)))
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
      else await api.createSource(name.trim() || defaultName(kind, values, path) || KINDS[kind].label, toConfig(kind, values, path))
      onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const form = kind ? FORMS[kind] : undefined
  const ready = kind === 'csv' ? files.length > 0 : kind ? isReady(kind, values, path) : false
  const info = kind ? KINDS[kind] : null
  const Icon = info?.icon ?? Plus

  const input = (f: FieldSpec) => {
    const value = values[f.key] ?? ''
    const id = `src-${f.key}`
    if (f.kind === 'checkbox') {
      return (
        <label key={f.key} className="checkbox">
          <input type="checkbox" checked={value === 'true'} onChange={(e) => set(f.key, String(e.target.checked))} />
          <span>{f.label}</span>
        </label>
      )
    }
    let control
    if (f.kind === 'select') {
      control = (
        <select id={id} name={f.key} value={value} onChange={(e) => set(f.key, e.target.value)}>
          {f.options?.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      )
    } else if (f.secret && f.multiline) {
      control = (
        <textarea id={id} name={f.key} rows={4} value={value} spellCheck={false} autoComplete="off" className="mono"
          placeholder={f.placeholder} onChange={(e) => set(f.key, e.target.value)} />
      )
    } else if (f.secret) {
      control = (
        <span className="input-affix">
          <input id={id} name={f.key} type={shown[f.key] ? 'text' : 'password'} autoComplete="new-password" value={value}
            required={f.required} onChange={(e) => set(f.key, e.target.value)} />
          <button type="button" className="icon-btn" onClick={() => setShown({ ...shown, [f.key]: !shown[f.key] })}
            aria-label={shown[f.key] ? `Hide ${f.label.toLowerCase()}` : `Show ${f.label.toLowerCase()}`}>
            {shown[f.key] ? <EyeOff size={15} /> : <Eye size={15} />}</button>
        </span>
      )
    } else {
      control = (
        <input id={id} name={f.key} value={value} required={f.required} spellCheck={false} autoComplete="off"
          inputMode={f.kind === 'number' ? 'decimal' : undefined} placeholder={f.placeholder}
          onChange={(e) => set(f.key, e.target.value)} />
      )
    }
    return (
      <label key={f.key} className={`field${f.narrow ? ' port' : ''}`} htmlFor={id}>
        <span>{f.label}</span>{control}
        {f.hint && <small className="muted">{f.hint}</small>}
      </label>
    )
  }
  // Fields in order, with each "narrow" one (a port) on the row of the field before it.
  const rows = (fields: FieldSpec[]) => fields.map((f, i) => {
    if (f.narrow) return null
    const next = fields[i + 1]
    return next?.narrow
      ? <div key={f.key} className="host-row">{input(f)}{input(next)}</div>
      : input(f)
  })
  const section = (id: FieldSpec['section']) => form?.fields.filter((f) => f.section === id) ?? []
  const signin = section('signin')
  const advanced = section('advanced')

  return (
    <dialog ref={setDialog} className="modal" aria-labelledby="add-source-title"
      onCancel={(e) => { e.preventDefault(); if (!busy) onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <PortalHost.Provider value={dialog}>
      <form className="modal-body" onSubmit={submit}>
        <header className="modal-head">
          {kind && (
            <button type="button" className="icon-btn" onClick={() => choose(null)} aria-label="Back to source types" disabled={busy !== null}>
              <ArrowLeft size={18} />
            </button>
          )}
          <span className="icon-tile"><Icon size={19} aria-hidden /></span>
          <div className="modal-title">
            <h2 id="add-source-title">{info ? info.label : 'Add a data source'}</h2>
            <p className="muted small">{info ? info.blurb : 'Where does your data live?'}</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={busy !== null}><X size={18} /></button>
        </header>

        <div className="modal-content">
          {!kind && GROUPS.map((g) => {
            const kinds = g.kinds.filter((k) => !(appConfig?.hosted && KINDS[k].file))
            const compact = g.id !== 'files'
            return (
              <section key={g.id} className="kind-group" aria-labelledby={`kind-group-${g.id}`}>
                <h3 id={`kind-group-${g.id}`}>{g.label}</h3>
                <div className={`kind-grid${compact ? ' compact' : ''}`} role="list">
                  {kinds.map((k) => (
                    <button type="button" role="listitem" key={k} className="kind-tile" onClick={() => choose(k)}
                      title={compact ? KINDS[k].blurb : undefined}>
                      <span className="icon-tile"><KindIcon kind={k} size={compact ? 17 : 20} /></span>
                      <span className="kind-text">
                        <strong>{KINDS[k].label}</strong>
                        {!compact && <span className="muted small">{KINDS[k].blurb}</span>}
                      </span>
                      {k === 'csv' && <span className="badge accent">Easiest</span>}
                      <ChevronRight size={16} className="kind-chevron" aria-hidden />
                    </button>
                  ))}
                </div>
              </section>
            )
          })}

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
              <span className="muted small">.csv, .tsv or .xlsx. Each CSV, and each sheet in an Excel file, becomes a table.
                {appConfig?.plan?.limits.uploadMb ? ` Up to ${appConfig.plan.limits.uploadMb} MB.` : ''}</span>
              <input type="file" accept=".csv,.tsv,.xlsx" multiple className="visually-hidden"
                onChange={(e) => setFiles([...(e.target.files ?? [])])} />
            </label>
          )}

          {kind && form && (
            <>
              {URL_KINDS.includes(kind) && (
                <label className="field url-field"><span>Connection URL <em>(optional shortcut)</em></span>
                  <input value={url} onChange={(e) => applyUrl(e.target.value)} spellCheck={false} autoComplete="off"
                    placeholder={`${kind}://user:password@host:${form.fields.find((f) => f.key === 'port')?.placeholder}/database`} />
                  {url && !parseUrl(url, kind) && <small className="warn-text">That doesn’t look like a {info?.label} URL. You can fill in the fields below instead.</small>}
                  {url && parseUrl(url, kind) && <small className="ok-text">Filled in the fields below.</small>}
                </label>
              )}
              <fieldset className="form-section">
                <legend>{kind === 'bigquery' ? 'Dataset' : kind === 'snowflake' ? 'Account' : 'Server'}</legend>
                {rows(section('server'))}
              </fieldset>
              <fieldset className="form-section">
                <legend>Sign in</legend>
                <div className={signin.length === 2 && !signin.some((f) => f.multiline || f.hint) ? 'two-col' : undefined}>
                  {rows(signin)}
                </div>
              </fieldset>
              {advanced.length > 0 && (
                <details className="advanced">
                  <summary>Advanced: {advanced.map((f) => f.label.split(' (')[0].toLowerCase()).join(', ')}</summary>
                  {rows(advanced)}
                </details>
              )}
            </>
          )}

          {kind && info?.file && (
            <label className="field"><span>File path on the Stardeck server</span>
              <input required value={path} spellCheck={false} onChange={(e) => { setPath(e.target.value); setTest(null) }}
                placeholder={kind === 'duckdb' ? '/data/warehouse.duckdb' : '/data/shop.sqlite'} />
              <small className="muted">The file must be readable by the machine running Stardeck, not your browser.</small></label>
          )}

          {kind && (
            <label className="field"><span>Display name <em>(optional)</em></span>
              <input value={name} onChange={(e) => setName(e.target.value)}
                placeholder={kind === 'csv' ? (files[0]?.name.replace(/\.\w+$/, '') || 'e.g. Q3 sales') : (defaultName(kind, values, path) || 'e.g. Production sales')} /></label>
          )}

          {kind && kind !== 'csv' && (
            <p className="hint"><ShieldCheck size={15} aria-hidden />
              <span>{info?.readOnly
                ? 'Stardeck only ever reads. Every connection opens in read-only mode, and a read-only database account adds one more layer of safety.'
                : `Stardeck only ever runs SELECT queries. ${info?.label} has no read-only connection mode, so sign in with an account that can only read.`}</span></p>
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
      </PortalHost.Provider>
    </dialog>
  )
}
