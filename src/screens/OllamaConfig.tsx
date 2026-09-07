// Pantalla Ajustes (Ollama): parámetros de runtime con hot-reload global y
// aplicación al perfil activo.

import { useEffect, useState } from 'react'
import { api } from '../api'
import { Button, Card, Spinner } from '../ui'

type S = Record<string, unknown>

function NumField({
  label, k, s, set, min, max, step, hint,
}: {
  label: string; k: string; s: S
  set: (k: string, v: number) => void
  min?: number; max?: number; step?: number; hint?: string
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-[11px] font-medium text-muted">
        <span>{label}</span>
      </span>
      <input
        type="number"
        min={min}
        max={max}
        step={step ?? 'any'}
        value={String(s[k] ?? '')}
        onChange={(e) => set(k, Number(e.target.value))}
        className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-sm text-ink focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
      />
      {hint && <span className="mt-0.5 block text-[10px] text-muted/70">{hint}</span>}
    </label>
  )
}

function BoolField({ label, k, s, set, hint }: { label: string; k: string; s: S; set: (k: string, v: boolean) => void; hint?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        checked={Boolean(s[k])}
        onChange={(e) => set(k, e.target.checked)}
        className="mt-0.5 h-4 w-4 accent-emerald-600"
      />
      <span>
        {label}
        {hint && <span className="block text-[10px] text-muted/70">{hint}</span>}
      </span>
    </label>
  )
}

export default function OllamaConfig() {
  const [s, setS] = useState<S | null>(null)
  const [err, setErr] = useState('')
  const [saving, setSaving] = useState(false)
  const [applied, setApplied] = useState('')
  const [dirty, setDirty] = useState(false)

  const load = async () => {
    try {
      const r = await api.settings()
      setS(r.settings)
      setErr('')
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  useEffect(() => {
    void load()
  }, [])

  const setNum = (k: string, v: number) => {
    if (!s) return
    setS({ ...s, [k]: v })
    setDirty(true)
  }
  const setBool = (k: string, v: boolean) => {
    if (!s) return
    setS({ ...s, [k]: v })
    setDirty(true)
  }

  const save = async () => {
    if (!s) return
    setSaving(true)
    setErr(''); setApplied('')
    try {
      const r = await api.saveSettings(s)
      setS(r.settings)
      setDirty(false)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }
  const reset = async () => {
    setSaving(true); setErr(''); setApplied('')
    try {
      const r = await api.resetSettings()
      setS(r.settings)
      setDirty(false)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }
  const applyProfile = async () => {
    setSaving(true); setErr(''); setApplied('')
    try {
      const r = await api.applyProfile()
      setApplied(`Aplicado al perfil «${r.profile ?? 'default'}»`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  if (!s) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-6">
        <div className="flex items-center justify-center gap-3 py-16 text-muted">
          <Spinner /> Cargando ajustes…
        </div>
        {err && <Card className="p-3 text-sm text-danger">{err}</Card>}
      </div>
    )
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Ajustes de Ollama</h2>
          <p className="text-sm text-muted">
            Parámetros de runtime con recarga en caliente (no requieren reinicio).
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void reset()} disabled={saving} variant="ghost">
            Restablecer
          </Button>
          <Button onClick={() => void applyProfile()} disabled={saving}>
            Aplicar al perfil activo
          </Button>
          <Button onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? <Spinner /> : 'Guardar'}
          </Button>
        </div>
      </div>

      {err && <Card className="p-3 text-sm text-danger">{err}</Card>}
      {applied && <Card className="p-3 text-sm text-emerald-300">{applied}</Card>}

      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold">Generación</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <NumField label="Temperatura" k="temperature" s={s} set={setNum} min={0} max={2} step={0.05} hint="Creatividad vs. determinismo" />
          <NumField label="Top P" k="top_p" s={s} set={setNum} min={0} max={1} step={0.05} hint="Recorte por probabilidad cumulativa" />
          <NumField label="Top K" k="top_k" s={s} set={setNum} min={0} step={1} hint="Recorte por ranking de tokens" />
          <NumField label="Min P" k="min_p" s={s} set={setNum} min={0} max={1} step={0.05} hint="Ignora tokens bajo P mínima" />
          <NumField label="TFS Z (tail-free)" k="tfs_z" s={s} set={setNum} min={0} max={2} step={0.05} />
          <NumField label="Typical P" k="typical_p" s={s} set={setNum} min={0} max={1} step={0.05} />
          <NumField label="Seed" k="seed" s={s} set={setNum} min={-1} step={1} hint="-1 = aleatorio cada vez" />
          <NumField label="Num predict (máx tokens)" k="num_predict" s={s} set={setNum} min={1} step={1} />
          <NumField label="Num keep" k="num_keep" s={s} set={setNum} min={0} step={1} />
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold">Contexto</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <NumField label="Num ctx (tamaño contexto)" k="num_ctx" s={s} set={setNum} min={2048} step={512} hint="Ventana de contexto en tokens" />
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold">Penalización de repetición</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <NumField label="Repeat penalty" k="repeat_penalty" s={s} set={setNum} min={0.5} max={2} step={0.05} />
          <NumField label="Repeat last N" k="repeat_last_n" s={s} set={setNum} min={0} step={1} />
          <NumField label="Presence penalty" k="presence_penalty" s={s} set={setNum} min={-2} max={2} step={0.1} />
          <NumField label="Frequency penalty" k="frequency_penalty" s={s} set={setNum} min={-2} max={2} step={0.1} />
        </div>
        <div className="mt-3">
          <BoolField label="Penalizar nueva línea" k="penalize_newline" s={s} set={setBool} hint="Evita repetir saltos de línea en código" />
        </div>
      </Card>

      <Card className="p-4">
        <h3 className="mb-3 text-sm font-semibold">Mirostat</h3>
        <div className="grid gap-3 sm:grid-cols-3">
          <NumField label="Mirostat (0=off, 1/2=on)" k="mirostat" s={s} set={setNum} min={0} max={2} step={1} />
          <NumField label="Mirostat ETA" k="mirostat_eta" s={s} set={setNum} min={0} max={1} step={0.05} />
          <NumField label="Mirostat TAU" k="mirostat_tau" s={s} set={setNum} min={0} max={10} step={0.5} />
        </div>
        <p className="mt-3 text-[11px] text-muted/70">
          Mirostat 2 suele bastar; 1 es más estricto. Desactívalo (0) y usa
          penalty estándar si buscas resultados más predecibles.
        </p>
      </Card>

      {dirty && (
        <p className="text-center text-xs text-muted">
          Tienes cambios sin guardar — pulsa «Guardar» para aplicarlos con recarga en caliente.
        </p>
      )}
    </div>
  )
}
