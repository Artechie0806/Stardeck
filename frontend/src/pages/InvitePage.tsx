import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { KeyRound, LoaderCircle, UserPlus } from 'lucide-react'
import { api } from '../api'
import type { InviteInfo } from '../api/types'
import { Logo } from '../components/Logo'

/** An invitation (choose your name and password, join the workspace) or a password-reset link.
 *  Shown without signing in; signs you in when done. */
export function InvitePage({ token, onDone }: { token: string; onDone: () => Promise<void> }) {
  const navigate = useNavigate()
  const [info, setInfo] = useState<InviteInfo | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.inviteInfo(token).then(setInfo).catch((e: Error) => setProblem(e.message))
  }, [token])

  const mismatch = confirm.length > 0 && confirm !== password
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (password !== confirm) return
    setBusy(true)
    setError(null)
    try {
      await api.acceptInvite(token, name.trim(), password)
      await onDone()
      navigate('/', { replace: true })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const reset = info?.kind === 'reset'
  return (
    <main className="signin">
      <div className="panel signin-card">
        <div className="brand"><Logo size={30} animate /></div>
        {problem ? (
          <>
            <h1>This link doesn’t work</h1>
            <p className="muted">{problem}</p>
            <p className="muted small">Links work once and expire after a few days. Ask whoever sent it for a new one.</p>
            <div className="form-actions"><a className="btn primary" href="/">Go to sign in</a></div>
          </>
        ) : !info ? (
          <p className="muted"><LoaderCircle size={15} className="spin" aria-hidden /> Checking your link…</p>
        ) : (
          <form onSubmit={submit}>
            <h1>{reset ? <><KeyRound size={18} aria-hidden /> Choose a new password</> : <><UserPlus size={18} aria-hidden /> Join {info.workspace || 'Stardeck'}</>}</h1>
            <p className="muted">{reset ? <>For <strong>{info.email}</strong>. You’ll be signed out everywhere else.</>
              : <>You’ll sign in with <strong>{info.email}</strong>.</>}</p>
            {!reset && (
              <label className="field"><span>Your name</span>
                <input autoFocus value={name} autoComplete="name" placeholder="e.g. Asha Rao" onChange={(e) => setName(e.target.value)} /></label>
            )}
            {/* Lets password managers save the new password against the right account. */}
            <input type="email" autoComplete="username" value={info.email} readOnly hidden />
            <label className="field"><span>{reset ? 'New password' : 'Password'} <em>(8+ characters)</em></span>
              <input type="password" autoFocus={reset} required minLength={8} autoComplete="new-password" value={password}
                onChange={(e) => setPassword(e.target.value)} /></label>
            <label className="field"><span>Confirm password</span>
              <input type="password" required minLength={8} autoComplete="new-password" value={confirm} aria-invalid={mismatch}
                onChange={(e) => setConfirm(e.target.value)} />
              {mismatch && <small className="danger-text">The passwords don’t match.</small>}
            </label>
            {error && <p className="error" role="alert">{error}</p>}
            <div className="form-actions">
              <button className="btn primary" disabled={busy || password.length < 8 || password !== confirm}>
                {busy && <LoaderCircle size={15} className="spin" aria-hidden />} {reset ? 'Save and sign in' : 'Join and sign in'}
              </button>
            </div>
          </form>
        )}
      </div>
    </main>
  )
}
