import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  ArrowUp, BarChart3, BookOpen, Check, ChartColumnIncreasing, LoaderCircle, LocateFixed, MessageCircleQuestion, Pencil,
  Plus, RefreshCw, RotateCcw, Sparkles, SquarePen, Trash2, Wand2, WandSparkles, X,
} from 'lucide-react'
import { api, type Analysis, type ChartType, type ChatAgent, type ChatMessage, type ChatProposal, type DatePreset } from '../api'
import { newId } from '../lib/browser'
import { chartLabel } from '../lib/chartFit'
import { DataTable } from './DataTable'
import { WidgetVisual } from './WidgetVisual'

/** Below this the panel covers the dashboard (with a scrim); above it, it floats beside the user's work. */
const MODAL = '(max-width: 1100px)'
/** Phones: the panel fills the screen. */
const PHONE = '(max-width: 600px)'

function useMedia(query: string) {
  const subscribe = useCallback((onChange: () => void) => {
    const m = window.matchMedia(query)
    m.addEventListener('change', onChange)
    return () => m.removeEventListener('change', onChange)
  }, [query])
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches)
}

const smooth = (): ScrollBehavior => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')

type StarterKind = 'ask' | 'add' | 'change' | 'define'
interface Suggestion { text: string; label: string; kind: StarterKind; /** false = put it in the input for the user to finish */ send: boolean }
const STARTER_ICON = { ask: MessageCircleQuestion, add: ChartColumnIncreasing, change: WandSparkles, define: BookOpen }

/** Starter prompts built from this dashboard's own widgets and the source's unclear columns. */
function buildSuggestions(widgets: { title: string; type: string }[], flagged: string | null): Suggestion[] {
  const out: Suggestion[] = []
  const add = (text: string, kind: StarterKind, send = true, label = text) => out.push({ text, label, kind, send })
  const kpi = widgets.find((w) => w.type === 'kpi')
  const chart = widgets.find((w) => w.type !== 'kpi' && w.type !== 'table')
  if (chart) add(`What stands out in "${chart.title}"?`, 'ask')
  if (kpi) add(`Show ${kpi.title.toLowerCase()} by month`, 'add')
  if (chart) add(`Make "${chart.title}" a ${chart.type === 'bar' ? 'donut' : 'bar'} chart`, 'change')
  if (!widgets.length) add('What are the most useful charts for this data?', 'ask')
  if (flagged) add(`${flagged} means `, 'define', false, `Explain what "${flagged}" means`)
  else if (kpi) add(`Compare ${kpi.title.toLowerCase()} with last year`, 'add')
  return out.slice(0, 4)
}

const AGENT: Record<ChatAgent | 'default', { label: string; icon: typeof Sparkles }> = {
  builder: { label: 'Dashboard builder', icon: Wand2 },
  analyst: { label: 'Data analyst', icon: BarChart3 },
  default: { label: 'Stardeck', icon: Sparkles },
}

/** A chat message plus what only the panel needs: failures to retry, and the widget a change touched. */
type Message = ChatMessage & { error?: boolean; retry?: { text: string; agent?: ChatAgent; basisSql?: string }; widgetId?: string }

interface Props {
  dashboardId: string
  sourceId: string
  preset: DatePreset
  /** Called once a change is saved; resolves when the dashboard shows it. `widgetId`: the widget added or changed. */
  onApplied: (widgetId?: string) => Promise<void>
  /** Point out a widget on the dashboard. */
  onShow: (widgetId: string) => void
  onClose: () => void
  /** Slid out of view (not unmounted) when closed, so the conversation survives. */
  open: boolean
  widgets: { id: string; title: string; type: string }[]
  /** Text to place in the input, e.g. from a broken widget's "Ask the assistant". n changes on every hand-off. */
  draft?: { text: string; n: number }
}

