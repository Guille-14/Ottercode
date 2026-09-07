// Barra de entrada flotante estilo OpenWebUI con autocompletado slash,
// selector de modo (Agente único / Cadena), loop, hacker, barra de objetivo
// y botón de envío/aborto.

import { useEffect, useRef, useState } from 'react'
import { ArrowUp, Square, Sliders, Paperclip, RotateCcw, Skull, Clock, X } from 'lucide-react'
import { useUi } from '../store'
import { api, type AgentInfo } from '../api'
import { F, SLASH_COMMANDS } from '../features'
import SlashPopup from '../SlashPopup'

const DEFAULT_AGENTS = ['architect', 'researcher', 'developer', 'reviewer']

export default function ChatInputBar({
  onLaunch,
  streaming,
  onStop,
}: {
  onLaunch: (payload: Record<string, unknown>) => void
  streaming: boolean
  onStop: () => void
}) {
  const composerDraft = useUi((s) => s.composerDraft)
  const clearComposerDraft = useUi((s) => s.clearComposerDraft)
  const loopMode = useUi((s) => s.loopMode)
  const setLoopMode = useUi((s) => s.setLoopMode)
  const maxRounds = useUi((s) => s.maxRounds)
  const setMaxRounds = useUi((s) => s.setMaxRounds)
  const hacker = useUi((s) => s.hacker)
  const setHacker = useUi((s) => s.setHacker)
  const missionQueue = useUi((s) => s.missionQueue)
  const enqueueMission = useUi((s) => s.enqueueMission)
  const dequeueMission = useUi((s) => s.dequeueMission)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [task, setTask] = useState('')
  const [mode, setMode] = useState<'chat' | 'chain'>('chat')
  const [startAgent, setStartAgent] = useState('agent')
  const [goal, setGoal] = useState('')
  const [planOnly, setPlanOnly] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [sel, setSel] = useState(0)
  const [agentsList, setAgentsList] = useState<AgentInfo[]>([])

  const changeMode = (m: 'chat' | 'chain') => {
    setMode(m)
    // D · En cadena, el Arquitecto empieza planeando y delega; en agente único,
    // el especializado en todo (Otter).
    if (m === 'chain') setStartAgent('architect')
    else setStartAgent('agent')
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = (event) => {
      const content = event.target?.result as string
      const fileBlock = `\n[Archivo: ${file.name}]\n\`\`\`\n${content}\n\`\`\`\n`
      setTask((prev) => prev + fileBlock)
    }
    reader.readAsText(file)
  }

  useEffect(() => {
    api.agents()
      .then((r) => {
        if (r.agents && r.agents.length > 0) {
          setAgentsList(r.agents)
        }
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (composerDraft && !streaming) {
      setTask(composerDraft)
      clearComposerDraft()
    }
  }, [composerDraft, streaming, clearComposerDraft])

  const word = task.startsWith('/') ? task.split(' ')[0] ?? '' : ''
  const filtered = word.length > 1
    ? SLASH_COMMANDS.filter((c) => c.cmd.slice(1).startsWith(word.slice(1)))
    : SLASH_COMMANDS
  const popupItems = filtered.slice(0, 8)
  const popupOpen = task.startsWith('/') && !streaming

  const pickCommand = (cmd: string) => setTask(`${cmd} `)

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const n = Math.max(1, popupItems.length)
    if (popupOpen) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setSel((s) => (e.key === 'ArrowUp' ? s - 1 + n : s + 1) % n)
        return
      }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
        e.preventDefault()
        pickCommand(popupItems[sel]?.cmd ?? '')
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setTask('')
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
    }
  }

  const handleSend = async () => {
    const trimmed = task.trim()
    if (!trimmed) return

    const parsed = trimmed.startsWith('/') ? F.parseInput(trimmed) : { fields: { task: trimmed } }

    // Si el modelo está ocupado pensando o escribiendo, añadir a la cola
    if (streaming) {
      const payload = {
        ...parsed.fields,
        mode,
        goal,
        plan_only: planOnly || Boolean(parsed.fields.plan_only),
        loop_mode: loopMode,
        max_rounds: loopMode ? maxRounds : undefined,
        hacker,
      }
      enqueueMission(trimmed, payload)
      setTask('')
      return
    }

    // Ejecución de comandos del sistema
    if (parsed.action === '/reset') {
      useUi.getState().clearMission()
      setTask('')
      return
    }
    const modelField = parsed.fields.model
    if (typeof modelField === 'string' && modelField.trim()) {
      useUi.getState().setModel(modelField.trim())
      setTask('')
      return
    }

    const effGoal = typeof parsed.fields.goal === 'string' ? parsed.fields.goal : goal.trim()
    const effMode =
      typeof parsed.fields.mode === 'string' && (parsed.fields.mode === 'chat' || parsed.fields.mode === 'chain')
        ? parsed.fields.mode
        : mode
    if (effMode !== mode) setMode(effMode)
    if (effGoal !== goal.trim()) setGoal(effGoal)

    // Respetar el start_agent del comando (/agents, /yolo…) o usar el selector.
    const fields: Record<string, unknown> = { ...parsed.fields }
    if (!fields.start_agent) fields.start_agent = startAgent

    onLaunch({
      ...fields,
      mode: effMode,
      goal: effGoal,
      plan_only: planOnly || Boolean(parsed.fields.plan_only),
      loop_mode: loopMode,
      max_rounds: loopMode ? maxRounds : undefined,
      hacker,
    })
    setTask('')
  }

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-3">
      {/* Contenedor flotante estilo OpenWebUI */}
      <div className="relative rounded-2xl border border-line bg-surface/90 p-3 shadow-sm backdrop-blur transition-all focus-within:border-accent/40 focus-within:shadow-md">
        {/* Autocomplete de slash commands */}
        {popupOpen && (
          <SlashPopup word={word} selected={sel} onPick={pickCommand} />
        )}

        {/* Lista de mensajes en cola mientras el modelo está ocupado */}
        {missionQueue.length > 0 && (
          <div className="mb-2.5 space-y-1.5 border-b border-line pb-2.5">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-accent">
              <Clock className="h-3.5 w-3.5 animate-spin" />
              <span>Mensajes en cola ({missionQueue.length}):</span>
            </div>
            <div className="space-y-1 max-h-24 overflow-y-auto">
              {missionQueue.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-line bg-panel px-2.5 py-1 text-xs text-ink"
                >
                  <span className="truncate font-medium text-muted">{item.text}</span>
                  <button
                    type="button"
                    onClick={() => dequeueMission(item.id)}
                    className="rounded p-0.5 text-muted hover:text-danger hover:bg-canvas transition-colors"
                    title="Quitar de la cola"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}


        {/* Barra superior de ajustes rápidos */}
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          {/* Selector de modo explícito */}
          <select
            id="modeSel2"
            aria-label="Modo de agente"
            value={mode}
            onChange={(e) => changeMode(e.target.value as 'chat' | 'chain')}
            className="cursor-pointer rounded-lg border border-line bg-panel2 px-2 py-1 text-[10px] font-semibold text-ink focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            <option value="chat">🧍 Agente único</option>
            <option value="chain">🔗 Cadena de agentes</option>
          </select>
          <button
            id="loopBtn"
            type="button"
            onClick={() => setLoopMode(!loopMode)}
            className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[10px] font-semibold border transition-colors ${
              loopMode ? 'bg-accent text-accentink border-accent' : 'bg-panel2 text-ink2 border-line2 hover:text-ink'
            }`}
            title="Bucle programador↔revisor hasta aprobar (o límite)"
          >
            <RotateCcw className="h-3 w-3" />
            Loop
          </button>
          {loopMode && (
            <input
              id="maxRoundsBar"
              type="number"
              min={1}
              max={25}
              value={maxRounds}
              onChange={(e) => setMaxRounds(Math.max(1, Math.min(25, Number(e.target.value) || 1)))}
              className="w-14 rounded-lg border border-line bg-canvas px-1.5 py-1 text-center text-[10px] font-medium text-ink focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
              title="Rondas máximas del bucle"
            />
          )}
          <button
            id="hackerBtn"
            type="button"
            onClick={() => setHacker(!hacker)}
            className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[10px] font-semibold border transition-colors ${
              hacker ? 'bg-danger text-white border-danger' : 'bg-panel2 text-ink2 border-line2 hover:text-ink'
            }`}
            title="Modo hacker: LLM sin censura de contenido (la protección del sistema sigue intacta)"
          >
            <Skull className="h-3 w-3" />
            Hacker
          </button>
          <button
            id="planBtn"
            type="button"
            onClick={() => setPlanOnly(!planOnly)}
            className={`px-3 py-1 rounded-full text-[10px] font-semibold border transition-colors ${
              planOnly ? 'bg-accent text-accentink border-accent' : 'bg-panel2 text-ink2 border-line2 hover:text-ink'
            }`}
          >
            Solo plan
          </button>
          <button
            type="button"
            onClick={() => setShowSettings(!showSettings)}
            className="flex items-center gap-1 rounded-full px-3 py-1 text-muted hover:bg-panel2"
          >
            <Sliders className="h-3 w-3" />
            <span>Avanzado</span>
          </button>
        </div>

        {/* Panel expandible de opciones avanzadas */}
        {showSettings && (
          <div className="mb-2 grid gap-2 rounded-xl border border-line bg-panel2 p-3 text-xs sm:grid-cols-2">
            {hacker && (
              <div className="sm:col-span-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[11px] text-danger">
                Modo hacker activo: el LLM responde sin censura de contenido. La
                protección del sistema (denylist de bash, guard SSRF) sigue intacta.
              </div>
            )}
            <div>
              <span className="mb-1 block font-medium text-muted">Objetivo (/goal)</span>
              <input
                id="goalBar"
                className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="Meta global…"
              />
            </div>
            <div>
              <span className="mb-1 block font-medium text-muted">
                {mode === 'chat' ? 'Agente (único)' : 'Agente inicial'}
              </span>
              <select
                className="w-full rounded-lg border border-line bg-canvas px-2.5 py-1.5 focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
                value={startAgent}
                onChange={(e) => setStartAgent(e.target.value)}
              >
                {agentsList.length > 0
                  ? agentsList.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.icon} {a.nombre}
                      </option>
                    ))
                  : DEFAULT_AGENTS.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
              </select>
            </div>
            <div className="sm:col-span-2 text-muted">
              {mode === 'chat' ? (
                <p>🧍 Agente único (Otter): el especializado en todo. Usa sus skills directamente sobre el workspace.</p>
              ) : (
                <p>🔗 Cadena de agentes: el Arquitecto piensa qué hay que hacer, planifica y delega en Investigador, especialistas, Programador y Revisor.</p>
              )}
            </div>
          </div>
        )}

        {/* Textarea auto-ajustable con Enter para enviar */}
        <div className="flex items-end gap-2">
          <button type="button" onClick={() => fileInputRef.current?.click()} className="mb-2 text-muted hover:text-ink">
            <Paperclip className="h-5 w-5" />
          </button>
          <input
            type="file"
            ref={fileInputRef}
            className="hidden"
            onChange={handleFileChange}
            accept=".py,.js,.json,.md,.rs,.txt,.css,.html"
          />
          <textarea
            className="max-h-48 min-h-[52px] w-full resize-none bg-transparent px-1 py-1 text-sm text-ink placeholder:text-muted focus:outline-none focus-visible:outline-none"
            rows={2}
            value={task}
            onChange={(e) => {
              setTask(e.target.value)
              setSel(0)
            }}
            onKeyDown={onKeyDown}
            placeholder="Pregunta o describe la tarea… (escribe / para comandos)"
          />

          {/* Botón de Enviar (Flecha), Añadir a cola (+) o Abortar (Cuadrado rojo) */}
          <div className="flex items-center gap-1">
            {streaming && (
              <button
                type="button"
                onClick={onStop}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-danger/30 bg-danger/10 text-danger transition-all hover:bg-danger hover:text-white"
                title="Detener generación actual"
              >
                <Square className="h-4 w-4 fill-current" />
              </button>
            )}
            <button
              type="button"
              onClick={handleSend}
              disabled={!task.trim()}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-accentink shadow-sm transition-all hover:opacity-90 disabled:opacity-30"
              title={streaming ? 'Añadir mensaje a la cola' : 'Enviar mensaje'}
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      <p className="mt-1.5 text-center text-[11px] text-muted">
        OtterCode con agentes locales · Los modelos pueden cometer errores · Verifica el código generado.
      </p>
    </div>
  )
}
