// Popup de autocompletado para comandos "/" (fuente única SLASH_COMMANDS).

import { SLASH_COMMANDS } from './features'

export default function SlashPopup({
  word,
  selected,
  onPick,
}: {
  word: string
  selected: number
  onPick: (cmd: string) => void
}) {
  const q = word.startsWith('/') ? word.slice(1).toLowerCase() : ''
  const items = SLASH_COMMANDS.filter((c) => c.cmd.slice(1).startsWith(q)).slice(0, 8)
  if (items.length === 0) return null
  return (
    <div className="absolute bottom-full left-0 right-0 z-20 mb-1 overflow-hidden rounded-md border border-line bg-panel shadow-sm">
      {items.map((c, i) => (
        <button
          key={c.cmd}
          type="button"
          onMouseDown={(e) => {
            e.preventDefault()
            onPick(c.cmd)
          }}
          className={`flex w-full items-center gap-3 px-3 py-1.5 text-left text-sm ${
            i === selected ? 'bg-accent text-accentink' : 'text-ink hover:bg-canvas'
          }`}
        >
          <span className="oc-mono">{c.cmd}</span>
          <span className={`truncate text-xs ${i === selected ? 'text-accentink/70' : 'text-muted'}`}>
            {c.desc}
          </span>
        </button>
      ))}
    </div>
  )
}