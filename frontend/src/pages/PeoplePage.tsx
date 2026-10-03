import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  Ban, Check, Copy, Dices, Eye, KeyRound, LoaderCircle, Pencil, Plus, Search, Shield, ShieldCheck, Trash2, UserPlus, Users, X,
} from 'lucide-react'
import { api, type AccessRule, type Cell, type ColumnValues, type DataSource, type Role, type UserAccount } from '../api'
import { ConfirmDelete } from '../components/ConfirmDelete'
import { UpgradePanel } from '../components/Upgrade'
import { useAuth, useCan } from '../lib/authContext'
import { copyText } from '../lib/browser'
import { formatCategory, relativeTime } from '../lib/format'
import { usePlan } from '../lib/plan'
import { PortalHost } from '../lib/portalHost'

const ROLES: { value: Role; label: string; text: string }[] = [
  { value: 'viewer', label: 'Viewer', text: 'Opens dashboards, uses filters, asks the data analyst. Sees only the rows their rules allow.' },
  { value: 'editor', label: 'Editor', text: 'Also builds and changes dashboards, definitions, measures and prepared tables. Sees all data.' },
  { value: 'admin', label: 'Admin', text: 'Also connects data sources and manages people and settings.' },
]

/** People who can sign in, their roles, and which rows viewers may see (admins). */
export function PeoplePage() {
  const isAdmin = useCan('admin')
  const auth = useAuth()
  const { has, loaded } = usePlan()
  const allowed = loaded && has('people')
  const [users, setUsers] = useState<UserAccount[] | null>(null)
  const [sources, setSources] = useState<DataSource[]>([])
  const [editing, setEditing] = useState<UserAccount | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.listUsers().then(setUsers).catch((e: Error) => setError(e.message))
  }, [])
  useEffect(() => {
    if (!isAdmin || auth.mode !== 'users' || !allowed) return
    load()
    api.listSources().then(setSources).catch(() => {})
  }, [isAdmin, auth.mode, load, allowed])

  if (!isAdmin) return <div className="page narrow"><h1>People</h1><p className="muted">Only admins can manage people.</p></div>
  if (auth.mode !== 'users') {
    return (
      <div className="page narrow">
        <h1>People</h1>
        <section className="empty">
          <span className="icon-tile large"><Users size={26} aria-hidden /></span>
          <h2>Sign-in is off</h2>
          <p>Turn on sign-in to give people their own accounts, roles and data access rules.</p>
          <Link to="/settings" className="btn primary">Turn on sign-in</Link>
        </section>
      </div>
    )
  }

  if (loaded && !allowed) {
    return (
      <div className="page narrow">
        <h1>People</h1>
        <UpgradePanel icon={<Users size={26} aria-hidden />} title="Bring your team">
          Invite colleagues as viewers, editors or admins, and limit what each viewer sees (for example, only their
          branch). Your Community workspace is just for you.
        </UpgradePanel>
      </div>
    )
  }

  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name ?? id
  return (
    <div className="page narrow-wide">
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p className="muted">Who can sign in, what they can do, and which rows viewers see.</p>
        </div>
        <button className="btn primary" onClick={() => setEditing('new')}><UserPlus size={16} aria-hidden /> Add person</button>
      </div>
      {error && <p className="error banner" role="alert">{error}</p>}
      {users === null && <div className="card-grid" aria-busy="true">{[0, 1].map((i) => <div key={i} className="dash-card skeleton" />)}</div>}
      <div className="people-list">
        {users?.map((u) => (
          <div key={u.id} className={`person${u.disabled ? ' disabled' : ''}`}>
            <span className="avatar large" aria-hidden>{(u.name || u.username).slice(0, 1).toUpperCase()}</span>
            <div className="person-main">
              <div className="person-name">
                <strong>{u.name || u.username}</strong>
                <span className={`role-badge role-${u.role}`}>{u.role}</span>
                {u.disabled && <span className="badge"><Ban size={11} aria-hidden /> Can’t sign in</span>}
                {u.id === auth.user?.id && <span className="badge">You</span>}
              </div>
              <p className="muted small">Signs in as <strong className="username">{u.username}</strong> ·{' '}
                {u.lastLoginAt ? `last signed in ${relativeTime(u.lastLoginAt)}` : 'hasn’t signed in yet'}</p>
              {u.role === 'viewer' && (
                <p className="small person-rules">
                  {u.rules.length === 0 ? <><Eye size={12} aria-hidden /> Sees all rows</>
                    : u.rules.map((r, i) => (
                      <span key={i} className="badge accent"><Shield size={11} aria-hidden />
                        {sources.length > 1 ? `${sourceName(r.sourceId)}: ` : ''}{r.table}.{r.column} is {r.values.map(formatCategory).join(', ') || 'nothing'}
                      </span>
                    ))}
                </p>
              )}
            </div>
            <button className="btn ghost small" onClick={() => setEditing(u)}><Pencil size={13} aria-hidden /> Edit</button>
          </div>
        ))}
      </div>
      {editing && (
        <PersonDialog user={editing === 'new' ? null : editing} sources={sources} isSelf={editing !== 'new' && editing.id === auth.user?.id}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />
      )}
    </div>
  )
}