export function ChatPanel({ dashboardId, sourceId, preset, onApplied, onShow, onClose, open, widgets, draft }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // Set while the assistant waits for the answer to its clarifying question.
  const [pending, setPending] = useState<string | undefined>()
  const panelRef = useRef<HTMLElement>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const focusedDraft = useRef(0)
  const [flagged, setFlagged] = useState<string | null>(null)
  const [draftSeen, setDraftSeen] = useState(0)
  if (draft && draft.n !== draftSeen) { // adopt a new hand-off during render (no effect round-trip)
    setDraftSeen(draft.n)
    setInput(draft.text)
  }
  const modal = useMedia(MODAL)
  const phone = useMedia(PHONE)

  useEffect(() => {
    api.getSource(sourceId).then((src) => {
      for (const t of src.tables) {
        const c = t.columns.find((x) => x.flag)
        if (c) { setFlagged(c.name); return }
      }
    }).catch(() => {})
  }, [sourceId])

  // Opening moves focus into the panel: the input on desktop (or when text was handed over), the panel
  // itself on touch screens, so the keyboard doesn't cover the starter suggestions.
  useEffect(() => {
    if (!open) return
    const frame = requestAnimationFrame(() => {
      const ta = inputRef.current
      const handOff = !!draft && draft.n !== focusedDraft.current
      if (ta && (handOff || window.matchMedia('(pointer: fine)').matches)) {
        if (draft) focusedDraft.current = draft.n
        ta.focus({ preventScroll: true })
        ta.setSelectionRange(ta.value.length, ta.value.length)
      } else {
        panelRef.current?.focus({ preventScroll: true })
      }
    })
    return () => cancelAnimationFrame(frame)
  }, [open, draft])

  // Escape closes, unless something inside (a menu) already used it.
  useEffect(() => {
    if (!open) return
    const onKey = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // Full screen on phones: the page behind stays put (no scroll bleeding through from the conversation).
  useEffect(() => {
    if (!open || !phone) return
    document.documentElement.classList.add('chat-locked')
    return () => document.documentElement.classList.remove('chat-locked')
  }, [open, phone])

  // Phones: fit the panel to the visible area, so the input stays above the on-screen keyboard.
  useEffect(() => {
    const vv = window.visualViewport
    const el = panelRef.current
    if (!open || !phone || !vv || !el) return
    const sync = () => {
      el.style.top = `${vv.offsetTop}px`
      el.style.height = `${vv.height}px`
    }
    sync()
    vv.addEventListener('resize', sync)
    vv.addEventListener('scroll', sync)
    return () => {
      vv.removeEventListener('resize', sync)
      vv.removeEventListener('scroll', sync)
      el.style.removeProperty('top')
      el.style.removeProperty('height')
    }
  }, [open, phone])

  // The input grows with its text, up to a few lines.
  useLayoutEffect(() => {
    const ta = inputRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 168)}px`
  }, [input])

  // Follow the conversation. A long reply (a proposal with a chart) is shown from its start, not its end.
  const count = messages.length
  const lastRole = messages.at(-1)?.role
  useEffect(() => {
    const log = logRef.current
    if (!log) return
    const last = log.lastElementChild as HTMLElement | null
    const reply = !busy && lastRole === 'assistant'
    log.scrollTo({ top: reply && last ? last.offsetTop - 12 : log.scrollHeight, behavior: smooth() })
  }, [count, lastRole, busy])

  /** `basisSql`: "Add as chart" passes the analyst's query, so the chart shows the same numbers.
   *  `base`: the conversation to continue (a retry drops the failed exchange first). */
  const send = async (text: string, agent?: ChatAgent, basisSql?: string, base: Message[] = messages) => {
    if (!text.trim() || busy) return
    const history = base.filter((m) => !m.error).map((m) => ({ role: m.role, text: m.text }))
    setMessages([...base, { id: newId(), role: 'user', text }])
    setInput('')
    setBusy(true)
    try {
      const reply = await api.chat(dashboardId, text, history, preset, pending, agent, basisSql)
      setPending(reply.pending)
      setMessages((m) => [...m, {
        id: newId(), role: 'assistant', text: reply.text, proposal: reply.proposal,
        proposalState: reply.proposal ? 'pending' : undefined, options: reply.options,
        agent: reply.agent, analysis: reply.analysis,
      }])
    } catch (e) {
      setMessages((m) => [...m, {
        id: newId(), role: 'assistant', error: true, retry: { text, agent, basisSql },
        text: `I couldn't get an answer: ${(e as Error).message}`,
      }])
    } finally {
      setBusy(false)
    }
  }

  /** Ask the failed question again, in place of the failed exchange. */
  const retry = (m: Message) => {
    if (m.retry) void send(m.retry.text, m.retry.agent, m.retry.basisSql, messages.slice(0, Math.max(0, messages.indexOf(m) - 1)))
  }

  const resolve = async (msg: Message, apply: boolean) => {
    const p = msg.proposal
    if (!p) return
    let widgetId: string | undefined
    if (apply) {
      try {
        widgetId = await applyProposal(p, dashboardId, sourceId, widgets.map((w) => w.id))
        await onApplied(widgetId) // "Added" only once the dashboard shows it
      } catch (e) {
        setMessages((m) => [...m, { id: newId(), role: 'assistant', error: true, text: `I couldn't apply that: ${(e as Error).message}` }])
        return
      }
    }
    setMessages((m) => m.map((x) => (x.id === msg.id ? { ...x, proposalState: apply ? 'applied' : 'dismissed', widgetId } : x)))
  }

  const startOver = () => {
    setMessages([])
    setPending(undefined)
    setInput('')
    inputRef.current?.focus()
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  // Covering the dashboard, Tab stays inside the panel.
  const trapFocus = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== 'Tab' || !modal || !panelRef.current) return
    const items = [...panelRef.current.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea, a[href], summary, [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null)
    if (!items.length) return
    const first = items[0], last = items[items.length - 1]
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) {
      e.preventDefault(); last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus()
    }
  }

  const suggestions = buildSuggestions(widgets, flagged)
  const lastIndex = messages.length - 1

  return createPortal(
    <>
      <div className={`chat-scrim${open ? ' open' : ''}`} onClick={onClose} aria-hidden="true" />
      <section ref={panelRef} id="stardeck-assistant" className={`chat${open ? ' open' : ''}`} role="dialog"
        aria-modal={modal || undefined} aria-labelledby="chat-title" tabIndex={-1} inert={!open} onKeyDown={trapFocus}>
        <header className="chat-head">
          <span className="chat-mark" aria-hidden="true"><Sparkles size={17} /></span>
          <div className="chat-titles">
            <h2 id="chat-title">Ask Stardeck</h2>
            <p>Charts and answers from this dashboard’s data</p>
          </div>
          {messages.length > 0 && (
            <button className="icon-btn" onClick={startOver} disabled={busy} title="New conversation" aria-label="Start a new conversation">
              <SquarePen size={17} />
            </button>
          )}
          <button className="icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close assistant"><X size={18} /></button>
        </header>

        <div className="chat-log" ref={logRef} role="log" aria-live="polite" aria-busy={busy}>
          {messages.length === 0 && (
            <div className="chat-empty">
              <span className="chat-empty-mark" aria-hidden="true"><Sparkles size={24} /></span>
              <p className="chat-empty-title">What would you like to know?</p>
              <p className="chat-empty-text">
                Add or change charts, ask questions about your numbers, or tell me what a column means.
                Nothing on the dashboard changes until you approve it.
              </p>
              {suggestions.length > 0 && (
                <div className="starters" role="group" aria-label="Suggestions">
                  {suggestions.map((s) => {
                    const Icon = STARTER_ICON[s.kind]
                    return (
                      <button key={s.text} className="starter" onClick={() => {
                        if (s.send) void send(s.text)
                        else { setInput(s.text); inputRef.current?.focus() }
                      }}>
                        <span className={`starter-icon ${s.kind}`} aria-hidden="true"><Icon size={15} /></span>
                        <span>{s.send ? s.label : `${s.label}…`}</span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {messages.map((m, i) => m.role === 'user' ? (
            <div key={m.id} className="msg user"><div className="msg-bubble">{m.text}</div></div>
          ) : (
            <AssistantMessage key={m.id} m={m} details={m.analysis && (
              <AnalysisDetails analysis={m.analysis} disabled={busy}
                onChart={(req) => void send(`Add a chart: ${req}`, 'builder', m.analysis?.queries.at(-1)?.sql)} />
            )}>
              {m.retry && i === lastIndex && (
                <div className="msg-actions">
                  <button className="btn ghost small" onClick={() => retry(m)} disabled={busy}><RotateCcw size={13} aria-hidden /> Try again</button>
                </div>
              )}
              {m.proposal && (
                <ProposalCard proposal={m.proposal} state={m.proposalState ?? 'pending'} preset={preset} widgets={widgets}
                  widgetId={m.widgetId} onShow={onShow} onApply={() => resolve(m, true)} onDismiss={() => resolve(m, false)} />
              )}
              {m.options && m.options.length > 0 && i === lastIndex && !busy && (
                <div className="msg-options" role="group" aria-label="Suggested replies">
                  {m.options.map((o) => <button key={o} className="chip" onClick={() => send(o)}>{o}</button>)}
                </div>
              )}
            </AssistantMessage>
          ))}
          {busy && <Thinking />}
        </div>

        <form className="chat-input" onSubmit={onSubmit}>
          <div className="composer">
            <textarea ref={inputRef} value={input} rows={1} enterKeyHint="send" aria-label="Message Stardeck"
              placeholder={pending ? 'Type your answer…' : 'Ask a question or describe a chart…'}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input) }
              }} />
            <button type="submit" className="send-btn" disabled={!input.trim() || busy} aria-label="Send" title="Send (Enter)">
              {busy ? <LoaderCircle size={17} className="spin" aria-hidden /> : <ArrowUp size={18} aria-hidden />}
            </button>
          </div>
          <p className="composer-hint" aria-hidden="true"><kbd>Enter</kbd> to send · <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line</p>
        </form>
      </section>
    </>,
    document.body,
  )
}

/** `details` sit inside the answer's bubble (the analyst's working); `children` follow it (proposals, replies). */
function AssistantMessage({ m, details, children }: { m: Message; details?: ReactNode; children?: ReactNode }) {
  const agent = AGENT[m.agent ?? 'default']
  const Icon = agent.icon
  return (
    <div className={`msg assistant${m.agent ? ` by-${m.agent}` : ''}${m.error ? ' is-error' : ''}`}>
      <div className="msg-meta">
        <span className="msg-avatar" aria-hidden="true"><Icon size={13} /></span>
        <span>{agent.label}</span>
      </div>
      <div className="msg-bubble" role={m.error ? 'alert' : undefined}>
        <div className="msg-text">{renderText(m.text)}</div>
        {details}
      </div>
      {children}
    </div>
  )
}

/** Shown while the assistant works; local models can take a while, so it says so after a few seconds. */
function Thinking() {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setSlow(true), 7000)
    return () => window.clearTimeout(t)
  }, [])
  return (
    <div className="msg assistant thinking" role="status">
      <div className="msg-meta">
        <span className="msg-avatar" aria-hidden="true"><Sparkles size={13} /></span>
        <span>Stardeck</span>
      </div>
      <div className="msg-bubble">
        <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
        <span>{slow ? 'Still working. Local models can take up to 20 seconds…' : 'Thinking…'}</span>
      </div>
    </div>
  )
}

