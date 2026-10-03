import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, LoaderCircle, Sparkles, X } from 'lucide-react'
import { errorFrom } from '../api/http'

const ROLES = ['Founder or owner', 'Finance', 'Sales or marketing', 'Operations', 'Analyst', 'Other']
const SOURCES = ['Excel or CSV files', 'PostgreSQL', 'MySQL', 'Something else']

/** Asking for an invitation from the public demo, without leaving it: the website's waitlist form
 *  (site/index.html), sent to the same endpoint (backend api/public.py). */
export function WaitlistDialog({ siteUrl, onClose }: { siteUrl: string | null; onClose: () => void }) {
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const doneRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [dialog])
  useEffect(() => {
    if (done) doneRef.current?.focus()
  }, [done])

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const email = form.elements.namedItem('email') as HTMLInputElement
    if (!email.checkValidity()) {
      setError('Please enter a valid email address.')
      email.focus()
      return
    }
    const data = Object.fromEntries(new FormData(form))
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/public/waitlist', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
      })
      if (res.status === 202) setDone(true)
      else if (res.status === 422) setError('Please enter a valid email address.')
      else setError((await errorFrom(res)).message)
    } catch {
      setError('We couldn’t send that. Please check your connection and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog ref={setDialog} className="modal waitlist-dialog" aria-labelledby="waitlist-title"
      onCancel={(e) => { e.preventDefault(); onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-body">
        <header className="modal-head">
          <span className="icon-tile"><Sparkles size={18} aria-hidden /></span>
          <div className="modal-title">
            <h2 id="waitlist-title">Get an invitation</h2>
            <p className="muted small">We let people in a few at a time. It’s free while Stardeck is in preview.</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </header>
        {done ? (
          <div className="modal-content waitlist-done" ref={doneRef} tabIndex={-1} role="status">
            <span className="icon-tile large"><Check size={26} aria-hidden /></span>
            <h3>You’re on the list.</h3>
            <p className="muted">Thank you. We’ll email you an invitation as soon as there’s room. Meanwhile, keep exploring the demo.</p>
            <button type="button" className="btn primary" onClick={onClose}>Back to the demo</button>
          </div>
        ) : (
          <form className="modal-body" onSubmit={(e) => void submit(e)} noValidate>
            <div className="modal-content">
              <label className="field"><span>Work email</span>
                <input type="email" name="email" required autoComplete="email" maxLength={200} placeholder="you@company.in" autoFocus /></label>
              <div className="two-col">
                <label className="field"><span>Name <em>optional</em></span>
                  <input type="text" name="name" autoComplete="name" maxLength={200} /></label>
                <label className="field"><span>Company <em>optional</em></span>
                  <input type="text" name="company" autoComplete="organization" maxLength={200} /></label>
                <label className="field"><span>Your role <em>optional</em></span>
                  <select name="role" defaultValue=""><option value="">Choose…</option>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></label>
                <label className="field"><span>Where your data lives <em>optional</em></span>
                  <select name="dataSource" defaultValue=""><option value="">Choose…</option>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></label>
              </div>
              <label className="field"><span>What would you like to see? <em>optional</em></span>
                <textarea name="useCase" rows={3} maxLength={2000} placeholder="Monthly sales by branch, and which products are slowing down" /></label>
              {/* The honeypot: people never see it, bots fill it in (backend waitlist.py). */}
              <label className="waitlist-honey" aria-hidden="true">Leave this empty
                <input type="text" name="website" tabIndex={-1} autoComplete="off" /></label>
              {error && <p className="test-result bad" role="alert"><span>{error}</span></p>}
            </div>
            <footer className="modal-foot">
              <small className="muted modal-foot-left">
                We’ll only email you about your invitation.{siteUrl && <> <a href={`${siteUrl}/privacy`} target="_blank" rel="noreferrer">Privacy policy</a></>}
              </small>
              <button type="submit" className="btn primary" disabled={busy}>
                {busy && <LoaderCircle size={15} className="spin" aria-hidden />} Request an invite
              </button>
            </footer>
          </form>
        )}
      </div>
    </dialog>
  )
}
