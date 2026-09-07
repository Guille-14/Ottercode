// Pantalla Estado (Monitor): latido (poll), actividad en vivo, VRAM y modelos.

import { useEffect, useState } from 'react'
import { api, type Pulse, type GpuModel } from '../api'
import { Badge, Button, Card, Spinner } from '../ui'

function fmtBytes(n?: number): string {
  if (!n) return '—'
  const gb = n / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

function GpuRow({ m }: { m: GpuModel }) {
  return (
    <div className="flex items-center justify-between rounded-md bg-canvas px-3 py-2 text-sm">
      <span className="oc-mono">{m.name}</span>
      <span className="text-muted">{fmtBytes(m.size_vram)} en VRAM</span>
    </div>
  )
}

export default function Estado() {
  const [pulse, setPulse] = useState<Pulse | null>(null)
  const [err, setErr] = useState('')
  const [flushBusy, setFlushBusy] = useState(false)

  useEffect(() => {
    let on = true
    const tick = async () => {
      try {
        const p = await api.pulse()
        if (on) {
          setPulse(p)
          setErr('')
        }
      } catch (e) {
        if (on) setErr((e as Error).message)
      }
    }
    void tick()
    const id = setInterval(tick, 3000)
    return () => {
      on = false
      clearInterval(id)
    }
  }, [])

  const flush = async () => {
    setFlushBusy(true)
    try {
      await api.flush()
      const p = await api.pulse()
      setPulse(p)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setFlushBusy(false)
    }
  }

  const act = pulse?.activity
  const usedVram = pulse?.ps.reduce((acc, m) => acc + (m.size_vram ?? 0), 0) ?? 0

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Estado</h2>
          <p className="text-sm text-muted">
            {pulse ? `versión ${pulse.version} · motor local sobre Ollama` : 'conectando…'}
          </p>
        </div>
        <Button onClick={() => void flush()} disabled={flushBusy}>
          {flushBusy ? <Spinner /> : 'Liberar VRAM'}
        </Button>
      </div>

      {err && <Card className="p-3 text-sm text-danger">{err}</Card>}

      {pulse && !pulse.ok && (
        <div className="rounded-md border border-danger/20 bg-danger/5 px-3 py-2 text-sm text-danger">
          Ollama inaccesible — comprueba que <span className="oc-mono">ollama serve</span> esté corriendo.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Actividad</h3>
            {act?.running ? <Badge tone="ok">en marcha</Badge> : <Badge>en reposo</Badge>}
          </div>
          {act?.running ? (
            <div className="space-y-1 text-sm">
              <p className="font-medium">{act.agent ?? act.current ?? '—'}</p>
              {act.text && <p className="text-muted">{act.text}</p>}
              {act.task_id && <p className="oc-mono text-xs text-muted">{act.task_id}</p>}
              {typeof act.elapsed_s === 'number' && (
                <p className="text-xs text-muted">transcurrido {act.elapsed_s.toFixed(1)} s</p>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted">Sin misiones activas.</p>
          )}
        </Card>

        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">
              VRAM ({((usedVram / (pulse?.vram_total ?? 1)) * 100).toFixed(0)}%)
            </h3>
            <span className="text-xs text-muted">total {fmtBytes(pulse?.vram_total)}</span>
          </div>
          <div className="mb-3 h-2 w-full overflow-hidden rounded-full border border-line bg-canvas">
            <div
              className="h-full bg-accent"
              style={{ width: `${Math.min(100, (usedVram / (pulse?.vram_total ?? 1)) * 100)}%` }}
            />
          </div>
          {pulse && pulse.ps.length > 0 ? (
            <div className="space-y-1.5">
              {pulse.ps.map((m) => (
                <GpuRow key={m.name} m={m} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">Ningún modelo en memoria.</p>
          )}
        </Card>
      </div>

      <Card className="p-4">
        <h3 className="mb-2 text-sm font-semibold">Modelos disponibles</h3>
        {pulse && pulse.models.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {pulse.models.map((m) => (
              <Badge key={m}>{m}</Badge>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">No se pudo consultar Ollama.</p>
        )}
        {pulse && (
          <p className="mt-3 text-xs text-muted">
            Predeterminado: <span className="oc-mono">{pulse.default_model}</span> · ctx{' '}
            {pulse.num_ctx_default}
          </p>
        )}
      </Card>
    </div>
  )
}