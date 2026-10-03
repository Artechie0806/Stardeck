import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { ArrowRight, Sparkles } from 'lucide-react'
import type { SharedDashboard } from '../api'
import { Logo, LogoMark } from '../components/Logo'
import { Tour, type TourStep } from '../components/Tour'
import { WaitlistDialog } from '../components/WaitlistDialog'
import { publicClient, ViewClientContext } from '../lib/viewClient'
import { DashboardView } from './ViewPage'

/** Charts that filter the others when clicked, best first for the tour. */
const CLICKABLE = ['hbar', 'bar', 'donut', 'pie', 'line', 'area', 'treemap', 'funnel']

const widgetEl = (id: string | undefined) => (id ? document.querySelector(`[data-widget-id="${CSS.escape(id)}"]`) : null)

function tourSteps(d: SharedDashboard): TourStep[] {
  const kpi = d.widgets.find((w) => w.spec.type === 'kpi')?.id
  const chart = CLICKABLE.map((t) => d.widgets.find((w) => w.spec.type === t)).find(Boolean)?.id
  const steps: TourStep[] = []
  if (kpi) {
    steps.push({ target: () => widgetEl(kpi), title: 'The numbers that matter',
      body: 'Each figure is compared with the same period last year, so you can see at a glance what went up and what went down.' })
  }
  steps.push({ target: () => document.querySelector('[data-tour="filters"]'), title: 'Pick a period or a filter',
    body: 'Choose dates like “This financial year” or “Last quarter”, or pick a city or category. Every chart follows.' })
  if (chart) {
    steps.push({ target: () => widgetEl(chart), title: 'Click a chart to filter the rest',
      body: 'Click a bar or a slice and the whole dashboard shows just that. Click it again to go back.' })
  }
  steps.push(d.share.demo
    ? { target: () => document.querySelector('[data-tour="waitlist"]'), title: 'Want this for your data?',
      body: 'Upload a spreadsheet or connect your database, and Stardeck builds a dashboard like this in about a minute. Join the waitlist for an invitation.' }
    : { target: () => null, title: 'That’s it',
      body: 'Change the dates and filters as much as you like: nothing you do here changes the dashboard for anyone else.' })
  return steps
}

/** A dashboard shared by link: anyone can open it, without signing in (see backend shares.py). */
export function PublicViewPage() {
  const { token = '' } = useParams()
  const client = useMemo(() => publicClient(token), [token])
  const [shared, setShared] = useState<SharedDashboard | null>(null)
  const [params, setParams] = useSearchParams()
  const touring = params.get('tour') === '1' && params.get('print') !== '1'
  const [joining, setJoining] = useState(false)

  useEffect(() => {
    let live = true
    client.shared().then((d) => { if (live) setShared(d) }).catch(() => {}) // the view shows the error
    return () => { live = false }
  }, [client])
  useEffect(() => {
    if (shared) document.title = `${shared.name} · Stardeck`
  }, [shared])

  const steps = useMemo(() => (shared ? tourSteps(shared) : []), [shared])
  const endTour = useCallback(() => setParams((p) => {
    const next = new URLSearchParams(p)
    next.delete('tour')
    return next
  }, { replace: true }), [setParams])
  const site = shared?.share.siteUrl?.replace(/\/$/, '') ?? null

  return (
    <ViewClientContext.Provider value={client}>
      {shared?.share.demo && (
        <div className="demo-bar no-print" data-tour="waitlist">
          <span><Sparkles size={15} aria-hidden /> A live demo with sample data. Want Stardeck for your own?</span>
          <button type="button" className="btn primary small" onClick={() => { endTour(); setJoining(true) }}>
            Join the waitlist <ArrowRight size={14} aria-hidden /></button>
        </div>
      )}
      <DashboardView id={token} client={client} isPublic
        title={<p className="crumb view-brand">{site ? <a href={site} aria-label="Stardeck"><Logo size={20} /></a> : <Logo size={20} />}</p>}
        onGone={site ? <a href={site} className="btn primary">About Stardeck</a> : null} />
      {shared?.share.badge && (
        site ? <a className="made-with no-print" href={site} target="_blank" rel="noreferrer"><LogoMark size={14} /> Made with Stardeck</a>
          : <span className="made-with no-print"><LogoMark size={14} /> Made with Stardeck</span>
      )}
      {touring && shared && steps.length > 0 && <Tour steps={steps} onClose={endTour} />}
      {joining && <WaitlistDialog siteUrl={site} onClose={() => setJoining(false)} />}
    </ViewClientContext.Provider>
  )
}

/** /demo: the current public demo, with the tour. */
export function DemoRedirect() {
  const navigate = useNavigate()
  const [missing, setMissing] = useState(false)
  useEffect(() => {
    fetch('/api/public/demo')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: { token: string }) => navigate(`/s/${encodeURIComponent(d.token)}?tour=1`, { replace: true }))
      .catch(() => setMissing(true))
  }, [navigate])
  if (!missing) return <div className="app-loading"><LogoMark size={56} animate label="Opening the demo" /></div>
  return (
    <div className="page narrow"><section className="empty"><h2>The demo isn’t available right now</h2>
      <p>Please try again in a little while.</p></section></div>
  )
}
