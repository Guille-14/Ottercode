// Panel derecho de monitorización de hardware estilo V2: widget flotante con
// VRAM (pesos/KV/libre + Flush), RAM del sistema y CPU, alimentado por
// GET /api/system (proc, sin dependencias). Colapsable y flotante.
import { useEffect, useState } from 'react'
import { Activity, Cpu, MemoryStick, ChevronDown, Zap, X } from 'lucide-react'
import { api, type SystemStats } from './api'
import { Spinner } from './ui'

function fmtBytes(n?: number): string {
  if (!n) return '—'
  const gb = n / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

function Meter({
  label,
  pct,
  value,
  tone = 'accent',
}: {
  label: string
  pct: number
  value: string
  tone?: 'accent' | 'amber' | 'red'
}) {
  const color =
    tone === 'amber' ? 'bg-warning' : tone === 'red' ? 'bg-danger' : 'bg-accent'
  return (
    <div>
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-ink2">{label}</span>
        <span className="oc-mono font-medium text-ink">{value}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-line2/60">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
    </div>
  )
}

export default function HwMonitor({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [sys, setSys] = useState<SystemStats | null>(null)
  const [err, setErr] = useState('')
  const [min, setMin] = useState(false)
  const [flushBusy, setFlushBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    const tick = async () => {
      try {
        const s = await api.system()
        setSys(s)
        setErr('')
      } catch (e) {
        setErr((e as Error).message)
      }
    }
    void tick()
    const id = setInterval(tick, 2500)
    return () => clearInterval(id)
  }, [open])

  const flush = async () => {
    setFlushBusy(true)
    try {
      await api.flush()
      const s = await api.system()
      setSys(s)
    } catch {
      /* noop */
    } finally {
      setFlushBusy(false)
    }
  }

  if (!open) return null

  const vram = sys?.vram
  const ram = sys?.ram
  const cpu = sys?.cpu
  const vramPct = vram && vram.total ? (vram.used / vram.total) * 100 : 0
  const ramPct = ram && ram.total ? (ram.used / ram.total) * 100 : 0

  return (
    <div className="fixed bottom-4 right-4 z-40 w-64 overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
      <div className="flex h-9 items-center justify-between border-b border-line px-3">
        <button
          type="button"
          onClick={() => setMin((m) => !m)}
          className="flex items-center gap-1.5 text-xs font-semibold text-ink"
        >
          <Activity className="h-3.5 w-3.5 text-accent" />
          Hardware
          <ChevronDown
            className={`h-3 w-3 text-muted transition-transform ${min ? 'rotate-180' : ''}`}
          />
        </button>
        <button type="button" onClick={onClose} className="rounded p-0.5 text-muted hover:text-ink" title="Ocultar monitor">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {!min && (
        <div className="space-y-3 p-3">
          {err && <p className="text-[11px] text-danger">{err}</p>}
          {!sys && !err && (
            <div className="flex justify-center py-2">
              <Spinner />
            </div>
          )}
          {sys && (
            <>
              {/* VRAM */}
              <div className="rounded-lg border border-line bg-panel2/40 p-2.5">
                <div className="mb-2 flex items-center justify-between text-[11px]">
                  <span className="flex items-center gap-1 font-medium text-ink2">
                    <MemoryStick className="h-3 w-3 text-accent" /> VRAM
                  </span>
                  <span className="oc-mono text-ink">
                    {fmtBytes(vram?.used)} / {fmtBytes(vram?.total)}
                  </span>
                </div>
                <Meter label="Uso" pct={vramPct} value={`${vramPct.toFixed(0)}%`} />
                <div className="mt-2 space-y-1 text-[10px] text-muted">
                  {vram && vram.models.length > 0
                    ? vram.models.map((m) => (
                        <div key={m.name} className="flex justify-between gap-2">
                          <span className="oc-mono truncate">{m.name}</span>
                          <span className="oc-mono">{fmtBytes(m.size_vram)}</span>
                        </div>
                      ))
                    : <span>sin modelos en memoria</span>}
                </div>
                <button
                  type="button"
                  onClick={flush}
                  disabled={flushBusy}
                  className="mt-2.5 inline-flex h-7 w-full items-center justify-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 text-[11px] font-medium text-accent transition-colors hover:bg-accent/20 disabled:opacity-50"
                >
                  {flushBusy ? <Spinner /> : <Zap className="h-3 w-3" />}
                  Flush VRAM
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg border border-line bg-panel2/40 p-2.5">
                  <div className="mb-1.5 flex items-center justify-between text-[11px]">
                    <span className="font-medium text-ink2">RAM</span>
                    <span className="oc-mono text-ink">
                      {fmtBytes(ram?.used)}/{fmtBytes(ram?.total)}
                    </span>
                  </div>
                  <Meter label="uso" pct={ramPct} value={`${ramPct.toFixed(0)}%`} tone={ramPct > 85 ? 'red' : ramPct > 70 ? 'amber' : 'accent'} />
                </div>
                <div className="rounded-lg border border-line bg-panel2/40 p-2.5">
                  <div className="mb-1.5 flex items-center justify-between text-[11px]">
                    <span className="flex items-center gap-1 font-medium text-ink2">
                      <Cpu className="h-3 w-3 text-accent" /> CPU
                    </span>
                    <span className="oc-mono text-ink">{cpu?.count ?? '—'} núc.</span>
                  </div>
                  <Meter label="carga" pct={cpu?.percent ?? 0} value={`${(cpu?.percent ?? 0).toFixed(1)}%`} />
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}