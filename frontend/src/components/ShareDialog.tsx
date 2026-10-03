import { useEffect, useState } from 'react'
import { Check, Copy, Globe, LoaderCircle, Share2, TriangleAlert, Users, X } from 'lucide-react'
import { api, type ShareLink } from '../api'
import { copyText } from '../lib/browser'
import { useAppConfig } from '../lib/config'
import { usePlan } from '../lib/plan'
import { UpgradeLink } from './Upgrade'

const EXPIRY: { days: number | null; label: string }[] = [
  { days: null, label: 'Never expires' }, { days: 7, label: 'Expires in 7 days' },
  { days: 30, label: 'Expires in 30 days' }, { days: 90, label: 'Expires in 90 days' },
]
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

/** Public links to a dashboard (anyone with the link can look, no sign-in), and the link for people
 *  in the workspace. Editors only; viewers just copy the workspace link. */
export function ShareDialog({ dashboardId, name, onClose }: { dashboardId: string; name: string; onClose: () => void }) {
  const config = useAppConfig()
  const { plan } = usePlan()
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null)
  const [links, setLinks] = useState<ShareLink[] | null>(null)
  const [expiry, setExpiry] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)

  useEffect(() => {
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [dialog])
  useEffect(() => {
    api.listShares(dashboardId).then(setLinks).catch((e: Error) => setError(e.message))
  }, [dashboardId])

  const base = (config?.shareUrl || window.location.origin).replace(/\/$/, '')
  const linkUrl = (token: string) => `${base}/s/${token}`
  const workspaceUrl = `${window.location.origin}/view/${dashboardId}`
  const copy = async (text: string, key: string) => {
    if (await copyText(text)) {
      setCopied(key)
      setTimeout(() => setCopied((c) => (c === key ? null : c)), 2000)
    } else {
      setError(`Copy this link: ${text}`)
    }
  }
  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const link = await api.createShare(dashboardId, expiry)
      setLinks((l) => [link, ...(l ?? [])])
      void copy(linkUrl(link.token), link.token)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const revoke = async (token: string) => {
    setError(null)
    try {
      await api.revokeShare(dashboardId, token)
      setLinks((l) => l?.filter((x) => x.token !== token) ?? null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setConfirming(null)
    }
  }

  return (
    <dialog ref={setDialog} className="modal share-dialog" aria-labelledby="share-title"
      onCancel={(e) => { e.preventDefault(); onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-body">
        <header className="modal-head">
          <span className="icon-tile"><Share2 size={18} aria-hidden /></span>
          <div className="modal-title">
            <h2 id="share-title">Share “{name}”</h2>
            <p className="muted small">Links open a read-only view. Nobody can change the dashboard through them.</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        <div className="modal-content">
          <section className="share-section">
            <h3><Globe size={15} aria-hidden /> Anyone with the link</h3>
            <p className="muted small">
              No sign-in needed. They can change the dates and filters and click charts, but can’t see your SQL,
              edit anything or use the assistant.
            </p>
            <p className="share-warning small"><TriangleAlert size={14} aria-hidden />
              <span>They see all the data in these charts: data access rules for people in your workspace don’t apply.</span></p>
            {plan?.badge && (
              <p className="muted small">Shared dashboards show a small “Made with Stardeck” badge. <UpgradeLink label="Remove it with Enterprise" /></p>
            )}
            {links === null ? <p className="muted small"><LoaderCircle size={14} className="spin" aria-hidden /> Loading links…</p> : (
              <ul className="share-links">
                {links.map((l) => (
                  <li key={l.token} className={l.active ? undefined : 'expired'}>
                    <div className="share-url">
                      <input readOnly value={linkUrl(l.token)} aria-label="Share link" onFocus={(e) => e.currentTarget.select()} />
                      <button className="btn ghost small" onClick={() => void copy(linkUrl(l.token), l.token)} disabled={!l.active}>
                        {copied === l.token ? <><Check size={14} aria-hidden /> Copied</> : <><Copy size={14} aria-hidden /> Copy</>}
                      </button>
                    </div>
                    <div className="share-meta muted small">
                      <span>
                        {l.active ? `Made ${day(l.createdAt)}` : 'Expired'} · {l.views} view{l.views === 1 ? '' : 's'}
                        {l.active && (l.expiresAt ? ` · Expires ${day(l.expiresAt)}` : ' · Never expires')}
                      </span>
                      {confirming === l.token ? (
                        <span className="share-confirm">Turn off? People with it can’t open the dashboard any more.
                          <button className="btn danger small" onClick={() => void revoke(l.token)}>Turn off</button>
                          <button className="btn text small" onClick={() => setConfirming(null)}>Keep</button></span>
                      ) : (
                        <button className="btn text small" onClick={() => setConfirming(l.token)}>{l.active ? 'Turn off' : 'Remove'}</button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <div className="share-create">
              <select value={expiry ?? ''} onChange={(e) => setExpiry(e.target.value ? Number(e.target.value) : null)} aria-label="Link expiry">
                {EXPIRY.map((o) => <option key={o.label} value={o.days ?? ''}>{o.label}</option>)}
              </select>
              <button className="btn primary" onClick={() => void create()} disabled={busy}>
                {busy && <LoaderCircle size={15} className="spin" aria-hidden />} {links?.length ? 'Create another link' : 'Create link'}
              </button>
            </div>
          </section>
          <section className="share-section">
            <h3><Users size={15} aria-hidden /> People in your workspace</h3>
            <p className="muted small">They sign in to open it, and see only the data their access rules allow.</p>
            <button className="btn ghost" onClick={() => void copy(workspaceUrl, 'workspace')}>
              {copied === 'workspace' ? <><Check size={15} aria-hidden /> Copied</> : <><Copy size={15} aria-hidden /> Copy workspace link</>}
            </button>
          </section>
          {error && <p className="test-result bad" role="alert"><span>{error}</span></p>}
        </div>
      </div>
    </dialog>
  )
}
