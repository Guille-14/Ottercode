// Zustand: estado global ligero para el streaming de misiones, navegación y tema.

import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { api, fetchWithAuth } from './api'
import { parseSse } from './sse'
import type { StudioTarget } from './features'



export interface QueuedItem {
  id: string
  text: string
  payload: Record<string, unknown>
}

export interface MissionEvent {
  id: number
  name: string
  data: Record<string, unknown>
  at: number
}

interface UiState {
  view: string
  taskId: string | null
  mission: MissionEvent[]
  streaming: boolean
  missionQueue: QueuedItem[]
  missionError: string | null
  studio: StudioTarget | null
  pendingPerm: { id: string; tool: string; title: string } | null
  ctxHint: {
    compact_hits: number
    current_ctx?: number
    next_ctx?: number
    current_tps?: number
    next_tps?: number
    has_bench?: boolean
    recommended?: number
  } | null
  focus: boolean
  composerDraft: string
  model: string
  artifactsOpen: boolean
  artifactsUserClosed: boolean
  loopMode: boolean
  maxRounds: number
  hacker: boolean
  yolo: boolean
  agentMode: 'chat' | 'chain'
  startAgent: string
  totalTokens: number
  tokensPerSec: number
  liveModel: string
  liveAgent: string
  missionStartedAt: number | null
  sessionTitles: Record<string, string>
  settingsSection: string
  notice: string
  fileTick: { path: string; version: number } | null
  setView: (v: string) => void
  setSettingsSection: (s: string) => void
  setNotice: (s: string) => void

  setModel: (m: string) => void
  setArtifactsOpen: (open: boolean) => void
  setLoopMode: (v: boolean) => void
  setMaxRounds: (n: number) => void
  setHacker: (v: boolean) => void
  setYolo: (v: boolean) => void
  setAgentMode: (m: 'chat' | 'chain') => void
  setStartAgent: (id: string) => void
  enqueueMission: (text: string, payload: Record<string, unknown>) => void
  dequeueMission: (id: string) => void
  startMission: (payload: Record<string, unknown>) => Promise<void>
  stopMission: (abort: boolean) => void
  clearMission: () => void
  openStudio: (t: StudioTarget) => void
  closeStudio: () => void
  toggleFocus: () => void
  setComposerDraft: (d: string) => void
  clearComposerDraft: () => void
  approvePerm: (id: string, allow: boolean, always?: boolean) => Promise<void>
  dismissCtxHint: () => void
  applyCtxHint: () => Promise<void>
}

let seq = 0
let controller: AbortController | null = null

// Ventana deslizante para el cálculo de tokens/segundo en tiempo real (fuentas
// de tiempo de cada token emitido por el modelo). No se persiste.
let tokTimes: number[] = []
const RATE_WINDOW_MS = 3000

