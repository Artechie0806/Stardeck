import { useEffect, useRef, useState, type FormEvent } from 'react'
import { BookOpen, Check, LoaderCircle, Pencil, Plus, RefreshCw, Send, Sparkles, Trash2, X } from 'lucide-react'
import { api, type ChatMessage, type ChatProposal, type DatePreset } from '../api'
import { newId } from '../lib/browser'
import { chartLabel } from '../lib/chartFit'
import { WidgetVisual } from './WidgetVisual'

const SUGGESTIONS = [
  'Orders by status',
  'Top 5 customers by revenue',
  'Make revenue by city a donut chart',
  'sts means order status: 1 = Completed, 2 = Cancelled, 9 = Refunded',
]

interface Props {
  dashboardId: string
  sourceId: string
  preset: DatePreset
  onApplied: () => void
  onClose: () => void
}

export function ChatPanel({ dashboardId, sourceId, preset, onApplied, onClose }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([{
    id: 'welcome', role: 'assistant',
    text: 'Ask me to add or change charts, or tell me what a column means. I always show you the change before saving it.',
  }])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // Set while the assistant waits for the answer to its clarifying question.
  const [pending, setPending] = useState<string | undefined>()
  const logRef = useRef<HTMLDivElement>(null)

  // Scroll only the log itself; scrollIntoView would also scroll the page.
  useEffect(() => {
    const log = logRef.current
    if (log) log.scrollTo({ top: log.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const send = async (text: string) => {
    if (!text.trim() || busy) return
    const history = messages.filter((m) => m.id !== 'welcome').map((m) => ({ role: m.role, text: m.text }))
    setMessages((m) => [...m, { id: newId(), role: 'user', text }])
    setInput('')
    setBusy(true)
    try {
      const reply = await api.chat(dashboardId, text, history, preset, pending)
      setPending(reply.pending)
      setMessages((m) => [...m, {
        id: newId(), role: 'assistant', text: reply.text, proposal: reply.proposal,
        proposalState: reply.proposal ? 'pending' : undefined, options: reply.options,
      }])
    } catch (e) {
      setMessages((m) => [...m, { id: newId(), role: 'assistant', text: `Something went wrong: ${(e as Error).message}` }])
    } finally {
      setBusy(false)
    }
  }

  const resolve = async (msg: ChatMessage, apply: boolean) => {
    const p = msg.proposal
    if (!p) return
    if (apply) {
      try {
        await applyProposal(p, dashboardId, sourceId)
        onApplied()
      } catch (e) {
        setMessages((m) => [...m, { id: newId(), role: 'assistant', text: `I couldn't apply that: ${(e as Error).message}` }])
        return
      }
    }
    setMessages((m) => m.map((x) => (x.id === msg.id ? { ...x, proposalState: apply ? 'applied' : 'dismissed' } : x)))
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  return (
    <aside className="chat" aria-label="Dashboard assistant">
      <header className="chat-head">
        <span><Sparkles size={16} aria-hidden /> Assistant</span>
        <button className="icon-btn" onClick={onClose} title="Close assistant"><X size={16} /></button>
      </header>

      <div className="chat-log" ref={logRef}>
        {messages.map((m, i) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="msg-text">{renderText(m.text)}</div>
            {m.options && m.options.length > 0 && i === messages.length - 1 && !busy && (
              <div className="msg-options">
                {m.options.map((o) => <button key={o} className="chip" onClick={() => send(o)}>{o}</button>)}
              </div>
            )}
            {m.proposal && (
              <ProposalCard proposal={m.proposal} state={m.proposalState ?? 'pending'} preset={preset}
                onApply={() => resolve(m, true)} onDismiss={() => resolve(m, false)} />
            )}
          </div>
        ))}
        {busy && (
          <div className="msg assistant thinking">
            <LoaderCircle size={16} className="spin" aria-hidden /> Working on it… building a chart can take a few seconds.
          </div>
        )}
      </div>

      {messages.length === 1 && (
        <div className="chat-suggestions">
          {SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}

      <form className="chat-input" onSubmit={onSubmit}>
        <textarea value={input} rows={2} placeholder="Add a chart, change one, or explain a column…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(input) } }} />
        <button type="submit" className="icon-btn primary" disabled={!input.trim() || busy} title="Send"><Send size={16} /></button>
      </form>
    </aside>
  )
}

async function applyProposal(p: ChatProposal, dashboardId: string, sourceId: string) {
  switch (p.kind) {
    case 'definition':
      return api.saveColumnDefinition(sourceId, p.table, p.column, p.definition, 'chat')
    case 'add_widget':
      return api.addWidget(dashboardId, p.widget)
    case 'replace_widget':
      return api.updateWidget(dashboardId, p.widgetId, {
        title: p.widget.title, summary: p.widget.summary, sql: p.widget.sql, spec: p.widget.spec,
        ...(p.widget.dateFilter ? { dateFilter: p.widget.dateFilter } : { clearDateFilter: true }),
      })
    case 'change_type':
      return api.updateWidget(dashboardId, p.widgetId, { chartType: p.type, ...(p.stack ? { stack: p.stack } : {}) })
    case 'rename_widget':
      return api.updateWidget(dashboardId, p.widgetId, { title: p.title })
    case 'remove_widget':
      return api.removeWidget(dashboardId, p.widgetId)
  }
}

const APPLY_LABEL: Record<ChatProposal['kind'], string> = {
  definition: 'Save definition', add_widget: 'Add to dashboard', replace_widget: 'Replace widget',
  change_type: 'Apply', rename_widget: 'Rename', remove_widget: 'Remove',
}

function ProposalCard({ proposal: p, state, preset, onApply, onDismiss }: {
  proposal: ChatProposal; state: 'pending' | 'applied' | 'dismissed'; preset: DatePreset
  onApply: () => void; onDismiss: () => void
}) {
  const [working, setWorking] = useState(false)
  const widget = p.kind === 'add_widget' || p.kind === 'replace_widget' ? p.widget : null
  return (
    <div className={`proposal ${state}`}>
      {p.kind === 'definition' && (
        <>
          <div className="proposal-title"><BookOpen size={14} aria-hidden /> {p.table}.{p.column}</div>
          <dl className="proposal-def">
            {p.definition.description && <><dt>Means</dt><dd>{p.definition.description}</dd></>}
            {p.definition.unit && <><dt>Unit</dt><dd>{p.definition.unit}</dd></>}
            {p.definition.values && <><dt>Values</dt><dd>
              {Object.entries(p.definition.values).map(([k, v]) => <span key={k} className="code-pill">{k} = {v}</span>)}
            </dd></>}
          </dl>
        </>
      )}
      {widget && (
        <>
          <div className="proposal-title">
            {p.kind === 'replace_widget' ? <RefreshCw size={14} aria-hidden /> : <Plus size={14} aria-hidden />} {widget.title}
          </div>
          {widget.result && (
            <div className={`proposal-chart ${widget.spec.type === 'kpi' ? 'kpi-preview' : ''}`}>
              <WidgetVisual spec={widget.spec} result={widget.result} preset={preset} dateFiltered={!!widget.dateFilter} />
            </div>
          )}
          <p className="proposal-summary">{widget.summary}</p>
          <details className="proposal-sql">
            <summary>View SQL</summary>
            <pre>{widget.sql}</pre>
          </details>
        </>
      )}
      {p.kind === 'change_type' && <div className="proposal-title">{p.widgetTitle} → {chartLabel(p.type, p.stack)}</div>}
      {p.kind === 'rename_widget' && <div className="proposal-title"><Pencil size={14} aria-hidden /> {p.widgetTitle} → {p.title}</div>}
      {p.kind === 'remove_widget' && <div className="proposal-title"><Trash2 size={14} aria-hidden /> {p.widgetTitle}</div>}
      {state === 'pending' ? (
        <div className="proposal-actions">
          <button className={`btn small ${p.kind === 'remove_widget' ? 'danger' : 'primary'}`} disabled={working}
            onClick={async () => { setWorking(true); await onApply(); setWorking(false) }}>
            {working && <LoaderCircle size={13} className="spin" aria-hidden />} {APPLY_LABEL[p.kind]}
          </button>
          <button className="btn ghost small" onClick={onDismiss} disabled={working}>Dismiss</button>
        </div>
      ) : (
        <div className="proposal-state">
          {state === 'applied' ? <><Check size={14} aria-hidden /> Done</> : 'Dismissed'}
        </div>
      )}
    </div>
  )
}

/** Minimal **bold**, *italic* and line-break rendering; text is never injected as HTML. */
function renderText(text: string) {
  return text.split('\n').filter((line, i, all) => line || (i > 0 && all[i - 1])).map((line, i) => (
    <p key={i}>
      {line.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, j) =>
        part.startsWith('**') ? <strong key={j}>{part.slice(2, -2)}</strong>
          : part.startsWith('*') && part.length > 2 ? <em key={j}>{part.slice(1, -1)}</em> : part)}
    </p>
  ))
}
