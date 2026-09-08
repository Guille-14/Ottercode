// Pantalla Configuración: skills (toggle) y perfiles (activo + switch).

import { useEffect, useState } from 'react'
import { api, type Profile, type Skill } from '../api'
import { Badge, Card } from '../ui'
import { F } from '../features'

function SkillsPane() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [busy, setBusy] = useState('')

  const load = async () => {
    const r = await api.skills()
    setSkills(r.skills)
  }
  useEffect(() => {
    void load()
  }, [])

  const toggle = async (s: Skill) => {
    setBusy(s.name)
    try {
      await api.setSkill(s.name, !s.enabled)
      await load()
    } finally {
      setBusy('')
    }
  }

  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold">Skills</h3>
      {skills.length === 0 ? (
        <p className="text-sm text-muted">Sin skills.</p>
      ) : (
        <div className="space-y-1">
          {skills.map((s) => (
            <div
              key={s.name}
              className="flex items-center justify-between gap-3 rounded-md px-3 py-2 hover:bg-canvas"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium">
                  {s.name} <Badge>{s.cat}</Badge>
                </p>
                <p className="truncate text-xs text-muted">{s.desc}</p>
              </div>
              <button
                onClick={() => void toggle(s)}
                disabled={busy === s.name}
                className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                  s.enabled ? 'bg-accent' : 'bg-line'
                }`}
                aria-label={`${s.name}: ${s.enabled ? 'desactivar' : 'activar'}`}
              >
                <span
                  className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                    s.enabled ? 'left-[22px]' : 'left-0.5'
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

function ProfilesPane() {
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [active, setActive] = useState('')
  const [role, setRole] = useState('Programador')
  const [models, setModels] = useState<{ name: string; size_gb?: number; vram_est_gb?: number }[]>([])
  const [suggest, setSuggest] = useState<{ suggested?: string; hint?: string }>({})

  const load = async () => {
    const r = await F.loadProfiles()
    setProfiles(r.profiles)
    setActive(r.active)
  }
  useEffect(() => {
    void load()
    api.models().then((r) => {
      const details = (r as { details?: { name: string; size_gb?: number; vram_est_gb?: number }[] }).details
      setModels(details?.length ? details : (r.models || []).map((n) => ({ name: n })))
      const sg = (r as { suggest?: Record<string, { suggested?: string; hint?: string }> }).suggest
      if (sg?.Programador) setSuggest(sg.Programador)
    }).catch(() => undefined)
  }, [])

  const select = async (name: string) => {
    await F.switchProfile(name)
    await load()
  }

  return (
    <Card className="p-4" id="profileSelect">
      <h3 className="mb-3 text-sm font-semibold">Perfiles / modelo por bot</h3>
      <label className="mb-3 block text-xs text-muted">
        Rol del bot
        <input
          className="mt-1 w-full rounded-md border border-line bg-canvas px-2 py-1 text-sm text-ink"
          value={role}
          onChange={(e) => {
            setRole(e.target.value)
            api.models().then((r) => {
              const sg = (r as { suggest?: Record<string, { suggested?: string; hint?: string }> }).suggest || {}
              const hit = Object.entries(sg).find(([k]) =>
                e.target.value.toLowerCase().includes(k.toLowerCase()),
              )
              setSuggest(hit ? hit[1] : sg.Programador || {})
            }).catch(() => undefined)
          }}
        />
      </label>
      {suggest.hint && (
        <p className="mb-2 text-xs text-muted">
          Recomendación: <strong className="text-ink">{suggest.suggested}</strong> — {suggest.hint}
        </p>
      )}
      <ul className="mb-3 max-h-40 overflow-auto text-xs">
        {models.map((m) => {
          const rec = m.name === suggest.suggested
          return (
            <li
              key={m.name}
              className={`flex justify-between rounded px-2 py-1 ${rec ? 'bg-accent text-accentink' : ''}`}
            >
              <span>{m.name}{rec ? ' · recomendado' : ''}</span>
              <span className={rec ? 'opacity-80' : 'text-muted'}>
                {m.size_gb ? `${m.size_gb} GB · ~${m.vram_est_gb} GB VRAM` : ''}
              </span>
            </li>
          )
        })}
      </ul>
      {profiles.length === 0 ? (
        <p className="text-sm text-muted">Sin perfiles.</p>
      ) : (
        <div className="space-y-1">
          {profiles.map((p) => {
            const isActive = p.name === active
            return (
              <button
                key={p.name}
                onClick={() => void select(p.name)}
                className={`flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors ${
                  isActive ? 'bg-accent text-accentink' : 'hover:bg-panel'
                }`}
              >
                <span className="min-w-0">
                  <span className="block font-medium">{p.display_name}</span>
                  <span className={`oc-mono text-xs ${isActive ? 'text-accentink/70' : 'text-muted'}`}>
                    {p.model}
                  </span>
                </span>
                {isActive ? <Badge>activo</Badge> : <Badge>{p.temperature}</Badge>}
              </button>
            )
          })}
        </div>
      )}
    </Card>
  )
}

function ProjectPane() {
  const [path, setPath] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => {
    api.project().then((r) => setPath(r.path || '')).catch(() => undefined)
  }, [])
  const save = async () => {
    try {
      const r = await api.setProject(path)
      setPath(r.path)
      setMsg(r.path ? `Proyecto: ${r.path}` : 'Usando workspace por misión')
    } catch (e) {
      setMsg(String(e))
    }
  }
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold">Carpeta del proyecto</h3>
      <p className="mb-2 text-xs text-muted">
        Si la indicas, Otter trabaja ahí en lugar de workspace/&lt;misión&gt;. Vacío = sandbox por tarea.
      </p>
      <input
        className="mb-2 w-full rounded-md border border-line bg-canvas px-2 py-1 text-sm text-ink"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/home/tú/mi-repo"
      />
      <button type="button" onClick={() => void save()} className="rounded-md bg-accent px-3 py-1 text-xs text-accentink">
        Guardar
      </button>
      {msg ? <p className="mt-2 text-xs text-muted">{msg}</p> : null}
    </Card>
  )
}

function McpPane() {
  const [data, setData] = useState<{ ready?: boolean; servers?: { name: string; connected: boolean; tools: string[] }[] }>({})
  useEffect(() => {
    api.mcp().then(setData).catch(() => undefined)
  }, [])
  return (
    <Card className="p-4">
      <h3 className="mb-3 text-sm font-semibold">MCP</h3>
      <p className="mb-2 text-xs text-muted">
        Servidores en mcp_servers.json · {data.ready ? 'listo' : 'sin herramientas cargadas'}
      </p>
      {(data.servers || []).length === 0 ? (
        <p className="text-sm text-muted">Ningún servidor configurado.</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {(data.servers || []).map((s) => (
            <li key={s.name} className="flex justify-between">
              <span>{s.name}</span>
              <Badge>{s.connected ? `${s.tools.length} tools` : 'offline'}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function Configuracion() {
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Configuración</h2>
        <p className="text-sm text-muted">skills, perfiles, carpeta y MCP</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <SkillsPane />
        <ProfilesPane />
        <ProjectPane />
        <McpPane />
      </div>
    </div>
  )
}