// Marca el tiempo de llegada de un token (barato). El total/tps reales se
// calculan al COMMITTEAR el lote, no por token (ver startMission).
function pushTokenTime(): void {
  const now = Date.now()
  tokTimes.push(now)
  while (tokTimes.length && now - tokTimes[0] > RATE_WINDOW_MS) tokTimes.shift()
}

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
      view: 'misiones',
      taskId: null,
      mission: [],
      streaming: false,
      missionQueue: [],
      missionError: null,
      studio: null,
      pendingPerm: null,
      ctxHint: null,
      focus: false,
      composerDraft: '',
      model: 'qwen3.5:4b',
      artifactsOpen: false,
      artifactsUserClosed: false,
      loopMode: false,
      maxRounds: 8,
      hacker: false,
      yolo: false,
      agentMode: 'chat',
      startAgent: 'agent',
      totalTokens: 0,
      tokensPerSec: 0,
      liveModel: '',
      liveAgent: '',
      missionStartedAt: null,
      sessionTitles: {},
      settingsSection: 'parametros',
      notice: '',
      fileTick: null,
      setSettingsSection: (s) => set({ settingsSection: s }),
      setNotice: (s) => set({ notice: s }),
      setView: (v) => {
        const map: Record<string, string> = {
          modelos: 'modelos',
          skills: 'skills',
          config: 'permisos',
          estado: 'telemetria',
        }
        if (map[v]) set({ view: 'ajustes', settingsSection: map[v] })
        else set({ view: v })
      },
      clearMission: () => {
        tokTimes = []
        set({
          mission: [],
          taskId: null,
          missionQueue: [],
          missionError: null,
          totalTokens: 0,
          tokensPerSec: 0,
          liveModel: '',
          liveAgent: '',
          missionStartedAt: null,
        })
      },
      enqueueMission: (text, payload) => {
        const item: QueuedItem = {
          id: Math.random().toString(36).slice(2, 9),
          text,
          payload,
        }
        set((s) => ({ missionQueue: [...s.missionQueue, item] }))
      },
      dequeueMission: (id) => {
        set((s) => ({ missionQueue: s.missionQueue.filter((q) => q.id !== id) }))
      },

      setModel: (m) => set({ model: m }),
      setArtifactsOpen: (open) => set({ artifactsOpen: open, artifactsUserClosed: !open }),
      setLoopMode: (v) => set({ loopMode: v }),
      setMaxRounds: (n) => set({ maxRounds: n }),
      setHacker: (v) => set({ hacker: v }),
      setYolo: (v) => set({ yolo: v }),
      setAgentMode: (m) => set({
        agentMode: m,
        startAgent: m === 'chain' ? 'architect' : (get().startAgent === 'architect' ? 'agent' : get().startAgent || 'agent'),
      }),
      setStartAgent: (id) => set({ startAgent: id }),
      openStudio: (t) => {
        const path = typeof t.path === 'string' ? t.path.trim() : ''
        if (!path || path === '[object Object]') return
        set({ studio: { taskId: t.taskId, path }, artifactsOpen: true, artifactsUserClosed: false })
      },
      closeStudio: () => set({ studio: null, artifactsOpen: false, artifactsUserClosed: true }),
      toggleFocus: () => set((s) => ({ focus: !s.focus })),
      setComposerDraft: (d) => set({ composerDraft: d }),
      clearComposerDraft: () => set({ composerDraft: '' }),
      dismissCtxHint: () => set({ ctxHint: null }),
      applyCtxHint: async () => {
        const h = get().ctxHint
        const nxt = Number(h?.next_ctx || h?.recommended || 0)
        set({ ctxHint: null })
        if (nxt >= 2048) {
          await api.applyCtx(nxt).catch(() => undefined)
        }
      },
      approvePerm: async (id, allow, always) => {
        set({ pendingPerm: null })
        await fetchWithAuth('/api/approve', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, allow, always: Boolean(always) }),
        })
      },
      stopMission: (abort: boolean) => {
        if (controller) {
          if (abort && get().taskId) api.abort(get().taskId!).catch(() => undefined)
          controller.abort()
        }
        set({ streaming: false })
      },
      startMission: async (payload) => {
        const wasStreaming = get().streaming
        get().stopMission(wasStreaming)
        controller = new AbortController()
        const missionAbort = controller
        const contTask =
          typeof payload.continue_task === 'string' && payload.continue_task
            ? payload.continue_task
            : get().taskId
        const keepOngoing = Boolean(contTask) && get().mission.length > 0
        const taskText = String(payload.task ?? '').trim()
        const userEv: MissionEvent = {
          id: ++seq,
          at: Date.now(),
          name: 'user',
          data: { text: taskText },
        }
        // Instantáneo: no esperamos historyDetail (eso congelaba la UI).
        // Conservamos burbujas y añadimos el mensaje del usuario ya.
        if (keepOngoing) {
          set({
            taskId: contTask,
            mission: [...get().mission.filter((e) => !isDoneName(e.name)), userEv],
            streaming: true,
            missionError: null,
            missionStartedAt: Date.now(),
          })
        } else {
          set({
            mission: taskText ? [userEv] : [],
            taskId: null,
            streaming: true,
            missionError: null,
            missionStartedAt: Date.now(),
            totalTokens: 0,
            tokensPerSec: 0,
          })
        }
        const st = get()
        const body = {
          ...payload,
          mode: payload.mode ?? st.agentMode ?? 'chat',
          start_agent: payload.start_agent ?? st.startAgent ?? (st.agentMode === 'chain' ? 'architect' : 'agent'),
        }
        let res: Response
        try {
          res = await fetchWithAuth(
            '/api/task',
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
              signal: missionAbort.signal,
            },
            0,
          )
        } catch (e) {
          set({ streaming: false, missionError: (e as Error).message || 'No se pudo conectar con el backend' })
          return
        }
        if (!res.ok || !res.body) {
          let msg = `HTTP ${res.status}`
          try {
            const b = await res.json()
            msg = b.detail ?? msg
          } catch {
            /* noop */
          }
          set({ streaming: false, missionError: msg })
          return
        }
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        // 📦 Lote de eventos SSE pendiente: los frames de `token` llegan a
        // decenas por segundo; commitear cada uno por separado dispara una
        // tormenta de re-renders y congela la pestaña con misiones largas.
        // Aquí se acumulan y se vuelcan al store 1x cada FLUSH_MS con un único
        // set() (mission + contadores juntos).
        const pending: ReturnType<typeof parseSse> = []
        let tokAcc = 0
        const enqueue = (parsed: ReturnType<typeof parseSse>) => {
          for (const ev of parsed) {
            if (ev.name === 'session_id') {
              const tid = ev.data['task_id'] as string | undefined
              if (tid) set({ taskId: tid })
              const raw = String(payload.task ?? '')
              if (tid && raw && !keepOngoing) {
                const title = raw.replace(/\s+/g, ' ').trim().slice(0, 48)
                set((s) => ({ sessionTitles: { ...s.sessionTitles, [tid]: title } }))
                void api.historyRename(tid, title).catch(() => undefined)
              }
              continue
            }
            if (ev.name === 'perm_request') {
              set({ pendingPerm: ev.data as any })
              continue
            }
            if (ev.name === 'ctx_hint') {
              set({ ctxHint: ev.data as UiState['ctxHint'] })
            }
            if (ev.name === 'file_updated') {
              const p = String(ev.data.path || ev.data.filepath || '')
              const v = Number(ev.data.version || Date.now())
              if (p) set({ fileTick: { path: p, version: v } })
            }
            if (ev.name === 'token') {
              pushTokenTime()
              tokAcc++
            }
            if (ev.name === 'agent_start') {
              set({
                liveAgent: String(ev.data.nombre ?? ev.data.agent ?? ''),
                liveModel: String(ev.data.model ?? get().model),
              })
            }
            if (ev.name === 'task_start') {
              set({
                missionStartedAt: Date.now(),
                liveModel: String(ev.data.model ?? get().model),
                liveAgent: String(ev.data.start_agent ?? get().startAgent),
              })
              const tid = get().taskId
              const task = String(ev.data.task ?? '')
              if (tid && task && !get().sessionTitles[tid]) {
                const title = task.replace(/\s+/g, ' ').trim().slice(0, 48)
                set((s) => ({ sessionTitles: { ...s.sessionTitles, [tid]: title } }))
              }
            }
            if (ev.name === 'task_error') {
              const d = String(ev.data.detail || ev.data.message || 'Error en la misión')
              set({ missionError: d })
            }
            pending.push(ev)
          }
        }
        const commit = () => {
          if (pending.length === 0 && tokAcc === 0) return
          const evs = pending.splice(0, pending.length)
          const n = tokAcc
          tokAcc = 0
          set((s) => ({
            mission: [...s.mission, ...evs.map((e) => ({ ...e, id: ++seq, at: Date.now() }))],
            totalTokens: s.totalTokens + n,
            tokensPerSec: Math.round((tokTimes.length / RATE_WINDOW_MS) * 1000),
          }))
        }
        let rafId = 0
        const rafLoop = () => {
          commit()
          if (get().streaming) rafId = requestAnimationFrame(rafLoop)
        }
        rafId = requestAnimationFrame(rafLoop)
        try {
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            let idx: number
            while ((idx = buffer.indexOf('\n\n')) !== -1) {
              const frame = buffer.slice(0, idx)
              buffer = buffer.slice(idx + 2)
              enqueue(parseSse(frame))
            }
          }
        } catch {
          /* aborted */
        } finally {
          cancelAnimationFrame(rafId)
          commit()
          controller = null
          set({ streaming: false })

          // 📥 Procesar siguiente mensaje de la cola de espera si existe
          const queue = get().missionQueue
          if (queue.length > 0) {
            const next = queue[0]
            set({ missionQueue: queue.slice(1) })
            setTimeout(() => {
              void get().startMission({
                ...next.payload,
                continue_task: get().taskId || undefined,
              })
            }, 100)
          }
        }
      },
    }),
    {
      name: 'otter-storage',
        version: 4,
        migrate: (persisted, version) => {
          const p = (persisted || {}) as Record<string, unknown>
          if (!p.agentMode) p.agentMode = 'chat'
          if (!p.startAgent) p.startAgent = p.agentMode === 'chain' ? 'architect' : 'agent'
          if (!p.sessionTitles) p.sessionTitles = {}
          if (version < 4) p.artifactsOpen = false
          return p as typeof persisted
        },
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        model: state.model,
        artifactsOpen: false,
        loopMode: state.loopMode,
        maxRounds: state.maxRounds,
        hacker: state.hacker,
        yolo: state.yolo,
        agentMode: state.agentMode,
        startAgent: state.startAgent,
        sessionTitles: state.sessionTitles,
      }),
    },
  ),
)

export const isDoneName = (n: string) =>
  n === 'task_done' || n === 'task_aborted' || n === 'task_error'