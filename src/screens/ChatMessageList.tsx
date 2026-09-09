// Lista de mensajes estilo OtterCode V2: una burbuja por agente, pensamiento
// (thought-block) y herramientas plegables, markdown en vivo, artefactos, error
// con reintentar y acciones de copia/zip. hideLogs oculta el razonamiento
// intermedio (interruptor "Logs" de la cadena).

import { memo, useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { Bot, User, ChevronDown, Wrench, Copy, Check, FileCode, RotateCcw, Download, Loader2 } from 'lucide-react'
import type { MissionEvent } from '../store'
import { useUi, isDoneName } from '../store'
import { api } from '../api'
import { Button } from '../ui'
import { F } from '../features'
import DiffView from '../DiffView'

interface ToolSeg {
  id: string
  name: string
  title: string
  output?: string
  ok?: boolean
}

interface Seg {
  key: string
  type: 'agent' | 'system'
  agent: string
  buf: string
  final: boolean
  tools: ToolSeg[]
  systemText?: string
  missionIndex: number // Para ordenar correctamente
}

// Caché de segmentos "cerrados": un segmento queda congelado cuando se fija su
// rango [start,end] en la misión (llegó su agent_end o comenzó el siguiente
// agente). Solo la burbuja activa (tail abierto, en streaming) se reconstruye
// con cada lote de tokens; el resto conserva identidad estable y React.memo
// evita re-renderizarlas. Las claves usan ids de evento (únicos globales),
// así que no hay colisiones entre misiones distintas.
const segCache = new Map<string, Seg>()

function segIsClosed(mission: MissionEvent[], start: number, end: number): boolean {
  for (let i = start; i <= end; i++) {
    if (mission[i].name === 'agent_end') return true
  }
  return false
}

function buildSeg(
  mission: MissionEvent[],
  start: number,
  end: number,
  agent: string,
  final: boolean,
): Seg {
  const seg: Seg = { key: '', type: 'agent', agent, buf: '', final, tools: [], missionIndex: start }
  for (let i = start; i <= end; i++) {
    const ev = mission[i]
    if (ev.name === 'token' && typeof ev.data.token === 'string') {
      seg.buf += ev.data.token
    } else if (ev.name === 'tool_call') {
      const id = String(ev.data.id ?? ev.id)
      const name = String(ev.data.tool ?? ev.data.name ?? 'tool')
      const title = String(ev.data.title ?? name)
      seg.tools.push({ id, name, title })
    } else if (ev.name === 'tool_result') {
      if (seg.tools.length > 0) {
        const last = seg.tools[seg.tools.length - 1]
        last.output = String(ev.data.output ?? '')
        last.ok = Boolean(ev.data.ok)
      }
    } else if (ev.name === 'diff') {
      const d = String(ev.data.diff ?? '')
      if (d && seg.tools.length > 0) {
        const last = seg.tools[seg.tools.length - 1]
        if (!last.output || !last.output.includes('```diff')) last.output = d
      }
    }
  }
  return seg
}

type TimelineItem =
  | { kind: 'user'; key: string; text: string }
  | { kind: 'seg'; key: string; seg: Seg }

function buildTimeline(mission: MissionEvent[]): TimelineItem[] {
  const out: TimelineItem[] = []
  let i = 0
  while (i < mission.length) {
    const ev = mission[i]
    if (ev.name === 'user' && ev.data.text) {
      out.push({ kind: 'user', key: `user:${ev.id}`, text: String(ev.data.text) })
      i++
      continue
    }
    if (ev.name === 'system') {
      const seg: Seg = {
        key: `sys:${ev.id}`,
        type: 'system',
        agent: 'Otter',
        buf: '',
        final: true,
        tools: [],
        systemText: String(ev.data.text ?? ''),
        missionIndex: i,
      }
      out.push({ kind: 'seg', key: seg.key, seg })
      i++
      continue
    }
    if (ev.name === 'agent_start') {
      const agent = String(ev.data.nombre ?? ev.data.agent ?? 'Otter')
      const start = i
      let end = i
      let j = i + 1
      while (j < mission.length) {
        const n = mission[j].name
        if (n === 'agent_start') break
        end = j
        j++
        if (n === 'agent_end') break
      }
      const closed = segIsClosed(mission, start, end)
      const key = `${ev.id}:${end}`
      const cached = closed ? segCache.get(key) : undefined
      if (cached) {
        out.push(cached)
      } else {
        const seg = buildSeg(mission, start, end, agent, closed)
        seg.key = key
        seg.type = 'agent'
        seg.missionIndex = start
        if (closed) segCache.set(key, seg)
        out.push(seg)
      }
      i = j
      continue
    }
    i++
  }
  return out
}

function splitThinking(buf: string): { thought: string; text: string } {
  // Captura bloques <think>...</think>
  const thinkMatch = buf.match(/<think>([\s\S]*?)<\/think>/i)
  if (thinkMatch) {
    return {
      thought: thinkMatch[1].trim(),
      text: buf.replace(thinkMatch[0], '').trim(),
    }
  }
  // Fallback a formato legacy si no hay tags
  if (buf.includes(' thinking')) {
    const parts = buf.split(' response')
    if (parts.length > 1) {
      return {
        thought: parts[0].replace(' thinking', '').trim(),
        text: parts.slice(1).join('').trim(),
      }
    }
    return { thought: buf.replace(' thinking', '').trim(), text: '' }
  }
  return { thought: '', text: buf }
}

function PlanCard({
  mission,
  taskId,
  streaming,
}: {
  mission: MissionEvent[]
  taskId: string | null
  streaming: boolean
}) {
  const startMission = useUi((s) => s.startMission)
  const model = useUi((s) => s.model)
  const done = [...mission].reverse().find((e) => e.name === 'task_done')
  const ready = Boolean(done?.data.plan_ready)
  const [plan, setPlan] = useState(String(done?.data.plan ?? ''))
  const [editing, setEditing] = useState(false)
  if (!ready || streaming) return null
  const context = String(done?.data.context ?? '')
  const original = String((done?.data.task as string) || '')
  return (
    <div className="rounded-xl border border-accent/30 bg-accent/5 p-3">
      <p className="mb-2 text-xs font-semibold text-ink">Plan listo — revisa antes de ejecutar</p>
      {editing ? (
        <textarea className="mb-2 w-full rounded-lg border border-line bg-canvas p-2 text-xs" rows={8} value={plan} onChange={(e) => setPlan(e.target.value)} />
      ) : (
        <pre className="mb-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-ink">{plan}</pre>
      )}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void startMission({ task: original || 'ejecutar plan aprobado', model, mode: 'chat', start_agent: 'agent', resume_plan: { plan, context }, resume_task: original, continue_task: taskId || undefined })}>Ejecutar</Button>
        <Button variant="ghost" onClick={() => setEditing((v) => !v)}>{editing ? 'Vista' : 'Editar'}</Button>
        <Button variant="ghost" onClick={() => void startMission({ task: `Regenera el plan: ${plan.slice(0, 400)}`, model, mode: 'chat', start_agent: 'agent', plan_only: true, continue_task: taskId || undefined })}>Regenerar</Button>
        <Button variant="ghost" onClick={() => {
          const reason = window.prompt('Motivo del rechazo (opcional)') || ''
          setPlan('')
          void startMission({
            task: reason
              ? `El usuario rechazó el plan. Motivo: ${reason}. Propón un enfoque distinto.`
              : 'El usuario rechazó el plan. Propón un enfoque distinto.',
            model, mode: 'chat', start_agent: 'agent', plan_only: true,
            continue_task: taskId || undefined,
          })
        }}>Rechazar</Button>
      </div>
    </div>
  )
}