/** Saves a proposal. Returns the id of the widget it added or changed, so the dashboard can point it out. */
async function applyProposal(p: ChatProposal, dashboardId: string, sourceId: string, knownIds: string[]): Promise<string | undefined> {
  switch (p.kind) {
    case 'definition':
      await api.saveColumnDefinition(sourceId, p.table, p.column, p.definition, 'chat')
      return undefined
    case 'add_widget': {
      const def = await api.addWidget(dashboardId, p.widget)
      const known = new Set(knownIds)
      return [...def.widgets].reverse().find((w) => !known.has(w.id))?.id // new widgets are appended
    }
    case 'replace_widget':
      await api.updateWidget(dashboardId, p.widgetId, {
        title: p.widget.title, summary: p.widget.summary, sql: p.widget.sql, spec: p.widget.spec,
        ...(p.widget.dateFilter ? { dateFilter: p.widget.dateFilter } : { clearDateFilter: true }),
      })
      return p.widgetId
    case 'change_type':
      await api.updateWidget(dashboardId, p.widgetId, { chartType: p.type, ...(p.stack ? { stack: p.stack } : {}) })
      return p.widgetId
    case 'rename_widget':
      await api.updateWidget(dashboardId, p.widgetId, { title: p.title })
      return p.widgetId
    case 'remove_widget':
      await api.removeWidget(dashboardId, p.widgetId)
      return undefined
  }
}

