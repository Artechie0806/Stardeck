import type { ReactNode } from 'react'
import { ArrowUpRight, Lock } from 'lucide-react'
import { usePlan } from '../lib/plan'

/** "Upgrade" link to the plans page (or a plain hint when none is configured). */
export function UpgradeLink({ label = 'See plans' }: { label?: string }) {
  const { upgradeUrl } = usePlan()
  if (!upgradeUrl) return <span className="muted">Ask us about Enterprise.</span>
  return <a className="upgrade-link" href={upgradeUrl} target="_blank" rel="noreferrer">{label} <ArrowUpRight size={13} aria-hidden /></a>
}

/** A one-line "this needs Enterprise" note next to a locked control. */
export function UpgradeNote({ children }: { children: ReactNode }) {
  return (
    <p className="upgrade-note"><Lock size={13} aria-hidden /> <span>{children} <UpgradeLink /></span></p>
  )
}

/** A whole page or section that's part of Enterprise. */
export function UpgradePanel({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="empty upgrade-panel">
      <span className="icon-tile large">{icon}</span>
      <span className="plan-pill"><Lock size={11} aria-hidden /> Enterprise</span>
      <h2>{title}</h2>
      <p>{children}</p>
      <UpgradeLink label="See what’s in Enterprise" />
    </section>
  )
}
