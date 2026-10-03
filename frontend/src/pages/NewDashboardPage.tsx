import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Check, CircleAlert, Database, LayoutDashboard, LoaderCircle, Sparkles } from 'lucide-react'
import { api, type DataSource } from '../api'
import { AiPaused, AiUsedUp } from '../components/AiNotice'
import { UpgradePanel } from '../components/Upgrade'
import { recheckAi, useAiStatus } from '../lib/aiStatus'
import { isFull, left, usePlan, useUsage } from '../lib/plan'

const GOAL_IDEAS = [
  'A one-page overview for management',
  'What is driving our numbers?',
  'Trends over time',
  'Customers and how they behave',
]

type Step = { title: string; state: 'pending' | 'done' | 'failed'; reason?: string }

export function NewDashboardPage() {
  const { plan, has } = usePlan()
  const { usage, reload: reloadUsage } = useUsage()
  const paused = useAiStatus().online === false
  const builds = usage?.ai.generation
  const buildsLeft = left(builds)
  const single = !has('multi_source')
  const navigate = useNavigate()
  const [sources, setSources] = useState<DataSource[]>([])
  const [loaded, setLoaded] = useState(false)
  const [sourceIds, setSourceIds] = useState<string[]>([])
  const [goal, setGoal] = useState('')
  const [phase, setPhase] = useState<'form' | 'planning' | 'building' | 'done'>('form')
  const [steps, setSteps] = useState<Step[]>([])
  const [dashboardId, setDashboardId] = useState<string | null>(null)
  const [planName, setPlanName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [params] = useSearchParams()
  const wanted = params.get('source') // "New dashboard" on a source card preselects it

  useEffect(() => {
    api.listSources().then((s) => {
      setSources(s)
      const first = s.find((x) => x.id === wanted)?.id ?? s[0]?.id
      setSourceIds(first ? [first] : [])
      setLoaded(true)
    })
  }, [wanted])

  const generate = async () => {
    setPhase('planning')
    setError(null)
    try {
      await consume()
    } catch (e) {
      setError((e as Error).message)
      void recheckAi()
    }
  }

  const consume = async () => {
    for await (const ev of api.generateDashboard(sourceIds, goal)) {
      if (ev.type === 'error') {
        setError(ev.message)
        void recheckAi() // if the AI went offline, the form says so
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

  // Leaving mid-build would orphan a half-built dashboard: ask first.
  const building = phase === 'planning' || phase === 'building'
  useEffect(() => {
    if (!building) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [building])

  const chosen = sources.filter((s) => sourceIds.includes(s.id))
  const unclear = chosen.map((s) => ({ s, n: s.tables.flatMap((t) => t.columns).filter((c) => c.flag).length })).filter((x) => x.n > 0)
  // One source per dashboard on some plans: picking another replaces it.
  const toggle = (id: string) => setSourceIds((ids) => (single ? [id]
    : ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))
  const many = sources.length > 1 && !single

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
            <button className="btn ghost small" onClick={() => { setPhase('form'); setSteps([]); setError(null); reloadUsage() }}>Back</button>
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
      {isFull(usage?.dashboards) ? (
        <UpgradePanel icon={<LayoutDashboard size={26} aria-hidden />} title="Your dashboards are all in use">
          Your {plan?.name ?? ''} plan includes {usage?.dashboards.limit} dashboard{usage?.dashboards.limit === 1 ? '' : 's'}, and
          the sample doesn’t count. Delete one to build another, or upgrade for more.
        </UpgradePanel>
      ) : loaded && sources.length === 0 ? (
        <section className="empty new-empty">
          <span className="icon-tile large"><Database size={26} aria-hidden /></span>
          <h2>Connect some data first</h2>
          <p>Upload a spreadsheet or connect a database, then come back to build a dashboard from it.</p>
          <Link to="/sources" className="btn primary">Add a data source</Link>
        </section>
      ) : (
      <div className="panel new-form">

      <fieldset className="field source-pick">
        <legend>Data <em>{many ? '(pick one or more)' : sources.length > 1 ? '(pick one)' : ''}</em></legend>
        <div className="source-options">
          {sources.map((s) => {
            const on = sourceIds.includes(s.id)
            return (
              <label key={s.id} className={`source-option${on ? ' on' : ''}`}>
                <input type={many ? 'checkbox' : 'radio'} name="source" checked={on} onChange={() => toggle(s.id)} />
                <span className="source-option-text">
                  <strong>{s.name}</strong>
                  <span className="muted small">
                    {s.tables.length} {s.tables.length === 1 ? 'table' : 'tables'}: {s.tables.slice(0, 5).map((t) => t.name).join(', ')}
                    {s.tables.length > 5 ? '…' : ''}
                  </span>
                </span>
                {on && <Check size={16} className="source-check" aria-hidden />}
              </label>
            )
          })}
        </div>
        {chosen.length > 1 && <small className="muted">Each chart uses one source; Stardeck picks the right one for each.</small>}
      </fieldset>
      {unclear.map(({ s, n }) => (
        <p key={s.id} className="hint"><span>
          {n} column{n > 1 ? 's' : ''} in {s.name} could be misread.{' '}
          <Link to={`/sources/${s.id}/definitions`}>Explain them</Link> for better results (optional).
        </span></p>
      ))}

      <label className="field">
        <span>What should it focus on? <em>(optional)</em></span>
        <textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)}
          placeholder="Leave empty and Stardeck will pick the most useful metrics." />
      </label>
      <div className="chips">
        {GOAL_IDEAS.map((g) => <button key={g} className="chip" onClick={() => setGoal(g)}>{g}</button>)}
      </div>

      {paused ? <AiPaused />
        : buildsLeft === 0 && builds?.limit != null && (
          <AiUsedUp what="AI dashboard build" limit={builds.limit} resetsAt={usage?.ai.resetsAt} />
        )}
      <div className="form-actions">
        <button className="btn primary large" disabled={!sourceIds.length || paused || buildsLeft === 0} onClick={generate}>
          <Sparkles size={16} aria-hidden /> Generate dashboard
        </button>
        <button className="btn text large" onClick={() => navigate(-1)}>Cancel</button>
        {buildsLeft !== null && buildsLeft > 0 && (
          <span className="ai-left">{buildsLeft} of {builds?.limit} AI build{builds?.limit === 1 ? '' : 's'} left today</span>
        )}
      </div>
      </div>
      )}
    </div>
  )
}
