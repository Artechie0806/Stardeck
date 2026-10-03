import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { usePortalHost } from '../lib/portalHost'

const GAP = 6
const EDGE = 8

/** Space the phone tab bar takes at the bottom of the screen (0 when it isn't shown). */
function bottomLimit() {
  const bar = document.querySelector<HTMLElement>('.tabbar')
  if (bar && getComputedStyle(bar).display !== 'none') return bar.getBoundingClientRect().top - EDGE
  return window.innerHeight - EDGE
}

/**
 * A dropdown menu rendered on <body> (or the open modal dialog it's in), so cards with overflow:hidden, scroll boxes
 * and glass layers can't clip it.
 * It opens below the anchor (or above, when there's more room there), stays inside the viewport and above the
 * phone tab bar, closes on outside click / Escape, and supports arrow-key navigation between items.
 */
export function Popover({ anchor, onClose, children, align = 'end', className = '', label, role = 'menu' }: {
  anchor: RefObject<HTMLElement | null>
  onClose: () => void
  children: ReactNode
  align?: 'start' | 'end'
  className?: string
  label?: string
  role?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const host = usePortalHost()
  // Inside a modal dialog, open in the browser's top layer: above the dialog and positioned against the viewport
  // (the glass dialog would otherwise offset and clip it), while still inside the dialog so it can be clicked.
  const layer = host !== document.body
  const [pos, setPos] = useState<{ top: number; left: number; maxHeight: number } | null>(null)
  const closeRef = useRef(onClose)
  useLayoutEffect(() => { closeRef.current = onClose })

  useLayoutEffect(() => {
    if (layer && ref.current && !ref.current.matches(':popover-open')) ref.current.showPopover()
    const place = () => {
      const a = anchor.current?.getBoundingClientRect()
      const m = ref.current
      if (!a || !m) return
      const width = m.offsetWidth
      const natural = m.scrollHeight
      // In a dialog, stay inside the dialog's box (on phones it also covers the tab bar).
      const box = layer ? host.getBoundingClientRect() : null
      const limit = box ? Math.min(window.innerHeight, box.bottom) - EDGE : bottomLimit()
      const ceiling = box ? Math.max(0, box.top) + EDGE : EDGE
      const minLeft = box ? Math.max(0, box.left) + EDGE : EDGE
      const maxRight = (box ? Math.min(window.innerWidth, box.right) : window.innerWidth) - EDGE
      const below = limit - a.bottom - GAP
      const above = a.top - GAP - ceiling
      const down = below >= Math.min(natural, 260) || below >= above
      const maxHeight = Math.max(120, down ? below : above)
      const height = Math.min(natural, maxHeight)
      const top = down ? a.bottom + GAP : a.top - GAP - height
      let left = align === 'end' ? a.right - width : a.left
      left = Math.min(Math.max(minLeft, left), maxRight - width)
      setPos({ top, left, maxHeight })
    }
    const onDown = (e: Event) => {
      const t = e.target as Node
      if (!ref.current?.contains(t) && !anchor.current?.contains(t)) closeRef.current()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); closeRef.current(); anchor.current?.focus() } // handled: the assistant stays open
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const items = [...(ref.current?.querySelectorAll<HTMLElement>('.menu-item:not(:disabled)') ?? [])]
      if (!items.length) return
      e.preventDefault()
      const i = items.indexOf(document.activeElement as HTMLElement)
      const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length
      items[i === -1 ? 0 : next].focus()
    }
    const onScroll = (e: Event) => { if (!ref.current?.contains(e.target as Node)) place() }
    place()
    const watch = new ResizeObserver(place) // contents can change while open (e.g. values loading)
    if (ref.current) watch.observe(ref.current)
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', onScroll, true)
    return () => {
      watch.disconnect()
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', onScroll, true)
    }
  }, [anchor, align, layer, host])

  return createPortal(
    <div ref={ref} popover={layer ? 'manual' : undefined} className={`menu floating ${className}`} role={role} aria-label={label}
      style={pos ? { top: pos.top, left: pos.left, maxHeight: pos.maxHeight } : { top: -9999, left: -9999 }}>
      {children}
    </div>,
    host,
  )
}
