import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { Eye, EyeOff, LoaderCircle, Lock } from 'lucide-react'
import { api, ApiError, UNAUTHORIZED_EVENT } from '../api'
import type { AuthStatus } from '../api/types'
import { AuthContext } from '../lib/authContext'
import { resetConfig } from '../lib/config'
import { InvitePage } from '../pages/InvitePage'
import { Logo, LogoMark } from './Logo'

/** Signs people in when sign-in is on (a shared password, or accounts with roles). Invitation and
 *  password-reset links open without signing in. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [offline, setOffline] = useState(false)
  const { pathname } = useLocation()

  const refresh = useCallback(async () => {
    resetConfig()  // signing in or out may switch workspace (and plan)
    try {
      setStatus(await api.authStatus())
    } catch {
      setOffline(true) // backend unreachable: let pages show their own errors
    }
  }, [])

  useEffect(() => {
    api.authStatus().then(setStatus).catch(() => setOffline(true))
    const onUnauthorized = () => void refresh()
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [refresh])

  const signOut = async () => {
    await api.logout().catch(() => {})
    await refresh()
  }

  const link = pathname.match(/^\/(invite|reset)\/([^/]+)/)
  if (link) return <InvitePage token={link[2]} onDone={refresh} />
  if (!status && !offline) return <div className="app-loading"><LogoMark size={56} animate label="Loading Stardeck" /></div>
  if (status && !status.authenticated) return <SignIn mode={status.mode} hosted={!!status.hosted} onDone={refresh} />
  return (
    <AuthContext.Provider value={{
      mode: status?.mode ?? 'open', required: !!status?.required, user: status?.user ?? null,
      hosted: !!status?.hosted, workspace: status?.workspace ?? null, signOut, refresh,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

function SignIn({ mode, hosted, onDone }: { mode: AuthStatus['mode']; hosted: boolean; onDone: () => Promise<void> }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Browsers fill in saved passwords; seeing the field is the quickest way to spot someone else's.
  const [show, setShow] = useState(false)
  const accounts = mode === 'users'

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(password, accounts ? username : undefined)
      await onDone()
    } catch (err) {
      const wrong = accounts && err instanceof ApiError && err.status === 401
      setError(!wrong ? (err as Error).message : hosted
        ? 'Wrong email or password. If your browser filled in a saved password, clear it and type yours.'
        : 'Wrong username or password. Use your username (like “ravi”), not your full name. If your browser filled in a saved password, clear it and type yours.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="signin">
      <form className="panel signin-card" onSubmit={submit}>
        <div className="brand"><Logo size={30} animate /></div>
        <h1><Lock size={18} aria-hidden /> Sign in</h1>
        <p className="muted">{hosted ? 'Welcome back. Sign in with your email.'
          : accounts ? 'Use the username and password your admin gave you.' : 'This Stardeck is password protected.'}</p>
        {accounts && (
          <label className="field"><span>{hosted ? 'Email' : 'Username'}</span>
            <input autoFocus type={hosted ? 'email' : 'text'} autoComplete="username" autoCapitalize="none" spellCheck={false}
              value={username} placeholder={hosted ? 'you@company.com' : 'e.g. ravi'} onChange={(e) => setUsername(e.target.value)} /></label>
        )}
        <label className="field"><span>Password</span>
          <span className="input-affix">
            <input type={show ? 'text' : 'password'} autoFocus={!accounts} autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} />
            <button type="button" className="icon-btn" onClick={() => setShow((v) => !v)}
              aria-label={show ? 'Hide password' : 'Show password'} title={show ? 'Hide password' : 'Show password'}>
              {show ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </span></label>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="form-actions">
          <button className="btn primary" disabled={!password || (accounts && !username) || busy}>
            {busy && <LoaderCircle size={15} className="spin" aria-hidden />} Sign in
          </button>
        </div>
        {accounts && (
          <p className="muted small signin-help">
            Forgot your password? {hosted ? 'Ask your workspace admin, or the person who invited you, for a reset link.'
              : 'Ask an admin to set a new one for you on the People page.'}
          </p>
        )}
      </form>
    </main>
  )
}