function ErrorCard({
  data,
  canRetry,
  onRetry,
}: {
  data: Record<string, unknown>
  canRetry: boolean
  onRetry: () => void
}) {
  const [open, setOpen] = useState(false)
  const typeName = String(data.error_type ?? '')
  const raw = String(data.detail || data.message || 'Error en la misión')
  const step = String(data.step ?? '')
  const headline = typeName ? `Error: ${typeName}` : raw.split('\n')[0]
  return (
    <div className="rounded-xl border border-danger/30 bg-danger/5 p-3 text-xs text-danger">
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          title={raw}
          onClick={() => setOpen((v) => !v)}
          className="min-w-0 flex-1 text-left font-semibold hover:underline"
        >
          {headline}
        </button>
        <Button variant="ghost" onClick={onRetry} className="h-7 shrink-0 text-xs" disabled={!canRetry}>
          <RotateCcw className="mr-1 h-3 w-3" /> Reintentar
        </Button>
      </div>
      <p className="mt-2 whitespace-pre-wrap text-[11px] text-ink" title={raw}>
        {open ? raw : raw.slice(0, 280) + (raw.length > 280 ? '…' : '')}
        {step ? ` · paso: ${step}` : ''}
      </p>
    </div>
  )
}

function SalvageProgressCard({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-accent/30 bg-accent/10 p-3 text-xs text-accent">
      <Loader2 className="h-4 w-4 animate-spin shrink-0" />
      <div className="flex flex-col">
        <span className="font-semibold">Continuando escritura (modo completación)…</span>
        <span className="text-[10px] opacity-80">{text.replace("🛟", "").trim()}</span>
      </div>
    </div>
  )
}

