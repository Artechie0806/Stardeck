import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Plus, Wand2 } from 'lucide-react'
import { api, type DataSource, type PreparedTable } from '../api'
import { ConfirmDelete } from '../components/ConfirmDelete'

/** A source's prepared tables (Power Query-style recipes), plus starting a new one. */
export function TransformsPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [source, setSource] = useState<DataSource | null>(null)
  const [items, setItems] = useState<PreparedTable[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [base, setBase] = useState('')

  const load = useCallback(() => {
    api.getSource(id).then(setSource).catch((e: Error) => setError(e.message))
    api.listTransforms(id).then(setItems).catch((e: Error) => setError(e.message))
  }, [id])
  useEffect(load, [load])

  const start = (e: FormEvent) => {
    e.preventDefault()
    navigate(`/sources/${id}/transforms/new?base=${encodeURIComponent(base)}&name=${encodeURIComponent(name)}`)
  }
  const remove = async (n: string) => {
    try {
      await api.deleteTransform(id, n)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="page narrow-wide">
      <p className="crumb"><Link to="/sources">Data sources</Link> / {source?.name}</p>
      <div className="page-head"><h1>Prepared tables</h1></div>
      <p className="muted">
        Clean and reshape data without touching your database: filter, rename, fix types, add calculated columns,
        merge tables and summarize, step by step like Power Query. The result works like a table everywhere in Stardeck.
      </p>
      {error && <p className="error banner" role="alert">{error}</p>}

      <form className="panel new-prep" onSubmit={start}>
        <h2><Wand2 size={16} aria-hidden /> New prepared table</h2>
        <div className="form-grid">
          <label className="field"><span>Start from</span>
            <select required value={base} onChange={(e) => { setBase(e.target.value); if (!name) setName(`${e.target.value}_clean`) }}>
              <option value="" disabled>Choose a table…</option>
              {source?.tables.filter((t) => !t.error).map((t) => <option key={t.name} value={t.name}>{t.name}{t.prepared ? ' (prepared)' : ''}</option>)}
            </select></label>
          <label className="field"><span>Name</span>
            <input required value={name} placeholder="e.g. orders_clean"
              onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'))} /></label>
        </div>
        <div className="form-actions"><button className="btn primary" disabled={!base || !name}><Plus size={15} aria-hidden /> Create</button></div>
      </form>

      {items?.length === 0 && <p className="muted">No prepared tables yet.</p>}
      <div className="card-grid">
        {items?.map((t) => (
          <Link key={t.name} to={`/sources/${id}/transforms/${t.name}`} className="dash-card">
            <div className="card-top">
              <Wand2 size={20} className="dash-card-icon" aria-hidden />
              <ConfirmDelete label="Delete prepared table?" title="Delete" onConfirm={() => remove(t.name)} />
            </div>
            <h2><code>{t.name}</code></h2>
            <p>From <code>{t.base}</code> · {t.steps.length} step{t.steps.length === 1 ? '' : 's'}</p>
            {t.labels.slice(0, 3).map((l, i) => <p key={i} className="small muted">{i + 1}. {l}</p>)}
            {t.error && <p className="error small">{t.error}</p>}
            <div className="dash-card-meta">
              {t.rowCount !== null && <span>{t.rowCount.toLocaleString('en-IN')} rows</span>}
              <span>{t.columns.length} columns</span>
              {t.usedBy.length > 0 && <span>Used by {t.usedBy.length} widget{t.usedBy.length > 1 ? 's' : ''}</span>}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
