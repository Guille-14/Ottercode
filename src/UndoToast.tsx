// Aviso «Eliminado — Deshacer» (A3). Temporizador 8 s.

export default function UndoToast({
  label,
  onUndo,
}: {
  label: string
  onUndo: () => void
}) {
  if (!label) return null
  return (
    <div
      className="fixed bottom-4 left-1/2 z-[55] flex -translate-x-1/2 items-center gap-3 rounded-xl border border-line bg-panel px-4 py-2 text-sm text-ink shadow-lg"
      role="status"
    >
      <span>Eliminado — {label}</span>
      <button
        type="button"
        className="rounded-md bg-accent px-2 py-1 text-xs font-semibold text-accentink"
        onClick={onUndo}
        aria-label="Deshacer"
      >
        Deshacer
      </button>
    </div>
  )
}