function PersonDialog({ user, sources, isSelf, onClose, onSaved }: {
  user: UserAccount | null; sources: DataSource[]; isSelf: boolean; onClose: () => void; onSaved: () => void
}) {
  const { hosted } = useAuth()
  const [dialog, setDialog] = useState<HTMLDialogElement | null>(null)
  const [name, setName] = useState(user?.name ?? '')
  const [username, setUsername] = useState(user?.username ?? '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>(user?.role ?? 'viewer')
  const [rules, setRules] = useState<AccessRule[]>(user?.rules ?? [])
  const [disabled, setDisabled] = useState(user?.disabled ?? false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // After adding someone or setting a new password: the details to hand over.
  const [done, setDone] = useState<{ name: string; username: string; password: string; reset: boolean } | null>(null)

  useEffect(() => {
    if (dialog && !dialog.open) dialog.showModal()
    return () => dialog?.close()
  }, [dialog])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const cleanRules = role === 'viewer' ? rules.filter((r) => r.table && r.column) : []
      if (user) {
        await api.updateUser(user.id, { name, role, rules: cleanRules, disabled, ...(password ? { password } : {}) })
        if (!password) { onSaved(); return }
        setDone({ name: name || user.username, username: user.username, password, reset: true })
      } else {
        const made = await api.createUser({ username, name, password, role, rules: cleanRules })
        setDone({ name: made.name || made.username, username: made.username, password, reset: false })
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const generate = () => {
    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    const bytes = crypto.getRandomValues(new Uint32Array(12))
    setPassword(Array.from(bytes, (b) => chars[b % chars.length]).join(''))
  }

  const close = done ? onSaved : onClose
  return (
    <dialog ref={setDialog} className="modal person-dialog" aria-labelledby="person-title"
      onCancel={(e) => { e.preventDefault(); if (!busy) close() }}
      onClick={(e) => { if (e.target === e.currentTarget && !busy) close() }}>
      <PortalHost.Provider value={dialog}>
      {done ? <SignInDetails {...done} onDone={onSaved} /> : (
      <form className="modal-body" onSubmit={submit} autoComplete="off">
        <header className="modal-head">
          <span className="icon-tile">{user ? <Pencil size={18} aria-hidden /> : <UserPlus size={18} aria-hidden />}</span>
          <div className="modal-title">
            <h2 id="person-title">{user ? `Edit ${user.name || user.username}` : 'Add a person'}</h2>
            <p className="muted small">{user ? <>Signs in as <strong className="username">{user.username}</strong></>
              : 'They sign in with the username and password you set here.'}</p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close" disabled={busy}><X size={18} /></button>
        </header>
        <div className="modal-content">
          <div className="two-col">
            <label className="field"><span>Name <em>(shown in Stardeck)</em></span>
              <input value={name} autoComplete="off" onChange={(e) => setName(e.target.value)} placeholder="e.g. Ravi Kumar" /></label>
            {!user && (
              <label className="field"><span>{hosted ? 'Email' : 'Username'} <em>(typed to sign in)</em></span>
                <input required type={hosted ? 'email' : 'text'} value={username} autoCapitalize="none" spellCheck={false}
                  autoComplete="off" onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/\s+/g, ''))}
                  placeholder={hosted ? 'ravi@company.com' : 'e.g. ravi'} /></label>
            )}
          </div>
          <label className="field"><span>{user ? 'New password' : 'Password'} {user && <em>(leave empty to keep theirs)</em>}</span>
            <span className="input-affix">
              <input type="text" value={password} required={!user} minLength={user && !password ? undefined : 8} autoComplete="new-password"
                spellCheck={false} onChange={(e) => setPassword(e.target.value)} placeholder={user ? '' : '8+ characters'} />
              <button type="button" className="icon-btn" onClick={generate} title="Make a strong password" aria-label="Make a strong password">
                <Dices size={15} />
              </button>
            </span>
            {password && <small className="muted">You’ll get the sign-in details to send them after saving.</small>}
          </label>

          <fieldset className="form-section">
            <legend>Role</legend>
            <div className="role-options">
              {ROLES.map((r) => (
                <label key={r.value} className={`role-option${role === r.value ? ' on' : ''}`}>
                  <input type="radio" name="role" checked={role === r.value} disabled={isSelf && r.value !== 'admin'}
                    onChange={() => setRole(r.value)} />
                  <span><strong>{r.label}</strong><span className="muted small">{r.text}</span></span>
                </label>
              ))}
            </div>
          </fieldset>

          {role === 'viewer' && (
            <fieldset className="form-section">
              <legend>Data access</legend>
              <p className="muted small">Without rules, this viewer sees every row. With rules, only matching rows, and rows linked to them
                (a rule on customers also limits their orders).</p>
              {rules.map((r, i) => (
                <RuleRow key={i} rule={r} sources={sources}
                  onChange={(next) => setRules(rules.map((x, j) => (j === i ? next : x)))}
                  onRemove={() => setRules(rules.filter((_, j) => j !== i))} />
              ))}
              <button type="button" className="btn ghost small" disabled={!sources.length}
                onClick={() => setRules([...rules, { sourceId: sources[0]?.id ?? '', table: '', column: '', values: [] }])}>
                <Plus size={14} aria-hidden /> Add a rule
              </button>
            </fieldset>
          )}

          {user && !isSelf && (
            <label className="checkbox"><input type="checkbox" checked={disabled} onChange={(e) => setDisabled(e.target.checked)} />
              Can’t sign in (keeps their account; signs them out now)</label>
          )}
          {error && <p className="test-result bad" role="alert"><span>{error}</span></p>}
        </div>
        <footer className="modal-foot">
          {user && !isSelf && (
            <span className="modal-foot-left">
              <ConfirmDelete label={`Remove ${user.name || user.username}?`} detail="They won’t be able to sign in. Dashboards they built stay."
                title="Remove person" confirmText="Remove" onConfirm={async () => {
                  try { await api.deleteUser(user.id); onSaved() } catch (e) { setError((e as Error).message) }
                }} />
            </span>
          )}
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || (!user && (!username || password.length < 8))}>
            {busy ? <LoaderCircle size={15} className="spin" aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
            {user ? 'Save changes' : 'Add person'}
          </button>
        </footer>
      </form>
      )}
      </PortalHost.Provider>
    </dialog>
  )
}

