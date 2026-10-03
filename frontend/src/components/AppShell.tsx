import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router'
import { Database, LayoutDashboard, LogOut, Monitor, Moon, Sun } from 'lucide-react'
import { api, type Health } from '../api'
import { useAuth } from '../lib/authContext'
import { useTheme, type ThemeChoice } from '../lib/themeContext'

const THEMES: { value: ThemeChoice; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'system', icon: Monitor, label: 'System' },
  { value: 'dark', icon: Moon, label: 'Dark' },
]

export function AppShell() {
  const { choice, setChoice } = useTheme()
  const auth = useAuth()
  const [health, setHealth] = useState<Health | null>(null)
  const [backendDown, setBackendDown] = useState(false)

  useEffect(() => {
    api.getHealth().then(setHealth).catch(() => setBackendDown(true))
  }, [])

  const llm = health?.llm
  const modelOk = llm?.reachable && llm.model_available !== false
  const modelText = backendDown ? 'Backend offline'
    : !llm ? 'Checking…'
    : !llm.reachable ? 'Model server offline'
    : llm.model_available === false ? `${llm.model} not loaded`
    : llm.model

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">✦ Stardeck</div>
        <NavLink to="/" end className="nav-link"><LayoutDashboard size={17} aria-hidden /> Dashboards</NavLink>
        <NavLink to="/sources" className="nav-link"><Database size={17} aria-hidden /> Data sources</NavLink>

        <div className="sidebar-foot">
          <div className="model-status" title={llm?.error ?? llm?.base_url}>
            <span className={`dot ${backendDown || (llm && !modelOk) ? 'bad' : modelOk ? 'ok' : ''}`} aria-hidden />
            <span className="model-name">{modelText}</span>
          </div>
          {auth.required && (
            <button className="nav-link signout" onClick={auth.signOut}><LogOut size={15} aria-hidden /> Sign out</button>
          )}
          <div className="segmented small" role="radiogroup" aria-label="Theme">
            {THEMES.map(({ value, icon: Icon, label }) => (
              <button key={value} role="radio" aria-checked={choice === value} title={label}
                className={choice === value ? 'selected' : ''} onClick={() => setChoice(value)}>
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>
      </nav>
      <main className="main"><Outlet /></main>
    </div>
  )
}
