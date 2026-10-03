import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'
import { Database, KeyRound, LayoutDashboard, LogOut, Monitor, Moon, Settings, Sparkles, Sun, Users } from 'lucide-react'
import { useAiStatus } from '../lib/aiStatus'
import { useAuth, useCan } from '../lib/authContext'
import { ChangePassword } from './ChangePassword'
import { Logo } from './Logo'
import { useTheme, type ThemeChoice } from '../lib/themeContext'

const THEMES: { value: ThemeChoice; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'system', icon: Monitor, label: 'System' },
  { value: 'dark', icon: Moon, label: 'Dark' },
]

export function AppShell() {
  const { choice, setChoice } = useTheme()
  const auth = useAuth()
  const canEdit = useCan('editor')
  const isAdmin = useCan('admin')
  const { health, backendDown } = useAiStatus()  // checked every minute
  const [changingPassword, setChangingPassword] = useState(false)

  const llm = health?.llm
  const modelOk = llm?.reachable && llm.model_available !== false
  // Plain words for everyone; admins get the model and server on hover (they're the ones who can fix it).
  const modelText = backendDown ? 'Backend offline'
    : !llm ? 'Checking AI…'
    : !llm.reachable ? 'AI not connected'
    : llm.model_available === false ? 'AI model not loaded'
    : 'AI connected'
  const modelDetail = isAdmin && llm ? [llm.model, llm.error ?? llm.base_url].filter(Boolean).join(' · ') : undefined

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><NavLink to="/" aria-label="Stardeck home"><Logo size={26} animate /></NavLink></div>
        <div className="nav-links">
          <NavLink to="/" end className="nav-link"><LayoutDashboard size={17} aria-hidden /> Dashboards</NavLink>
          {canEdit && <NavLink to="/sources" className="nav-link"><Database size={17} aria-hidden /> Data sources</NavLink>}
          {isAdmin && <NavLink to="/people" className="nav-link"><Users size={17} aria-hidden /> People</NavLink>}
          {isAdmin && <NavLink to="/settings" className="nav-link"><Settings size={17} aria-hidden /> Settings</NavLink>}
          {canEdit && <NavLink to="/new" className="nav-link nav-new"><Sparkles size={17} aria-hidden /> New dashboard</NavLink>}
        </div>

        <div className="sidebar-foot">
          <div className="model-status" title={modelDetail || modelText}>
            <span className={`dot ${backendDown || (llm && !modelOk) ? 'bad' : modelOk ? 'ok' : ''}`} aria-hidden />
            <span className="model-name">{modelText}</span>
          </div>
          {auth.user && auth.mode === 'users' ? (
            <div className="whoami">
              <span className="avatar" role="img" title={`${auth.user.name || auth.user.username} (${auth.user.username}), ${auth.user.role}`}
                aria-label={`Signed in as ${auth.user.name || auth.user.username}, ${auth.user.role}`}>
                {(auth.user.name || auth.user.username).slice(0, 1).toUpperCase()}</span>
              <span className="whoami-text" aria-hidden>
                <strong title={auth.user.name || auth.user.username}>{auth.user.name || auth.user.username}</strong>
                <span className={`role-badge role-${auth.user.role}`}>{auth.user.role}</span></span>
              <button className="icon-btn account-btn" onClick={() => setChangingPassword(true)} title="Change password"
                aria-label="Change password"><KeyRound size={15} aria-hidden /></button>
              <button className="icon-btn signout-btn" onClick={auth.signOut} title="Sign out" aria-label="Sign out">
                <LogOut size={16} aria-hidden />
              </button>
            </div>
          ) : auth.required && (
            <button className="nav-link signout" onClick={auth.signOut} title="Sign out">
              <LogOut size={15} aria-hidden /> <span className="signout-text">Sign out</span>
            </button>
          )}
          <div className="theme-row">
            <span className="theme-label" aria-hidden>Theme</span>
            <div className="segmented small" role="radiogroup" aria-label="Theme">
              {THEMES.map(({ value, icon: Icon, label }) => (
                <button key={value} role="radio" aria-checked={choice === value} title={label} aria-label={label}
                  className={choice === value ? 'selected' : ''} onClick={() => setChoice(value)}>
                  <Icon size={15} />
                </button>
              ))}
            </div>
          </div>
        </div>
      </nav>
      <main className="main"><Outlet /></main>
      {changingPassword && <ChangePassword onClose={() => setChangingPassword(false)} />}
      {/* Phones: the main links live in a bottom tab bar, within thumb reach. */}
      <nav className="tabbar" aria-label="Main (mobile)">
        <NavLink to="/" end><LayoutDashboard size={20} aria-hidden /><span>Dashboards</span></NavLink>
        {canEdit && <NavLink to="/new" className="tab-new"><span className="tab-new-icon"><Sparkles size={20} aria-hidden /></span><span>New</span></NavLink>}
        {canEdit && <NavLink to="/sources"><Database size={20} aria-hidden /><span>Sources</span></NavLink>}
        {isAdmin && <NavLink to="/people"><Users size={20} aria-hidden /><span>People</span></NavLink>}
      </nav>
    </div>
  )
}