function SystemBubble({ text }: { text: string }) {
  if (text.startsWith("🛟")) return <SalvageProgressCard text={text} />
  return (
    <div className="rounded-xl border border-line bg-panel2 p-3 text-xs text-muted">
      <span className="font-semibold uppercase tracking-wider">ℹ️ Sistema: </span>
      <span>{text}</span>
    </div>
  )
}

const SegBubble = memo(function SegBubble({
  seg,
  streaming,
  last,
  hideLogs,
}: {
  seg: Seg
  streaming: boolean
  last: boolean
  hideLogs: boolean
}) {
  const [copied, setCopied] = useState(false)
  const [thoughtOpen, setThoughtOpen] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(true)
  const { thought, text } = useMemo(() => splitThinking(seg.buf), [seg.buf])

  const copy = async () => {
    if (!text) return
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-panel text-ink shadow-sm">
        <Bot className="h-4 w-4 text-ink" />
      </div>

      <div className="min-w-0 flex-1 space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-ink">
            {seg.agent}
          </span>
          <span className="rounded-full bg-canvas px-2 py-0.5 text-[10px] text-muted">agente local</span>
          {last && streaming && <span className="oc-caret" />}
          {last && !streaming && seg.final && <span className="h-1.5 w-1.5 rounded-full bg-accent" />}
        </div>

        {!hideLogs && (
          <>
            {/* Acordeón de Razonamiento ( thought-block) */}
            {thought && (
              <div className="rounded-xl border border-line bg-codebg p-2.5 text-xs">
                <button
                  type="button"
                  onClick={() => setThoughtOpen((v) => !v)}
                  className="flex w-full items-center justify-between font-medium text-muted hover:text-ink"
                >
                  <span className="flex items-center gap-1.5">
                    <Loader2 className={`h-3 w-3 ${thoughtOpen ? 'rotate-0' : ''}`} />
                    Razón ({seg.agent})
                  </span>
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${thoughtOpen ? 'rotate-180' : ''}`} />
                </button>
                {thoughtOpen && (
                  <p className="mt-2 whitespace-pre-wrap border-t border-line/60 pt-2 text-muted">
                    {F.stripToolJson(F.escapeHtml(thought))}
                  </p>
                )}
              </div>
            )}

            {/* Acordeón de Herramientas ejecutadas */}
            {seg.tools.length > 0 && (
              <div className="rounded-xl border border-line bg-panel p-2.5">
                <button
                  type="button"
                  onClick={() => setToolsOpen((v) => !v)}
                  className="flex w-full items-center justify-between text-xs font-medium text-muted hover:text-ink"
                >
                  <span className="flex items-center gap-1.5">
                    <Wrench className="h-3.5 w-3.5" />
                    Herramientas ({seg.tools.length})
                  </span>
                  <ChevronDown className={`h-3.5 w-3.5 transition-transform ${toolsOpen ? 'rotate-180' : ''}`} />
                </button>
                {toolsOpen && (
                  <div className="src-chips mt-2 space-y-1.5 border-t border-line pt-2">
                    {seg.tools.map((t) => (
                      <div key={t.id} className="rounded-lg border border-line bg-canvas p-2 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="oc-mono font-medium">{t.title}</span>
                          <span className={`text-[10px] font-semibold ${t.ok ? 'text-success' : 'text-danger'}`}>
                            {t.ok ? '✓ OK' : '✕'}
                          </span>
                        </div>
                        {t.output && (t.output.includes('```diff') || t.output.includes('\n--- ') || t.output.startsWith('--- ')) ? (
                          <div className="mt-1.5">
                            <DiffView text={t.output} />
                          </div>
                        ) : t.output ? (
                          <p className="pane-code mt-1 truncate oc-mono text-[11px] text-muted">
                            {F.collapseBigFences(t.output)?.collapsed ? 'Bloque largo de salida' : t.output}
                          </p>
                        ) : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Texto en Markdown en vivo */}
        {text && F.looksLikeHtmlDump(text) ? (
          <pre className="oc-mono max-h-72 overflow-auto rounded-xl border border-line bg-codebg p-3 text-[11px] text-muted whitespace-pre-wrap">
            {text}
          </pre>
        ) : text ? (
          <div className="oc-md text-sm leading-relaxed text-ink">
            <Markdown>{text}</Markdown>
          </div>
        ) : null}

        {/* Acciones por burbuja */}
        {text && !streaming && seg.final && (
          <div className="flex items-center gap-2 pt-1 text-muted">
            <button
              type="button"
              onClick={() => void copy()}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-panel hover:text-ink"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? 'Copiado' : 'Copiar'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
})

export default function ChatMessageList({
  mission,
  streaming,
  taskId,
  onRetry,
  canRetry,
  hideLogs,
}: {
  mission: MissionEvent[]
  streaming: boolean
  taskId: string | null
  onRetry: () => void
  canRetry: boolean
  hideLogs: boolean
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const _lockAt = useRef(0)

  const lastDone = useMemo(() => {
    for (let i = mission.length - 1; i >= 0; i--) {
      if (isDoneName(mission[i].name)) return mission[i]
    }
    return undefined
  }, [mission])

  const files = (lastDone?.data.files as unknown[] | undefined) ?? []

  const timeline = useMemo(() => buildTimeline(mission), [mission])
  const lastSegIdx = useMemo(() => {
    let n = -1
    timeline.forEach((it, i) => {
      if (it.kind === 'seg' && it.seg.type === 'agent') n = i
    })
    return n
  }, [timeline])

  // Auto-scroll estilo scroll-lock
  useEffect(() => {
    const box = boxRef.current
    if (!box || _lockAt.current > 60) return
    box.scrollTop = box.scrollHeight
  })

  return (
    <div
      ref={boxRef}
      className="scroll-lock flex-1 space-y-6 overflow-y-auto px-4 py-6"
      onScroll={(e) => {
        const el = e.currentTarget
        _lockAt.current = el.scrollHeight - el.scrollTop - el.clientHeight
      }}
    >
      {timeline.map((it, i) => {
        if (it.kind === 'user') {
          return (
            <div key={it.key} className="flex items-start justify-end gap-3">
              <div className="max-w-[85%] rounded-2xl bg-panel px-4 py-3 text-sm text-ink shadow-sm sm:max-w-2xl">
                <p className="whitespace-pre-wrap">{it.text}</p>
              </div>
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-accentink shadow-sm">
                <User className="h-4 w-4" />
              </div>
            </div>
          )
        }
        const seg = it.seg
        if (seg.type === 'system') {
          return <SystemBubble key={seg.key} text={seg.systemText ?? ''} />
        }
        return (
          <SegBubble
            key={seg.key}
            seg={seg}
            streaming={streaming}
            last={i === lastSegIdx}
            hideLogs={hideLogs}
          />
        )
      })}
      {streaming && lastSegIdx < 0 && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin" />
          Otter está pensando en GPU…
        </div>
      )}

      <PlanCard mission={mission} taskId={taskId} streaming={streaming} />

      {/* Tarjeta de Archivos Generados */}
      {files.length > 0 && (
        <div className="rounded-xl border border-line bg-panel p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink">
            <FileCode className="h-4 w-4" /> Archivos generados
          </p>
          <div className="space-y-1">
            {files.map((f, i) => {
              const path = F.filePathOf(f)
              if (!path) return null
              return (
              <div key={path || i} className="artifact-card flex items-center justify-between text-xs">
                <span className="oc-mono">{path}</span>
                {taskId && (
                  <button
                    type="button"
                    className="font-medium text-ink hover:underline"
                    onClick={() => F.studioFile(taskId, path)}
                  >
                    abrir →
                  </button>
                )}
              </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Tarjeta de error con Reintentar */}
      {lastDone?.name === 'task_error' && (
        <ErrorCard
          data={lastDone.data}
          canRetry={canRetry && !streaming}
          onRetry={onRetry}
        />
      )}

      {/* Descarga ZIP cuando terminó */}
      {taskId && lastDone && (
        <div className="flex items-center gap-2 pt-1 text-muted">
          <a
            href={api.zipUrl(taskId)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs hover:bg-panel hover:text-ink"
          >
            <Download className="h-3.5 w-3.5" /> Descargar ZIP
          </a>
        </div>
      )}
    </div>
  )
}