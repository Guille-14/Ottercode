// Pantalla Modelos (estilo V2): lista de modelos locales de Ollama, estado de
// VRAM (cargados), descarga de nuevos modelos con progreso SSE y gestión
// (borrar / copiar).
import { useEffect, useRef, useState } from 'react'
import { Box, CloudDownload, Trash2, Copy, Cpu } from 'lucide-react'
import { api, fetchWithAuth, type Pulse } from '../api'
import { useUi } from '../store'
import { Badge, Card } from '../ui'
import ConfirmDialog from '../ConfirmDialog'
import UndoToast from '../UndoToast'

function fmtBytes(n?: number): string {
  if (!n) return '—'
  const gb = n / 1024 ** 3
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`
}

export default function Modelos() {
  const [models, setModels] = useState<string[]>([])
  const [pulse, setPulse] = useState<Pulse | null>(null)
  const [err, setErr] = useState('')
  const [pullName, setPullName] = useState('')
  const [pulling, setPulling] = useState(false)
  const [progress, setProgress] = useState<{ pct: number; status: string } | null>(null)
  const pullAbort = useRef<(() => void) | null>(null)
  const [confirmName, setConfirmName] = useState<string | null>(null)
  const [undoName, setUndoName] = useState<string | null>(null)
  const undoRef = useRef<number | null>(null)
  const [info, setInfo] = useState<Record<string, { family?: string; parameter_size?: string; quantization_level?: string; context_length?: number; vision?: boolean; tools?: boolean | null; suggested_num_ctx?: number; vram_warn?: string }>>({})
  const [createName, setCreateName] = useState('')
  const [modelfile, setModelfile] = useState('FROM qwen2.5-coder:7b\nPARAMETER num_ctx 8192\n')
  const [creating, setCreating] = useState(false)

  const reload = async () => {
    try {
      const [m, p] = await Promise.all([api.models(), api.pulse()])
      setModels(m.models)
      setPulse(p)
      setErr('')
      const names = (m.models || []).slice(0, 32)
      void Promise.all(names.map(async (name) => {
        try {
          const r = await fetchWithAuth('/api/model/probe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model: name }),
          })
          const j = await r.json()
          setInfo((prev) => ({ ...prev, [name]: j }))
        } catch {
          /* probe opcional */
        }
      }))
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  useEffect(() => {
    void reload()
    const id = setInterval(() => api.pulse().then(setPulse).catch(() => undefined), 4000)
    return () => clearInterval(id)
  }, [])

  const startPull = async () => {
    const name = pullName.trim()
    if (!name || pulling) return
    setPulling(true)
    setProgress({ pct: 0, status: 'conectando…' })
    let cancelled = false
    pullAbort.current = () => {
      cancelled = true
    }
    try {
      await api.pullModel(name, (_, data) => {
        if (cancelled) return
        if (typeof data.pct === 'number') setProgress({ pct: data.pct, status: `${String(data.status ?? '')} ${data.completed}/${data.total}` })
        else setProgress((p) => ({ pct: p?.pct ?? 0, status: String(data.status ?? '') }))
      })
      if (!cancelled) {
        setProgress(null)
        setPullName('')
        await reload()
      }
    } catch (e) {
      if (!cancelled) setErr(`${name}: ${(e as Error).message}`)
    } finally {
      setPulling(false)
      pullAbort.current = null
    }
  }

  const cancelPull = () => {
    pullAbort.current?.()
    setPulling(false)
    setProgress(null)
  }

  const doDelete = (name: string) => setConfirmName(name)

  const commitDelete = async (name: string) => {
    try {
      await api.deleteModel(name)
      await reload()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const doCopy = async (name: string) => {
    const dest = window.prompt('Nuevo nombre (copia):', `${name}-copy`)
    if (!dest) return
    try {
      await api.copyModel(name, dest)
      await reload()
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const loaded = new Map(pulse?.ps.map((m) => [m.name, m]) ?? [])
  const usedVram = pulse?.ps.reduce((acc, m) => acc + (m.size_vram ?? 0), 0) ?? 0

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
      <header>
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-panel2">
            <Box className="h-4.5 w-4.5 text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Modelos</h1>
            <p className="text-xs text-muted">Modelos locales de Ollama · motor 100% local.</p>
          </div>
        </div>
      </header>

      {err && <Card className="p-3 text-sm text-danger">{err}</Card>}
      {pulse && !pulse.ok && (
        <Card className="p-3 text-sm text-danger">
          Ollama inaccesible — comprueba que <span className="oc-mono">ollama serve</span> esté corriendo.
        </Card>
      )}

      {pulse && (
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="flex items-center gap-1.5 text-sm font-semibold">
              <Cpu className="h-4 w-4 text-accent" /> VRAM ({((usedVram / (pulse.vram_total ?? 1)) * 100).toFixed(0)}%)
            </h2>
            <span className="text-xs text-muted">{fmtBytes(usedVram)} de {fmtBytes(pulse.vram_total)}</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-line">
            <div className="h-full bg-accent transition-all duration-500" style={{ width: `${Math.min(100, (usedVram / (pulse.vram_total ?? 1)) * 100)}%` }} />
          </div>
          {pulse.ps.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {pulse.ps.map((m) => (
                <Badge key={m.name} tone="ok">
                  <span className="oc-mono">{m.name}</span>
                  <span className="ml-1.5 opacity-70">{fmtBytes(m.size_vram)}</span>
                </Badge>
              ))}
            </div>
          )}
        </Card>
      )}

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Descargar modelo</h2>
        <div className="flex gap-2">
          <input
            className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-3 py-2 text-sm focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            placeholder="ej. llama3.2:1b"
            value={pullName}
            onChange={(e) => setPullName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) void startPull()
            }}
            disabled={pulling}
          />
          {pulling ? (
            <button
              type="button"
              onClick={cancelPull}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-danger/40 bg-danger/10 px-3 text-sm font-medium text-danger"
            >
              Cancelar
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void startPull()}
              disabled={!pullName.trim()}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-accent px-3 text-sm font-medium text-accentink transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              <CloudDownload className="h-4 w-4" />
              Descargar
            </button>
          )}
        </div>
        {pulling && progress && (
          <div className="mt-3 rounded-lg border border-line bg-canvas p-3">
            <div className="flex justify-between text-xs mb-1">
              <span className="oc-mono font-medium">{pullName}</span>
              <span className="oc-mono">{progress.pct}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-line">
              <div className="h-full bg-accent transition-all duration-300" style={{ width: `${Math.min(100, progress.pct)}%` }} />
            </div>
            <p className="mt-1 text-[10px] text-muted">{progress.status}</p>
          </div>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Crear desde Modelfile</h2>
        <input
          className="mb-2 w-full rounded-lg border border-line bg-canvas px-3 py-2 text-sm"
          placeholder="nombre (ej. otter-coder:local)"
          value={createName}
          onChange={(e) => setCreateName(e.target.value)}
          disabled={creating}
        />
        <textarea
          className="mb-2 min-h-[88px] w-full rounded-lg border border-line bg-canvas px-3 py-2 font-mono text-xs"
          value={modelfile}
          onChange={(e) => setModelfile(e.target.value)}
          disabled={creating}
        />
        <button
          type="button"
          disabled={!createName.trim() || !modelfile.trim() || creating}
          onClick={() => {
            const name = createName.trim()
            setCreating(true)
            void fetchWithAuth('/api/models/create', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ model: name, modelfile }),
            }).then(async (r) => {
              if (!r.ok) throw new Error(`HTTP ${r.status}`)
              setCreateName('')
              await reload()
            }).catch((e: Error) => setErr(e.message)).finally(() => setCreating(false))
          }}
          className="inline-flex h-9 items-center rounded-lg bg-accent px-3 text-sm font-medium text-accentink disabled:opacity-40"
        >
          {creating ? 'Creando…' : 'Crear modelo'}
        </button>
      </Card>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-semibold">Disponibles ({models.length})</h2>
        {models.length === 0 ? (
          <p className="text-sm text-muted">No se pudo consultar Ollama.</p>
        ) : (
          <div className="space-y-1">
            {models.map((m) => {
              const gpu = loaded.get(m)
              return (
                <div key={m} className="group flex items-center justify-between gap-3 rounded-lg px-3 py-2 hover:bg-canvas">
                  <div className="flex min-w-0 items-center gap-2">
                    <button
                      type="button"
                      className="oc-mono truncate text-sm font-medium hover:underline"
                      title="Usar en el chat"
                      onClick={() => {
                        useUi.getState().setModel(m)
                        useUi.getState().setNotice(`Modelo activo: ${m}`)
                      }}
                    >
                      {m}
                    </button>
                    {gpu && (
                      <Badge tone="ok">
                        <span className="oc-mono">{fmtBytes(gpu.size_vram)}</span>
                      </Badge>
                    )}
                    {info[m]?.family && <span className="text-[10px] text-muted">{info[m].family}</span>}
                    {info[m]?.parameter_size && <span className="text-[10px] text-muted">{info[m].parameter_size}</span>}
                    {info[m]?.quantization_level && <span className="text-[10px] text-muted">{info[m].quantization_level}</span>}
                    {info[m]?.context_length ? <span className="text-[10px] text-muted">ctx {info[m].context_length}</span> : null}
                    {info[m]?.vision ? <Badge tone="ok">visión</Badge> : null}
                    {info[m]?.tools ? <Badge tone="ok">tools</Badge> : null}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      type="button"
                      aria-label={`Copiar modelo ${m}`}
                      onClick={() => void doCopy(m)}
                      className="rounded-md p-1.5 text-muted hover:bg-panel hover:text-ink"
                      title="Copiar modelo"
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Eliminar modelo ${m}`}
                      onClick={() => void doDelete(m)}
                      className="rounded-md p-1.5 text-muted hover:bg-panel hover:text-danger"
                      title="Eliminar modelo"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>
      <ConfirmDialog
        open={Boolean(confirmName)}
        itemLabel={confirmName || 'este modelo'}
        onCancel={() => setConfirmName(null)}
        onConfirm={() => {
          const name = confirmName
          setConfirmName(null)
          if (!name) return
          setModels((ms) => ms.filter((x) => x !== name))
          setUndoName(name)
          if (undoRef.current) window.clearTimeout(undoRef.current)
          undoRef.current = window.setTimeout(() => {
            void commitDelete(name)
            setUndoName(null)
          }, 8000)
        }}
      />
      {undoName && (
        <UndoToast
          label={undoName}
          onUndo={() => {
            if (undoRef.current) window.clearTimeout(undoRef.current)
            setUndoName(null)
            void reload()
          }}
        />
      )}
    </div>
  )
}