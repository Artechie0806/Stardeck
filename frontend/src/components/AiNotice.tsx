import { useState } from 'react'
import { CloudOff, Hourglass } from 'lucide-react'
import { recheckAi } from '../lib/aiStatus'
import { useAppConfig } from '../lib/config'
import { usePlan } from '../lib/plan'
import { UpgradeLink } from './Upgrade'

/** The AI is offline: say so plainly, and that everything else still works. */
export function AiPaused() {
  const hosted = useAppConfig()?.hosted
  const [checking, setChecking] = useState(false)
  const check = async () => {
    setChecking(true)
    await recheckAi()
    setChecking(false)
  }
  return (
    <div className="ai-notice" role="status">
      <CloudOff size={16} aria-hidden />
      <span>
        <strong>AI is paused:</strong>{' '}
        {hosted ? 'our AI server is offline. Dashboards, filters and sharing keep working.'
          : 'the AI model isn’t reachable. Dashboards and filters keep working. Check that your AI server is running.'}
        {' '}
        <button className="btn text small" onClick={check} disabled={checking}>{checking ? 'Checking…' : 'Check again'}</button>
      </span>
    </div>
  )
}

/** Today's allowance is used up. `what` is singular, e.g. "assistant message". */
export function AiUsedUp({ what, limit, resetsAt }: { what: string; limit: number; resetsAt?: string }) {
  const { plan } = usePlan()
  const at = resetsAt ? new Date(resetsAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : null
  return (
    <div className="ai-notice" role="status">
      <Hourglass size={16} aria-hidden />
      <span>
        You’ve used today’s {limit} {what}{limit === 1 ? '' : 's'}. You’ll get more at {at ?? 'midnight (India time)'}.
        {plan?.id === 'community' && <> <UpgradeLink label="Get more with Enterprise" /></>}
      </span>
    </div>
  )
}