const KIND: Record<ChatProposal['kind'], { label: string; apply: string; working: string; done: string; icon: typeof Plus }> = {
  definition: { label: 'Column meaning', apply: 'Save definition', working: 'Saving…', done: 'Definition saved', icon: BookOpen },
  add_widget: { label: 'New chart', apply: 'Add to dashboard', working: 'Adding…', done: 'Added to the dashboard', icon: Plus },
  replace_widget: { label: 'Updated chart', apply: 'Replace widget', working: 'Replacing…', done: 'Widget replaced', icon: RefreshCw },
  change_type: { label: 'Change chart type', apply: 'Apply', working: 'Applying…', done: 'Chart type changed', icon: WandSparkles },
  rename_widget: { label: 'Rename', apply: 'Rename', working: 'Renaming…', done: 'Renamed', icon: Pencil },
  remove_widget: { label: 'Remove widget', apply: 'Remove', working: 'Removing…', done: 'Removed from the dashboard', icon: Trash2 },
}

/** Taller previews for charts that need the room (legends under round charts, flows, maps). */
const TALL = new Set(['pie', 'donut', 'treemap', 'funnel', 'radar', 'sankey', 'heatmap', 'map', 'scatter', 'bubble', 'boxplot'])

function ProposalCard({ proposal: p, state, preset, widgets, widgetId, onShow, onApply, onDismiss }: {
  proposal: ChatProposal; state: 'pending' | 'applied' | 'dismissed'; preset: DatePreset
  widgets: { id: string; title: string; type: string }[]; widgetId?: string
  onShow: (widgetId: string) => void; onApply: () => Promise<void>; onDismiss: () => void
}) {
  const [working, setWorking] = useState(false)
  const kind = KIND[p.kind]
  const Icon = kind.icon
  const widget = p.kind === 'add_widget' || p.kind === 'replace_widget' ? p.widget : null
  const title = p.kind === 'add_widget' || p.kind === 'replace_widget' ? p.widget.title
    : p.kind === 'definition' ? `${p.table}.${p.column}` : p.widgetTitle
  const current = 'widgetId' in p ? widgets.find((w) => w.id === p.widgetId) : undefined
  const previewClass = !widget ? '' : widget.spec.type === 'kpi' ? 'kpi-preview'
    : widget.spec.type === 'table' || widget.spec.type === 'matrix' ? 'table-preview'
    : TALL.has(widget.spec.type) ? 'tall' : ''

  return (
    <div className={`proposal ${state}${p.kind === 'remove_widget' ? ' danger' : ''}`}>
      <div className="proposal-head">
        <span className="proposal-icon" aria-hidden="true"><Icon size={15} /></span>
        <div className="proposal-titles">
          <span className="proposal-kind">{kind.label}</span>
          <span className="proposal-title">{title}</span>
        </div>
      </div>

      {state !== 'dismissed' && (
        <>
          {p.kind === 'definition' && (
            <dl className="proposal-def">
              {p.definition.description && <><dt>Means</dt><dd>{p.definition.description}</dd></>}
              {p.definition.unit && <><dt>Unit</dt><dd>{p.definition.unit}</dd></>}
              {p.definition.values && <><dt>Values</dt><dd>
                {Object.entries(p.definition.values).map(([k, v]) => <span key={k} className="code-pill">{k} = {v}</span>)}
              </dd></>}
            </dl>
          )}
          {widget && (
            <>
              {p.kind === 'replace_widget' && current && <p className="proposal-note">Replaces “{current.title}”</p>}
              {widget.result && (
                <div className={`proposal-chart ${previewClass}`}>
                  <WidgetVisual spec={widget.spec} result={widget.result} preset={preset} dateFiltered={!!widget.dateFilter} />
                </div>
              )}
              {widget.summary && <p className="proposal-summary">{widget.summary}</p>}
              <details className="proposal-sql">
                <summary>View SQL</summary>
                <pre>{widget.sql}</pre>
              </details>
            </>
          )}
          {p.kind === 'change_type' && (
            <p className="proposal-change">
              {current && <><span>{chartLabel(current.type as ChartType)}</span><span aria-label="to">→</span></>}
              <strong>{chartLabel(p.type, p.stack)}</strong>
            </p>
          )}
          {p.kind === 'rename_widget' && (
            <p className="proposal-change"><s>{p.widgetTitle}</s><span aria-label="to">→</span><strong>{p.title}</strong></p>
          )}
          {p.kind === 'remove_widget' && <p className="proposal-note">This removes the widget from the dashboard.</p>}
        </>
      )}

      {state === 'pending' ? (
        <div className="proposal-actions">
          <button className={`btn ${p.kind === 'remove_widget' ? 'danger' : 'primary'}`} disabled={working}
            onClick={async () => { setWorking(true); await onApply(); setWorking(false) }}>
            {working ? <LoaderCircle size={15} className="spin" aria-hidden /> : <Check size={15} aria-hidden />}
            {working ? kind.working : kind.apply}
          </button>
          <button className="btn ghost" onClick={onDismiss} disabled={working}>Dismiss</button>
        </div>
      ) : (
        <div className="proposal-state" role="status">
          {state === 'applied' ? <><Check size={15} aria-hidden /> {kind.done}</> : 'Dismissed'}
          {state === 'applied' && widgetId && (
            <button className="btn text small" onClick={() => onShow(widgetId)}>
              <LocateFixed size={14} aria-hidden /> Show on dashboard
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** Tiny markdown: paragraphs, **bold**, *italic* and "- " bullet lists (the analyst answers in bullets).
 *  Text is never injected as HTML. */
function renderText(text: string) {
  const inline = (line: string) => line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, j) =>
    part.startsWith('**') ? <strong key={j}>{part.slice(2, -2)}</strong>
      : part.startsWith('*') && part.length > 2 ? <em key={j}>{part.slice(1, -1)}</em> : part)
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  const flush = () => {
    if (bullets.length) blocks.push(<ul key={`ul${blocks.length}`}>{bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}</ul>)
    bullets = []
  }
  for (const line of text.split('\n')) {
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line)
    if (bullet) { bullets.push(bullet[1]); continue }
    flush()
    if (line.trim()) blocks.push(<p key={`p${blocks.length}`}>{inline(line)}</p>)
  }
  flush()
  return blocks
}

/** The analyst's working: the numbers behind the answer, the queries it ran, and "Add as chart". */
function AnalysisDetails({ analysis, disabled, onChart }: {
  analysis: Analysis; disabled: boolean; onChart: (request: string) => void
}) {
  const { evidence, queries, chartRequest } = analysis
  return (
    <div className="analysis">
      {evidence && evidence.rows.length > 0 && (
        <details>
          <summary>Show the numbers <span className="count">{evidence.rows.length}{evidence.truncated ? '+' : ''} row{evidence.rows.length === 1 ? '' : 's'}</span></summary>
          <div className="analysis-table"><DataTable result={evidence} spec={{ type: 'table' }} /></div>
        </details>
      )}
      {queries.length > 0 && (
        <details>
          <summary>How I worked it out <span className="count">{queries.length} {queries.length === 1 ? 'query' : 'queries'}</span></summary>
          {queries.map((q, i) => <pre key={i} className="analysis-sql">{q.sql}</pre>)}
        </details>
      )}
      {chartRequest && (
        <button className="btn ghost small" disabled={disabled} onClick={() => onChart(chartRequest)}>
          <ChartColumnIncreasing size={14} aria-hidden /> Add as chart
        </button>
      )}
    </div>
  )
}
