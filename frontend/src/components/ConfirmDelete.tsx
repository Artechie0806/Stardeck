import { useRef, useState } from 'react'
import { Trash2, TriangleAlert } from 'lucide-react'
import { Popover } from './Popover'

/**
 * Trash icon that opens a small confirmation anchored to it (no browser confirm() dialog, no layout shift).
 * Cancel has focus, so Enter or Escape never deletes by accident.
 */
export function ConfirmDelete({ label, detail, title, confirmText = 'Delete', onConfirm }: {
  label: string
  detail?: string
  title: string
  confirmText?: string
  onConfirm: () => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLButtonElement>(null)
  return (
    <>
      <button ref={ref} type="button" className={`icon-btn ${open ? 'active danger-active' : ''}`} title={title} aria-label={title}
        aria-haspopup="dialog" aria-expanded={open} onClick={(e) => { e.preventDefault(); setOpen((o) => !o) }}>
        <Trash2 size={15} />
      </button>
      {open && (
        <Popover anchor={ref} onClose={() => setOpen(false)} className="confirm-pop" role="alertdialog" label={label}>
          <div className="confirm-body">
            <span className="confirm-icon"><TriangleAlert size={16} aria-hidden /></span>
            <div>
              <p className="confirm-title">{label}</p>
              {detail && <p className="confirm-detail">{detail}</p>}
            </div>
          </div>
          <div className="confirm-actions">
            <button type="button" className="btn ghost small" autoFocus onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className="btn danger small" onClick={() => { setOpen(false); onConfirm() }}>
              <Trash2 size={13} aria-hidden /> {confirmText}
            </button>
          </div>
        </Popover>
      )}
    </>
  )
}
