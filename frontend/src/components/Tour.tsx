import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'

export interface TourStep {
  /** What to point at (looked up when the step shows; null = no spotlight). */
  target: () => Element | null
  title: string
  body: string
}

const PAD = 8
const GAP = 12
const smooth = (): ScrollBehavior => (window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth')

/** A few steps pointing at parts of the page. The page stays usable underneath (people are asked to
 *  click a chart), so this isn't a modal; Esc or "Skip" ends it. On phones the card is a bottom sheet. */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [i, setI] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [cardH, setCardH] = useState(0)
  const card = useRef<HTMLDivElement>(null)
  const step = steps[i]
  const last = i === steps.length - 1

  // Bring the target into view, then keep the spotlight on it while charts load and the page moves.
  useLayoutEffect(() => {
    step?.target()?.scrollIntoView({ block: 'center', behavior: smooth() })
    const measure = () => setRect(step?.target()?.getBoundingClientRect() ?? null)
    measure()
    const timer = window.setInterval(measure, 400)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [step])

  useLayoutEffect(() => { setCardH(card.current?.offsetHeight ?? 0) }, [i, rect])
  useEffect(() => { card.current?.querySelector<HTMLButtonElement>('.tour-next')?.focus() }, [i])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' && !last) setI((n) => n + 1)
      else if (e.key === 'ArrowLeft' && i > 0) setI((n) => n - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [i, last, onClose])

  if (!step) return null
  const phone = window.innerWidth <= 600
  const width = Math.min(340, window.innerWidth - 32)
  // Below the spotlight if it fits, else above it; never off screen.
  let position: CSSProperties | undefined
  if (!phone) {
    const below = rect ? rect.bottom + PAD + GAP : window.innerHeight / 2 - cardH / 2
    const top = rect && below + cardH > window.innerHeight - 16 ? rect.top - PAD - GAP - cardH : below
    const left = rect ? rect.left + rect.width / 2 - width / 2 : window.innerWidth / 2 - width / 2
    position = {
      top: Math.max(16, Math.min(top, window.innerHeight - cardH - 16)),
      left: Math.max(16, Math.min(left, window.innerWidth - width - 16)),
      width,
    }
  }

  return createPortal(
    <div className="tour">
      <div className={`tour-spot${rect ? '' : ' none'}`} aria-hidden="true" style={rect ? {
        top: rect.top - PAD, left: rect.left - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2,
      } : undefined} />
      <div ref={card} className={`tour-card${phone ? ' sheet' : ''}`} style={position} role="dialog" aria-modal="false"
        aria-labelledby="tour-title" aria-describedby="tour-body">
        <p className="tour-count">{i + 1} of {steps.length}</p>
        <h2 id="tour-title">{step.title}</h2>
        <p id="tour-body">{step.body}</p>
        <div className="tour-actions">
          <button className="btn text small" onClick={onClose}>Skip tour</button>
          {i > 0 && <button className="btn ghost small" onClick={() => setI(i - 1)}>Back</button>}
          <button className="btn primary small tour-next" onClick={() => (last ? onClose() : setI(i + 1))}>
            {last ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