/** What to send someone so they can sign in: the address, their username (not their name) and password. */
function SignInDetails({ name, username, password, reset, onDone }: {
  name: string; username: string; password: string; reset: boolean; onDone: () => void
}) {
  const [copied, setCopied] = useState(false)
  const host = window.location.hostname
  const address = host === 'localhost' || host === '127.0.0.1' ? null : window.location.origin
  const copy = async () => {
    const text = ['Stardeck sign-in', address && `Address: ${address}`, `Username: ${username}`, `Password: ${password}`]
      .filter(Boolean).join('\n')
    if (await copyText(text)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    }
  }
  return (
    <div className="modal-body">
      <header className="modal-head">
        <span className="icon-tile"><KeyRound size={18} aria-hidden /></span>
        <div className="modal-title">
          <h2 id="person-title">{reset ? `New password for ${name}` : `${name} can sign in now`}</h2>
          <p className="muted small">Send them these details privately. The password isn’t shown again.</p>
        </div>
        <button type="button" className="icon-btn" onClick={onDone} aria-label="Close"><X size={18} /></button>
      </header>
      <div className="modal-content">
        <dl className="signin-details">
          {address && <div><dt>Address</dt><dd><code>{address}</code></dd></div>}
          <div><dt>Username</dt><dd><code>{username}</code></dd></div>
          <div><dt>Password</dt><dd><code>{password}</code></dd></div>
        </dl>
        <p className="hint"><span>They sign in with the <strong>username</strong>, not their name.</span></p>
      </div>
      <footer className="modal-foot">
        <button type="button" className="btn ghost" onClick={() => void copy()}>
          {copied ? <Check size={15} aria-hidden /> : <Copy size={15} aria-hidden />} {copied ? 'Copied' : 'Copy details'}
        </button>
        <button type="button" className="btn primary" autoFocus onClick={onDone}>Done</button>
      </footer>
    </div>
  )
}

