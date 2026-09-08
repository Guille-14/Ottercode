// Diálogo de confirmación único. El borrado real espera ~8 s (UndoToast).

import { useEffect, useRef } from 'react'
import { Button } from './ui'

export default function ConfirmDialog({
  open,
  itemLabel,
  onCancel,
  onConfirm,
}: {
  open: boolean
  itemLabel: string
  onCancel: () => void
  onConfirm: () => void
}) {
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    const first = box.current?.querySelector<HTMLElement>('button')
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
        return
      }
      if (e.key !== 'Tab' || !box.current) return
      const focusable = box.current.querySelectorAll<HTMLElement>('button, [href], input')
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      prev?.focus?.()
    }
  }, [open, onCancel])

  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="oc-confirm-title"
    >
      <div ref={box} className="w-full max-w-sm rounded-xl border border-line bg-panel p-4 shadow-lg">
        <h2 id="oc-confirm-title" className="text-sm font-semibold text-ink">
          Confirmar eliminación
        </h2>
        <p className="mt-2 text-sm text-muted">
          ¿Seguro que quieres eliminar {itemLabel}? Tendrás unos segundos para deshacer.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" type="button" onClick={onCancel}>
            Cancelar
          </Button>
          <Button variant="danger" type="button" onClick={onConfirm}>
            Eliminar
          </Button>
        </div>
      </div>
    </div>
  )
}
