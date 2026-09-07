// Paleta de comandos (Ctrl+K): comandos slash, vistas y sesiones recientes.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useUi } from './store'
import { api } from './api'
import { SLASH_COMMANDS } from './features'

type Item = { kind: 'cmd' | 'view' | 'ses'; label: string; hint: string }

export default function CommandPalette({
  open,
  onClose,
  nav,
}: {
  open: boolean
  onClose: () => void
  nav: { key: string; label: string; icon: string }[]
}) {
  const setView = useUi((s) => s.setView)
  const setComposerDraft = useUi((s) => s.setComposerDraft)
  const [q, setQ] = useState('')
  const [sessions, setSessions] = useState<{ id: string; task: string }[]>([])
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQ('')
      setSel(0)
      setTimeout(() => inputRef.current?.focus(), 0)
      api.history('', 8).then((r) =>
        setSessions(
          r.sessions.map((s) => ({
            id: String(s.id ?? ''),
            task: String(s.task ?? ''),
          })),
        ),
      )
    }
  }, [open])

  const items = useMemo<Item[]>(() => {
    const ql = q.toLowerCase()
    const out: Item[] = []
    if (ql === '') {
      for (const c of SLASH_COMMANDS.slice(0, 5))
        out.push({ kind: 'cmd', label: c.cmd, hint: c.desc })
      for (const v of nav) out.push({ kind: 'view', label: v.label, hint: 'vista' })
    } else {
      for (const c of SLASH_COMMANDS)
        if (c.cmd.includes(ql)) out.push({ kind: 'cmd', label: c.cmd, hint: c.desc })
      for (const v of nav)
        if (v.label.toLowerCase().includes(ql) || v.key.includes(ql))
          out.push({ kind: 'view', label: v.label, hint: 'vista' })
      for (const s of sessions)
        if (s.task.toLowerCase().includes(ql) || s.id.includes(ql))
          out.push({ kind: 'ses', label: s.task.slice(0, 48), hint: `${s.id} · sesión` })
    }
    return out.slice(0, 10)
  }, [q, sessions, nav])

  if (!open) return null

  const run = (it: Item) => {
    if (it.kind === 'cmd') {
      setComposerDraft(it.label)
      setView('misiones')
    } else if (it.kind === 'view') {
      const n = nav.find((v) => v.label === it.label)
      if (n) setView(n.key)
    }
    onClose()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSel((s) => (s + 1) % items.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSel((s) => (s - 1 + items.length) % items.length)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (items[sel]) run(items[sel])
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 pt-24" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-lg border border-line bg-panel p-2 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="w-full rounded-md bg-canvas px-3 py-2 text-sm focus:outline-none"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setSel(0)
          }}
          onKeyDown={onKey}
          placeholder="Escribe o pulsa ↑↓ / Enter… (Esc cierra)"
        />
        <ul className="mt-2 space-y-0.5">
          {items.map((it, i) => (
            <button
              key={`${it.kind}-${it.label}`}
              type="button"
              onMouseEnter={() => setSel(i)}
              onClick={() => run(it)}
              className={`flex w-full items-center gap-3 rounded-md px-3 py-1.5 text-left text-sm ${
                i === sel ? 'bg-accent text-accentink' : 'text-ink hover:bg-canvas'
              }`}
            >
              <span className={`w-8 shrink-0 oc-mono text-xs ${i === sel ? 'text-accentink/70' : 'text-muted'}`}>
                {it.kind === 'cmd' ? '⌘' : it.kind === 'view' ? '›' : '≡'}
              </span>
              <span className="truncate">{it.label}</span>
              <span className={`ml-auto truncate text-xs ${i === sel ? 'text-accentink/60' : 'text-muted'}`}>
                {it.hint}
              </span>
            </button>
          ))}
          {items.length === 0 && <li className="px-3 py-2 text-sm text-muted">sin resultados</li>}
        </ul>
      </div>
    </div>
  )
}