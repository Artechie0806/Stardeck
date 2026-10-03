import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'

/** Two-step inline delete; avoids a browser confirm() dialog. */
export function ConfirmDelete({ label, title, onConfirm }: { label: string; title: string; onConfirm: () => void }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return armed ? (
    <button className="btn danger small" onClick={(e) => { e.preventDefault(); onConfirm() }}>{label}</button>
  ) : (
    <button className="icon-btn" title={title} aria-label={title} onClick={(e) => { e.preventDefault(); setArmed(true) }}><Trash2 size={15} /></button>
  )
}
