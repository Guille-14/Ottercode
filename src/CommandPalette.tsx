// Paleta de comandos (Ctrl+K): comandos slash, vistas y acciones.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useUi } from './store'
import { api } from './api'
import { SLASH_COMMANDS } from './features'

type Item = { kind: 'cmd' | 'view' | 'ses' | 'act'; label: string; hint: string; run?: () => void }

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
  const [skillCmds, setSkillCmds] = useState<{ cmd: string; desc: string }[]>([])
  const [sel, setSel] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLDivElement>(null)

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
      api.skills().then((r) =>
        setSkillCmds(
          (r.skills || [])
            .filter((s) => s.kind === 'markdown' || s.cat === 'Markdown')
            .map((s) => ({ cmd: `/${s.name}`, desc: s.desc || 'skill' })),
        ),
      )
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const items = useMemo<Item[]>(() => {
    const ql = q.toLowerCase()
    const out: Item[] = []
    const cmds = [...SLASH_COMMANDS, ...skillCmds]
    const st = useUi.getState()
    const goAjustes = (sec: string) => {
      st.setSettingsSection(sec)
      st.setView('ajustes')
    }
    const acts: Item[] = [
      { kind: 'act', label: 'Nueva misión', hint: 'acción', run: () => { st.clearMission(); st.setView('misiones') } },
      {
        kind: 'act',
        label: 'Compactar contexto',
        hint: 'acción',
        run: () => {
          const id = st.taskId
          if (!id) {
            st.setNotice('No hay misión activa para compactar')
            return
          }
          void api.compactNow(id).then((r) => {
            st.setNotice(r.ok ? (r.still_over ? 'Compactado, el contexto sigue alto' : 'Contexto compactado') : 'No se pudo compactar')
          }).catch((e: unknown) => st.setNotice((e as Error).message))
        },
      },
      { kind: 'act', label: 'Abortar generación', hint: 'acción', run: () => st.stopMission(true) },
      {
        kind: 'act',
        label: 'Deshacer cambios de la misión (git)',
        hint: 'acción',
        run: () => {
          const id = st.taskId
          if (!id) {
            st.setNotice('No hay misión para deshacer')
            return
          }
          void api.missionUndo(id).then(() => st.setNotice('Cambios de la misión deshechos')).catch((e: unknown) => st.setNotice((e as Error).message))
        },
      },
      { kind: 'act', label: 'Ajustes · Parámetros', hint: 'ajustes', run: () => goAjustes('parametros') },
      { kind: 'act', label: 'Ajustes · Modelos', hint: 'ajustes', run: () => goAjustes('modelos') },
      { kind: 'act', label: 'Ajustes · Skills', hint: 'ajustes', run: () => goAjustes('skills') },
      { kind: 'act', label: 'Ajustes · Memoria', hint: 'ajustes', run: () => goAjustes('memoria') },
      { kind: 'act', label: 'Ajustes · Permisos', hint: 'ajustes', run: () => goAjustes('permisos') },
      { kind: 'act', label: 'Ajustes · MCP', hint: 'ajustes', run: () => goAjustes('mcp') },
      { kind: 'act', label: 'Ajustes · Telemetría', hint: 'ajustes', run: () => goAjustes('telemetria') },
      { kind: 'act', label: 'Cambiar de bot (Agentes)', hint: 'acción', run: () => st.setView('identidad') },
    ]
    const match = (label: string, hint: string) => !ql || label.toLowerCase().includes(ql) || hint.toLowerCase().includes(ql)
    if (ql === '') {
      for (const c of cmds.slice(0, 6))
        out.push({ kind: 'cmd', label: c.cmd, hint: c.desc })
      for (const a of acts.slice(0, 8)) out.push(a)
      for (const v of nav) out.push({ kind: 'view', label: v.label, hint: 'vista' })
    } else {
      for (const c of cmds)
        if (c.cmd.includes(ql) || c.desc.toLowerCase().includes(ql))
          out.push({ kind: 'cmd', label: c.cmd, hint: c.desc })
      for (const a of acts)
        if (match(a.label, a.hint)) out.push(a)
      for (const v of nav)
        if (v.label.toLowerCase().includes(ql) || v.key.includes(ql))
          out.push({ kind: 'view', label: v.label, hint: 'vista' })
      for (const s of sessions)
        if (s.task.toLowerCase().includes(ql) || s.id.includes(ql))
          out.push({ kind: 'ses', label: s.task.slice(0, 48), hint: `${s.id} · sesión` })
    }
    return out.slice(0, 14)
  }, [q, sessions, nav, skillCmds])

  if (!open) return null

  const run = (it: Item) => {
    if (it.kind === 'cmd') {
      setComposerDraft(it.label)
      setView('misiones')
    } else if (it.kind === 'view') {
      const n = nav.find((v) => v.label === it.label)
      if (n) setView(n.key)
    } else if (it.kind === 'act' && it.run) {
      it.run()
    }
    onClose()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSel((s) => (s + 1) % Math.max(1, items.length))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSel((s) => (s - 1 + items.length) % Math.max(1, items.length))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (items[sel]) run(items[sel])
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 pt-24" onClick={onClose} role="presentation">
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-label="Paleta de comandos"
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
