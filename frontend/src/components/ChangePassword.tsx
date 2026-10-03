import { useEffect, useState, type FormEvent } from 'react'
import { Check, KeyRound, LoaderCircle, X } from 'lucide-react'
import { api } from '../api'

/** Change your own password. Other browsers signed in as you are signed out. */
export function ChangePassword({ onClose }: { onClose: () => void }) {
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [dialog])

  const mismatch = confirm.length > 0 && confirm !== next
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (next !== confirm) return
    setBusy(true)
    setError(null)
    try {
      await api.changePassword(current, next)
      setDone(true)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog ref={setDialog} className="modal password-dialog" aria-labelledby="password-title"
      onCancel={(e) => { e.preventDefault(); if (!busy) onClose() }}
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <form className="modal-body" onSubmit={submit}>
        <header className="modal-head">
          <span className="icon-tile"><KeyRound size={18} aria-hidden /></span>
          <div className="modal-title">
            <h2 id="password-title">Change password</h2>
            <p className="muted small">You’ll stay signed in here; other browsers are signed out.</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={busy}><X size={18} /></button>
        </header>
        <div className="modal-content">
          {done ? (
            <p className="test-result ok" role="status"><Check size={16} aria-hidden /> <span>Your password was changed.</span></p>
          ) : (
            <>
              <label className="field"><span>Current password</span>
                <input type="password" required autoFocus autoComplete="current-password" value={current}
                  onChange={(e) => setCurrent(e.target.value)} /></label>
              <label className="field"><span>New password <em>(8+ characters)</em></span>
                <input type="password" required minLength={8} autoComplete="new-password" value={next}
                  onChange={(e) => setNext(e.target.value)} /></label>
              <label className="field"><span>Confirm new password</span>
                <input type="password" required minLength={8} autoComplete="new-password" value={confirm} aria-invalid={mismatch}
                  onChange={(e) => setConfirm(e.target.value)} />
                {mismatch && <small className="danger-text">The passwords don’t match.</small>}
              </label>
              {error && <p className="test-result bad" role="alert"><span>{error}</span></p>}
            </>
          )}
        </div>
        <footer className="modal-foot">
          {done ? <button type="button" className="btn primary" autoFocus onClick={onClose}>Done</button> : (
            <>
              <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
              <button type="submit" className="btn primary" disabled={busy || !current || next.length < 8 || next !== confirm}>
                {busy && <LoaderCircle size={15} className="spin" aria-hidden />} Change password
              </button>
            </>
          )}
        </footer>
      </form>
    </dialog>
  )
}
