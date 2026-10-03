import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { CalendarRange, Check, Gem, KeyRound, LoaderCircle, ShieldCheck } from 'lucide-react'
import { api } from '../api'
import { useAuth, useCan } from '../lib/authContext'
import { loadConfig } from '../lib/config'
import { fyLabel, fyStart } from '../lib/dates'
import { UpgradeLink } from '../components/Upgrade'
import { usePlan, useUsage } from '../lib/plan'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
  'November', 'December']

/** Install-wide settings (admins): financial year, and turning on sign-in. */
export function SettingsPage() {
  const isAdmin = useCan('admin')
  if (!isAdmin) {
    return <div className="page narrow"><h1>Settings</h1><p className="muted">Only admins can change settings.</p></div>
  }
  return (
    <div className="page narrow-wide">
      <div className="page-head"><div><h1>Settings</h1><p className="muted">These apply to everyone using this Stardeck.</p></div></div>
      <PlanSection />
      <FiscalYear />
      <SignIn />
    </div>
  )
}

const amount = (n: number | null, one: string, many: string) =>
  n === null ? `Unlimited ${many}` : `${n} ${n === 1 ? one : many}`

/** Hosted: what the workspace's plan includes and how much of it is in use. */
function PlanSection() {
  const { plan } = usePlan()
  const { usage } = useUsage()
  if (!plan || plan.id === 'self_hosted') return null
  const l = plan.limits
  const used = (n: number | undefined) => (n === undefined ? '' : ` (${n} in use)`)
  return (
    <section className="panel settings-section">
      <h2><Gem size={17} aria-hidden /> {plan.name} plan</h2>
      <ul className="plan-list">
        <li>{amount(l.sources, 'data source', 'data sources')}{used(usage?.sources.used)}, plus the sample data</li>
        <li>{amount(l.dashboards, 'dashboard', 'dashboards')}{used(usage?.dashboards.used)}, plus the sample dashboard</li>
        <li>{l.sourcesPerDashboard === 1 ? 'One data source per dashboard' : `Up to ${l.sourcesPerDashboard} data sources per dashboard`}</li>
        <li>AI each day: {l.aiGenerationsPerDay ?? 'unlimited'} dashboard builds and {l.aiMessagesPerDay ?? 'unlimited'} assistant messages</li>
        {l.uploadMb !== null && <li>Uploads up to {l.uploadMb} MB</li>}
        <li>{plan.features.length === 0
          ? 'People and data access rules, measures, data preparation and multi-source dashboards come with Enterprise'
          : 'People and data access rules, measures, data preparation and multi-source dashboards included'}</li>
        {plan.badge && <li>Shared dashboards show “Made with Stardeck”</li>}
      </ul>
      {plan.id === 'community' && <p className="plan-upgrade"><UpgradeLink label="Upgrade to Enterprise" /></p>}
    </section>
  )
}

function FiscalYear() {
  const [month, setMonth] = useState<number | null>(null)
  const [saved, setSaved] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [today] = useState(() => new Date())

  useEffect(() => {
    loadConfig().then((c) => { setMonth(c.fiscalYearStart); setSaved(c.fiscalYearStart) }).catch((e: Error) => setError(e.message))
  }, [])

  const save = async () => {
    if (month === null) return
    setBusy(true)
    setError(null)
    try {
      const c = await api.updateSettings({ fiscalYearStart: month })
      await loadConfig(true)
      setSaved(c.fiscalYearStart)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const example = month ? fyStart(today, month) : null
  return (
    <section className="panel settings-section">
      <h2><CalendarRange size={17} aria-hidden /> Financial year</h2>
      <p className="muted">Used by the “financial year” date ranges, quarters, and when you ask the assistant about a financial year.</p>
      <div className="settings-row">
        <label className="field"><span>Starts in</span>
          <select value={month ?? ''} onChange={(e) => setMonth(Number(e.target.value))} disabled={month === null}>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}{i === 3 ? ' (India)' : i === 0 ? ' (calendar year)' : ''}</option>)}
          </select>
        </label>
        <button className="btn primary" disabled={busy || month === null || month === saved} onClick={save}>
          {busy ? <LoaderCircle size={15} className="spin" aria-hidden /> : saved === month ? <Check size={15} aria-hidden /> : null}
          {saved === month ? 'Saved' : 'Save'}
        </button>
      </div>
      {example && month && (
        <p className="hint"><span>The current financial year is <strong>{fyLabel(example, month)}</strong>, starting{' '}
          {example.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}.</span></p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}

function SignIn() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (auth.mode === 'users') {
    return (
      <section className="panel settings-section">
        <h2><ShieldCheck size={17} aria-hidden /> Sign-in</h2>
        <p>{auth.hosted ? <>Everyone in {auth.workspace?.name ? <strong>{auth.workspace.name}</strong> : 'this workspace'} signs in
          with their own email and has a role.</> : 'Sign-in is on: everyone has their own account and role.'}{' '}
          <Link to="/people">Manage people</Link>.</p>
      </section>
    )
  }

  const mismatch = confirm.length > 0 && confirm !== password
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (password !== confirm) return
    setBusy(true)
    setError(null)
    try {
      await api.setupAuth(username.trim(), name.trim(), password)
      await auth.refresh()
      navigate('/people')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel settings-section">
      <h2><KeyRound size={17} aria-hidden /> Turn on sign-in</h2>
      <p className="muted">
        {auth.mode === 'open'
          ? 'Right now anyone who can open Stardeck can see and change everything. '
          : 'Right now everyone signs in with one shared password. '}
        Create your admin account to give people their own sign-in, a role (viewer, editor or admin), and, for viewers,
        rules about which rows they can see.
      </p>
      <form className="setup-form" onSubmit={submit}>
        <div className="two-col">
          <label className="field"><span>Your name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Asha Rao" autoComplete="name" /></label>
          <label className="field"><span>Username</span>
            <input required value={username} autoCapitalize="none" spellCheck={false} autoComplete="username"
              onChange={(e) => setUsername(e.target.value.toLowerCase())} placeholder="e.g. asha" /></label>
        </div>
        <div className="two-col">
          <label className="field"><span>Password <em>(8+ characters)</em></span>
            <input required type="password" minLength={8} autoComplete="new-password" value={password}
              onChange={(e) => setPassword(e.target.value)} /></label>
          <label className="field"><span>Confirm password</span>
            <input required type="password" minLength={8} autoComplete="new-password" value={confirm} aria-invalid={mismatch}
              aria-describedby={mismatch ? 'confirm-hint' : undefined} onChange={(e) => setConfirm(e.target.value)} />
            {mismatch && <small id="confirm-hint" className="danger-text">The passwords don’t match.</small>}
          </label>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="form-actions">
          <button className="btn primary" disabled={busy || !username || password.length < 8 || confirm !== password}>
            {busy && <LoaderCircle size={15} className="spin" aria-hidden />} Create admin and turn on sign-in
          </button>
        </div>
      </form>
    </section>
  )
}
