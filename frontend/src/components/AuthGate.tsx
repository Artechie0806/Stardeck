import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { LoaderCircle, Lock } from 'lucide-react'
import { api, UNAUTHORIZED_EVENT } from '../api'
import { AuthContext } from '../lib/authContext'
import { Logo, LogoMark } from './Logo'

/** Shows a sign-in screen when the server has STARDECK_PASSWORD set and there's no session. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'checking' | 'in' | 'out'>('checking')
  const [required, setRequired] = useState(false)

  useEffect(() => {
    api.authStatus()
      .then((s) => { setRequired(s.required); setState(s.authenticated ? 'in' : 'out') })
      .catch(() => setState('in')) // backend unreachable: let pages show their own errors
    const onUnauthorized = () => { setRequired(true); setState('out') }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  const signOut = async () => {
    await api.logout().catch(() => {})
    setState('out')
  }

  if (state === 'checking') return <div className="app-loading"><LogoMark size={56} animate label="Loading Stardeck" /></div>
  if (state === 'out') return <SignIn onDone={() => setState('in')} />
  return <AuthContext.Provider value={{ required, signOut }}>{children}</AuthContext.Provider>
}

function SignIn({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(password)
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="signin">
      <form className="panel signin-card" onSubmit={submit}>
        <div className="brand"><Logo size={30} animate /></div>
        <h1><Lock size={18} aria-hidden /> Sign in</h1>
        <p className="muted">This Stardeck is password protected.</p>
        <label className="field"><span>Password</span>
          <input type="password" autoFocus autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="form-actions">
          <button className="btn primary" disabled={!password || busy}>
            {busy && <LoaderCircle size={15} className="spin" aria-hidden />} Sign in
          </button>
        </div>
      </form>
    </main>
  )
}
