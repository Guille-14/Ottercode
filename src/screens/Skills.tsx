// Pantalla Skills (estilo V2): habilita/deshabilita las herramientas que
// OtterCode puede usar (bash, web_search, memoria, ficheros…), persistidas en
// el backend (skills_config.json).
import { useEffect, useState } from 'react'
import { Wrench } from 'lucide-react'
import { api, type Skill } from '../api'
import { Card } from '../ui'

export default function Skills() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const load = async () => {
    try {
      const r = await api.skills()
      setSkills(r.skills)
      setErr('')
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  useEffect(() => {
    void load()
  }, [])

  const toggle = async (s: Skill) => {
    setBusy(s.name)
    try {
      await api.setSkill(s.name, !s.enabled)
      await load()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <header>
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-panel2">
            <Wrench className="h-4.5 w-4.5 text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Skills</h1>
            <p className="text-xs text-muted">
              Herramientas del sistema y skills markdown en backend/skills/*.md. Las activas se inyectan en el system prompt.
            </p>
          </div>
        </div>
      </header>

      {err && <Card className="p-3 text-sm text-danger">{err}</Card>}

      <Card className="p-4">
        {skills.length === 0 ? (
          <p className="text-sm text-muted">Sin skills.</p>
        ) : (
          <div className="space-y-1">
            {skills.map((s) => {
              const on = s.enabled
              return (
                <div
                  key={s.name}
                  className="flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-canvas"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium">
                      <span className="oc-mono">{s.name}</span>
                      <span className="ml-2 rounded-full border border-line2 bg-panel2 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted">
                        {s.cat}
                      </span>
                    </p>
                    <p className="truncate text-xs text-muted">{s.desc}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void toggle(s)}
                    disabled={busy === s.name}
                    aria-label={`${s.name}: ${on ? 'desactivar' : 'activar'}`}
                    className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
                      on ? 'bg-accent' : 'bg-line'
                    } disabled:opacity-50`}
                  >
                    <span
                      className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${
                        on ? 'left-[22px]' : 'left-0.5'
                      }`}
                    />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      <p className="text-center text-[11px] text-muted">
        Los cambios se aplican a las próximas misiones.
      </p>
    </div>
  )
}