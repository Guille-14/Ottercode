// Pantalla Misiones (Chat): conversación con agentes locales, error/pendientes
// globales, lista de mensajes y barra de entrada. El selector de modelo vive en
// la sidebar y la columna de artefactos se gestiona desde App (V2 shell).

import { useEffect, useMemo, useRef, useState } from 'react'
import { Download, Plus, AlertCircle } from 'lucide-react'
import { useUi, isDoneName } from '../store'
import { api } from '../api'
import { Button, Card } from '../ui'
import ChatHero from './ChatHero'
import ChatMessageList from './ChatMessageList'
import ChatInputBar from './ChatInputBar'
import StatusTicker from '../StatusTicker'

export default function Misiones({ hideLogs }: { hideLogs: boolean }) {
  const {
    taskId,
    mission,
    streaming,
    missionError,
    startMission,
    stopMission,
    clearMission,
    pendingPerm,
    approvePerm,
    ctxHint,
    dismissCtxHint,
    applyCtxHint,
    artifactsOpen,
    artifactsUserClosed,
    setArtifactsOpen,
  } = useUi()
  const model = useUi((s) => s.model)
  const agentMode = useUi((s) => s.agentMode)
  const startAgent = useUi((s) => s.startAgent)

  const lastPayload = useRef<Record<string, unknown> | null>(null)
  const [ckpt, setCkpt] = useState<{ task_id: string; last: Record<string, unknown>; steps: number } | null>(null)
  useEffect(() => {
    api.checkpoints().then((r) => {
      setCkpt(r.unfinished?.[0] ?? null)
    }).catch(() => undefined)
  }, [taskId, streaming])

  const done = mission.some((e) => isDoneName(e.name))

  const onLaunch = (p: Record<string, unknown>) => {
    // Continuidad: si ya hay una conversación (terminada O aún en curso),
    // encadenar continue_task para que la nueva generación SIGA la misma web.
    // Si la anterior sigue corriendo, force=true aborta el relevo y toma el
    // control (el workspace se hereda copiado, los ficheros persisten).
    const unfinished = Boolean(taskId) && !done
    const payload: Record<string, unknown> = {
      model,
      ...p,
      mode: (typeof p.mode === 'string' && p.mode) ? p.mode : (agentMode || 'chat'),
      start_agent: (typeof p.start_agent === 'string' && p.start_agent)
        ? p.start_agent
        : (startAgent || (agentMode === 'chain' ? 'architect' : 'agent')),
      ...(taskId && !p.continue_task ? { continue_task: taskId } : {}),
      ...(unfinished && !p.continue_task ? { force: true } : {}),
    }
    lastPayload.current = payload
    void startMission(payload)
  }

  useEffect(() => {
    // Nunca reabrir si el usuario lo cerró (el stream de tokens lo reabría).
    if (artifactsOpen || artifactsUserClosed) return
    const detectCodeBlock = mission.some((e) => {
      if (e.name === 'token' && typeof e.data.token === 'string') {
        return e.data.token.includes('```html') || e.data.token.includes('```svg') || e.data.token.includes('<!DOCTYPE html')
      }
      return false
    })
    if (detectCodeBlock) setArtifactsOpen(true)
  }, [mission, artifactsOpen, artifactsUserClosed, setArtifactsOpen])

  const handleClear = () => {
    clearMission()
  }

  const handleRetry = () => {
    if (streaming) return
    const prev = lastPayload.current || {}
    const tid = taskId || (typeof prev.continue_task === 'string' ? prev.continue_task : '')
    void startMission({
      ...prev,
      task: String(prev.task || 'continuar desde el último checkpoint'),
      continue_task: tid || undefined,
      resume_checkpoint: tid || undefined,
      force: true,
    })
  }

  const memoryBlock = useMemo(() => {
    if (mission.length > 0 && mission[0].data.memory_block) {
      return String(mission[0].data.memory_block)
    }
    return ''
  }, [mission])

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      {/* Modal de Aprobación de Permisos (Fase 5) */}
      {pendingPerm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 p-4">
          <Card className="w-full max-w-md p-4 shadow-xl">
            <div className="flex items-center gap-2 text-danger">
              <AlertCircle className="h-5 w-5" />
              <h3 className="font-semibold">Aprobación necesaria</h3>
            </div>
            <p className="mt-2 text-sm text-ink">{pendingPerm.title}</p>
            <p className="mt-1 text-xs text-muted">
              La herramienta solicitada requiere tu confirmación antes de ejecutarse en el sistema.
            </p>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button variant="ghost" onClick={() => void approvePerm(pendingPerm.id, false)}>
                Denegar
              </Button>
              <Button variant="ghost" onClick={() => void approvePerm(pendingPerm.id, true, true)}>
                Siempre esta sesión
              </Button>
              <Button onClick={() => void approvePerm(pendingPerm.id, true)}>
                Una vez
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Contexto de Memoria (Vault) */}
      {memoryBlock && (
        <div className="border-b border-success/20 bg-success/5 px-4 py-2 text-xs text-success">
          <span className="font-semibold uppercase tracking-wider">🧠 Contexto de Memoria: </span>
          <span>{memoryBlock}</span>
        </div>
      )}

      {/* Error global de misión */}
      {ckpt && !streaming && (
        <div className="border-b border-accent/20 bg-accent/5 px-4 py-2 text-xs text-ink">
          Checkpoint sin terminar: <span className="oc-mono">{ckpt.task_id}</span>
          {' · '}{String(ckpt.last?.done ?? ckpt.steps + ' pasos')}
          <button
            type="button"
            className="ml-2 rounded border border-line px-2 py-0.5 font-semibold"
            onClick={() => {
              onLaunch({
                task: String(ckpt.last?.pending || 'continuar misión'),
                resume_checkpoint: ckpt.task_id,
                continue_task: ckpt.task_id,
              })
              setCkpt(null)
            }}
          >
            Reanudar
          </button>
          <button type="button" className="ml-1 text-muted" onClick={() => setCkpt(null)}>
            Empezar de cero
          </button>
        </div>
      )}
      {missionError && (
        <div className="border-b border-danger/20 bg-danger/5 px-4 py-2 text-xs text-danger">
          Error en la misión: {missionError}
        </div>
      )}
      {ctxHint && (
        <div className="flex flex-wrap items-center gap-2 border-b border-accent/20 bg-accent/5 px-4 py-2 text-xs text-ink">
          <span>
            Compacta muy a menudo ({ctxHint.compact_hits}×). ctx={ctxHint.current_ctx}
            {ctxHint.current_tps != null ? ` · ${ctxHint.current_tps} tok/s` : ''}
            {ctxHint.next_ctx
              ? ` → ${ctxHint.next_ctx}${ctxHint.next_tps != null ? ` · ${ctxHint.next_tps} tok/s` : ''}`
              : ctxHint.has_bench
                ? ' · no hay peldaño más alto medido'
                : ' · sin calibración (Ajustes → Recalibrar)'}
          </span>
          {Boolean(ctxHint.next_ctx || ctxHint.recommended) && (
            <Button variant="ghost" onClick={() => void applyCtxHint()}>
              Subir ctx
            </Button>
          )}
          <button type="button" className="text-muted" onClick={() => dismissCtxHint()}>
            Cerrar
          </button>
        </div>
      )}

      {/* Contenedor central del chat */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        {mission.length === 0 && !streaming ? (
          <div className="flex-1 overflow-y-auto">
            <ChatHero
              model={model}
              mode={agentMode}
              onSelectPrompt={(text) => onLaunch({ task: text, mode: agentMode, start_agent: startAgent })}
            />
          </div>
        ) : (
          <ChatMessageList
            mission={mission}
            streaming={streaming}
            taskId={taskId}
            onRetry={handleRetry}
            canRetry={Boolean(lastPayload.current || taskId)}
            hideLogs={hideLogs}
          />
        )}

        {/* Barra flotante inferior estilo OpenWebUI */}
        <StatusTicker />
        <ChatInputBar
          onLaunch={onLaunch}
          streaming={streaming}
          onStop={() => stopMission(true)}
        />

        {/* Acción rápida ZIP / Nuevo bajo la conversación */}
        {taskId && (
          <div className="flex items-center justify-end gap-2 px-4 pb-1">
            {!streaming && mission.some((e) => isDoneName(e.name)) && (
              <a
                href={api.zipUrl(taskId)}
                className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 text-[11px] font-medium text-ink transition-colors hover:bg-canvas"
              >
                <Download className="h-3 w-3" /> Descargar ZIP
              </a>
            )}
            {(mission.length > 0 || streaming) && (
              <button
                type="button"
                onClick={handleClear}
                className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 text-[11px] font-medium text-muted transition-colors hover:bg-canvas hover:text-ink"
              >
                <Plus className="h-3 w-3" /> Nuevo
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}