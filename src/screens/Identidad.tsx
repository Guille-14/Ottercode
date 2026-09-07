// Pantalla Identidad: editar SOUL.md y USER.md, gestionar Memoria y Escuadrón.

import { useEffect, useState } from 'react'
import { Bot, Search, ChevronDown, ChevronUp, Shield, Trash2, Plus } from 'lucide-react'
import { api, type IdentityDoc, type AgentInfo } from '../api'
import { Button, Card } from '../ui'
import { F } from '../features'

type Kind = 'soul' | 'user'

function Editor({ kind, title, hint }: { kind: Kind; title: string; hint: string }) {
  const [doc, setDoc] = useState<IdentityDoc>({ content: '', path: '' })
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = async () => {
    try {
      const d = await F.loadIdentity(kind)
      setDoc(d)
      setValue(d.content)
      setSaved(true)
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const save = async () => {
    setBusy(true)
    try {
      await F.saveIdentity(kind, value)
      setSaved(true)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col p-4">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="oc-mono text-xs text-muted">{doc.path.split('/').pop()}</p>
        </div>
        <Button onClick={save} disabled={busy || saved}>
          {saved ? 'Guardado' : 'Guardar'}
        </Button>
      </div>
      <p className="mb-2 text-xs text-muted">{hint}</p>
      <textarea
        id={kind === 'soul' ? 'identitySoul' : 'identityUser'}
        className="oc-mono w-full flex-1 resize-none rounded-md border border-line bg-canvas p-3 text-xs leading-relaxed focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
        rows={8}
        value={value}
        onChange={(e) => {
          setValue(e.target.value)
          setSaved(false)
        }}
      />
      {err && <p className="mt-2 text-sm text-danger">{err}</p>}
    </Card>
  )
}

function MemoryManager() {
  const [memory, setMemory] = useState<string[]>([])
  const [newItem, setNewItem] = useState('')

  const loadMemory = async () => {
    try {
      const res = await api.getMemory()
      const lines = res.content.split('\n').filter(l => l.trim().startsWith('-'))
      setMemory(lines.map(l => l.replace(/^- /, '').trim()))
    } catch (e) {
      console.error('Error loading memory:', e)
    }
  }
  useEffect(() => { loadMemory() }, [])

  const handleAdd = async () => {
    if (!newItem.trim()) return
    await api.addMemory(newItem)
    setNewItem('')
    await loadMemory()
  }

  const handleDelete = async (index: number) => {
    await api.deleteMemory(index)
    await loadMemory()
  }

  return (
    <Card className="p-4 space-y-4">
      <h3 className="text-sm font-semibold">Memoria de Sesión (MEMORY.md)</h3>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-lg border border-line bg-canvas px-3 py-2 text-xs"
          value={newItem}
          onChange={(e) => setNewItem(e.target.value)}
          placeholder="Nuevo recuerdo…"
        />
        <Button onClick={handleAdd}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      <ul className="space-y-2">
        {memory.map((item, i) => (
          <li key={i} className="flex items-center justify-between rounded-lg bg-canvas p-2 text-xs border border-line">
            <span>{item}</span>
            <Button variant="ghost" className="h-7 w-7 p-0" onClick={() => handleDelete(i)}>
              <Trash2 className="h-3.5 w-3.5 text-danger" />
            </Button>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function AgentCard({ agent }: { agent: AgentInfo }) {
  const [expanded, setExpanded] = useState(false)
  const isCore = !agent.dynamic
  const tools = agent.tools_disponibles ?? []

  return (
    <div
      className="flex flex-col rounded-xl border border-line bg-panel p-3.5 transition-all hover:border-ink/20"
      style={{ borderLeftColor: agent.color_neon || undefined, borderLeftWidth: '3px' }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-canvas border border-line text-base">
            {agent.icon}
          </span>
          <div className="min-w-0">
            <h4 className="truncate text-xs font-bold text-ink">{agent.nombre}</h4>
            <p className="truncate text-[11px] text-muted">{agent.rol || agent.role || ''}</p>
          </div>
        </div>
        <span
          className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-bold ${
            isCore ? 'bg-accent/15 text-accent' : 'bg-canvas text-muted border border-line'
          }`}
        >
          {isCore ? 'NÚCLEO' : 'PRESET'}
        </span>
      </div>

      {tools.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1">
          {tools.slice(0, expanded ? undefined : 6).map((t) => (
            <span
              key={t}
              className="rounded bg-canvas/60 border border-line/60 px-1.5 py-0.5 text-[9px] font-mono text-muted"
            >
              {t}
            </span>
          ))}
          {!expanded && tools.length > 6 && (
            <span className="rounded bg-canvas/60 px-1 py-0.5 text-[9px] text-muted">
              +{tools.length - 6}
            </span>
          )}
        </div>
      )}

      {agent.system_prompt && (
        <div className="mt-3 border-t border-line/60 pt-2">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex w-full items-center justify-between text-[11px] text-muted hover:text-ink font-medium"
          >
            <span>{expanded ? 'Ocultar detalles' : 'Ver directivas'}</span>
            {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
          {expanded && (
            <pre className="mt-2 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-lg bg-canvas p-2.5 font-mono text-[10px] leading-relaxed text-muted border border-line">
              {agent.system_prompt}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

export default function Identidad() {
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [query, setQuery] = useState('')
  const [tab, setTab] = useState<'agents' | 'identity' | 'memory'>('agents')

  useEffect(() => {
    api.agents()
      .then((r) => {
        if (r.agents) setAgents(r.agents)
      })
      .catch(() => undefined)
  }, [])

  const filtered = agents.filter((a) => {
    if (!query.trim()) return true
    const q = query.toLowerCase()
    const rolStr = (a.rol || a.role || '').toLowerCase()
    return (
      a.nombre.toLowerCase().includes(q) ||
      rolStr.includes(q) ||
      a.id.toLowerCase().includes(q)
    )
  })

  const coreAgents = filtered.filter((a) => !a.dynamic)
  const presetAgents = filtered.filter((a) => a.dynamic)

  return (
    <div id="pane-identity" className="space-y-5 pb-8">
      {/* Cabecera */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div>
          <h2 className="text-lg font-bold text-ink">Identidad & Sistema</h2>
          <p className="text-xs text-muted">
            Configuración, memoria atómica y escuadrón de agentes.
          </p>
        </div>

        {/* Selector de Pestañas */}
        <div className="flex rounded-xl border border-line bg-panel p-1 text-xs">
          <button
            type="button"
            onClick={() => setTab('agents')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-semibold transition-all ${
              tab === 'agents' ? 'bg-accent text-accentink shadow-xs' : 'text-muted hover:text-ink'
            }`}
          >
            <Bot className="h-3.5 w-3.5" />
            <span>Agentes ({agents.length || 25})</span>
          </button>
          <button
            type="button"
            onClick={() => setTab('memory')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-semibold transition-all ${
              tab === 'memory' ? 'bg-accent text-accentink shadow-xs' : 'text-muted hover:text-ink'
            }`}
          >
            <Shield className="h-3.5 w-3.5" />
            <span>Memoria</span>
          </button>
          <button
            type="button"
            onClick={() => setTab('identity')}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-semibold transition-all ${
              tab === 'identity' ? 'bg-accent text-accentink shadow-xs' : 'text-muted hover:text-ink'
            }`}
          >
            <Shield className="h-3.5 w-3.5" />
            <span>SOUL/USER.md</span>
          </button>
        </div>
      </div>

      {tab === 'identity' && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Editor kind="soul" title="SOUL.md" hint="Personalidad base" />
          <Editor kind="user" title="USER.md" hint="Preferencias usuario" />
        </div>
      )}

      {tab === 'memory' && <MemoryManager />}

      {tab === 'agents' && (
        <div className="space-y-6">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted" />
            <input
              type="text"
              placeholder="Buscar agente..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full rounded-xl border border-line bg-panel py-2 pl-9 pr-3 text-xs text-ink focus:outline-none focus:border-ink/40"
            />
          </div>

          {coreAgents.length > 0 && (
            <div className="space-y-2.5">
              <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">Núcleo</h3>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {coreAgents.map((a) => <AgentCard key={a.id} agent={a} />)}
              </div>
            </div>
          )}

          {presetAgents.length > 0 && (
            <div className="space-y-2.5">
              <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">Especialistas</h3>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {presetAgents.map((a) => <AgentCard key={a.id} agent={a} />)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
