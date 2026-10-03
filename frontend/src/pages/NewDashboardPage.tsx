import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { Check, CircleAlert, LoaderCircle, Sparkles } from 'lucide-react'
import { api, type DataSource } from '../api'

const GOAL_IDEAS = [
  'A one-page overview for management',
  'What is driving our numbers?',
  'Trends over time',
  'Customers and how they behave',
]

type Step = { title: string; state: 'pending' | 'done' | 'failed'; reason?: string }

export function NewDashboardPage() {
  const navigate = useNavigate()
  const [sources, setSources] = useState<DataSource[]>([])
  const [sourceId, setSourceId] = useState('')
  const [goal, setGoal] = useState('')
  const [phase, setPhase] = useState<'form' | 'planning' | 'building' | 'done'>('form')
  const [steps, setSteps] = useState<Step[]>([])
  const [dashboardId, setDashboardId] = useState<string | null>(null)
  const [planName, setPlanName] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listSources().then((s) => { setSources(s); setSourceId(s[0]?.id ?? '') })
  }, [])

  const generate = async () => {
    setPhase('planning')
    setError(null)
    try {
      await consume()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const consume = async () => {
    for await (const ev of api.generateDashboard(sourceId, goal)) {
      if (ev.type === 'error') {
        setError(ev.message)
        return
      }
      if (ev.type === 'plan') {
        setPlanName(ev.name)
        setSteps(ev.titles.map((title) => ({ title, state: 'pending' })))
        setPhase('building')
      } else if (ev.type === 'widget') {
        setSteps((s) => s.map((x, i) => (i === ev.index ? { ...x, state: 'done' } : x)))
      } else if (ev.type === 'widget_failed') {
        setSteps((s) => s.map((x, i) => (i === ev.index ? { ...x, state: 'failed', reason: ev.reason } : x)))
      } else if (ev.type === 'done') {
        setDashboardId(ev.dashboardId)
        setPhase('done')
      }
    }
  }

  const source = sources.find((s) => s.id === sourceId)
  const undefinedCount = source?.tables.flatMap((t) => t.columns).filter((c) => c.flag).length ?? 0

  if (phase !== 'form') {
    const done = steps.filter((s) => s.state === 'done').length
    return (
      <div className="page narrow">
        <h1>{error ? 'Something went wrong' : phase === 'done' ? 'Your dashboard is ready' : 'Building your dashboard…'}</h1>
        {planName && <h2 className="plan-name">{planName}</h2>}
        <p className="muted">
          {phase === 'planning' ? 'Reading your tables and deciding what is worth showing. This takes a moment with a local model.'
            : `${done} of ${steps.length} widgets built. Each one gets its own query, checked before it runs.`}
        </p>
        {error && (
          <p className="error" role="alert">{error}{' '}
            <button className="btn ghost small" onClick={() => { setPhase('form'); setSteps([]); setError(null) }}>Back</button>
          </p>
        )}
        <ol className="build-steps">
          {phase === 'planning' && !error && <li><LoaderCircle size={16} className="spin" aria-hidden /> Planning</li>}
          {steps.map((s) => (
            <li key={s.title} className={s.state}>
              {s.state === 'done' ? <Check size={16} aria-hidden />
                : s.state === 'failed' ? <CircleAlert size={16} aria-hidden />
                : error ? <CircleAlert size={16} aria-hidden />
                : <LoaderCircle size={16} className="spin" aria-hidden />}
              <span>{s.title}</span>
              {s.reason && <span className="muted small"> · skipped: {s.reason}</span>}
            </li>
          ))}
        </ol>
        {dashboardId && (
          <button className="btn primary" onClick={() => navigate(`/d/${dashboardId}`)}>Open dashboard</button>
        )}
      </div>
    )
  }

  return (
    <div className="page narrow">
      <h1>New dashboard</h1>
      <p className="muted">Stardeck looks at your data and builds a dashboard. You can change anything afterwards.</p>

      <label className="field">
        <span>Data source</span>
        <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
          {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      {undefinedCount > 0 && (
        <p className="hint">
          {undefinedCount} column{undefinedCount > 1 ? 's' : ''} in this source could be misread.{' '}
          <Link to={`/sources/${sourceId}/definitions`}>Explain them</Link> for better results (optional).
        </p>
      )}

      <label className="field">
        <span>What should it focus on? <em>(optional)</em></span>
        <textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)}
          placeholder="Leave empty and Stardeck will pick the most useful metrics." />
      </label>
      <div className="chips">
        {GOAL_IDEAS.map((g) => <button key={g} className="chip" onClick={() => setGoal(g)}>{g}</button>)}
      </div>

      <button className="btn primary large" disabled={!sourceId} onClick={generate}>
        <Sparkles size={16} aria-hidden /> Generate dashboard
      </button>
    </div>
  )
}