function RuleRow({ rule, sources, onChange, onRemove }: {
  rule: AccessRule; sources: DataSource[]; onChange: (r: AccessRule) => void; onRemove: () => void
}) {
  const source = sources.find((s) => s.id === rule.sourceId)
  const tables = (source?.tables ?? []).filter((t) => !t.prepared && !t.error)
  const table = tables.find((t) => t.name === rule.table)
  const [values, setValues] = useState<ColumnValues | null>(null)
  const [q, setQ] = useState('')
  const [coverage, setCoverage] = useState<{ restricted: string[]; unrestricted: string[] } | null>(null)

  useEffect(() => {
    if (!rule.sourceId || !rule.table || !rule.column) return
    let live = true
    const t = window.setTimeout(() => {
      api.sourceValues(rule.sourceId, rule.table, rule.column, q).then((v) => { if (live) setValues(v) }).catch(() => {})
    }, q ? 250 : 0)
    return () => { live = false; window.clearTimeout(t) }
  }, [rule.sourceId, rule.table, rule.column, q])

  useEffect(() => {
    if (!rule.sourceId || !rule.table) return
    let live = true
    api.ruleCoverage(rule.sourceId, rule.table).then((c) => { if (live) setCoverage(c) }).catch(() => {})
    return () => { live = false }
  }, [rule.sourceId, rule.table])

  const toggle = (v: Cell) => onChange({ ...rule, values: rule.values.includes(v) ? rule.values.filter((x) => x !== v) : [...rule.values, v] })
  const label = (v: Cell) => values?.labels[String(v)] ?? formatCategory(v)
  return (
    <div className="rule-card">
      <div className="rule-head">
        {sources.length > 1 && (
          <select aria-label="Data source" value={rule.sourceId} onChange={(e) => onChange({ sourceId: e.target.value, table: '', column: '', values: [] })}>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        <select aria-label="Table" value={rule.table} onChange={(e) => onChange({ ...rule, table: e.target.value, column: '', values: [] })}>
          <option value="" disabled>Table…</option>
          {tables.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
        </select>
        <select aria-label="Column" value={rule.column} disabled={!table} onChange={(e) => onChange({ ...rule, column: e.target.value, values: [] })}>
          <option value="" disabled>Column…</option>
          {table?.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <button type="button" className="icon-btn" onClick={onRemove} aria-label="Remove rule" title="Remove rule"><Trash2 size={14} /></button>
      </div>
      {rule.column && (
        <>
          <p className="small rule-sentence">Only rows where <code>{rule.table}.{rule.column}</code> is{' '}
            {rule.values.length ? <strong>{rule.values.map(label).join(', ')}</strong> : <span className="warn-text">nothing yet (no rows)</span>}</p>
          <label className="menu-search">
            <Search size={14} aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search values…" aria-label="Search values" />
          </label>
          <div className="rule-values">
            {values?.values.map((v) => (
              <button type="button" key={String(v)} className={`chip${rule.values.includes(v) ? ' on' : ''}`} onClick={() => toggle(v)}>
                {label(v)}
              </button>
            ))}
            {values?.more && <span className="muted small">Search to see more…</span>}
          </div>
        </>
      )}
      {coverage && rule.table && (
        <p className="muted small rule-coverage">
          Limits {coverage.restricted.join(', ')}.
          {coverage.unrestricted.length > 0 && <> Not linked, so fully visible: {coverage.unrestricted.join(', ')}.{' '}
            <Link to={`/sources/${rule.sourceId}/definitions#relationships`}>Add a link</Link> if that matters.</>}
        </p>
      )}
    </div>
  )
}
