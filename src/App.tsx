// Shell de la app estilo OtterCode V2: barra lateral fina (250px), cabecera
// superior, cadena de agentes, pestañas Chat/Agentes/Modelos/Skills, columna
// derecha de artefactos y widget flotante de hardware.

import { useEffect, useState } from 'react'
import {
  MessageSquare, Users, Box, Wrench, Plus, Search, Trash2,
  Bell, Menu, Activity, Focus, Bot, Settings, Zap, Hash, History,
} from 'lucide-react'
import Misiones from './screens/Misiones'
import Sesiones from './screens/Sesiones'
import Identidad from './screens/Identidad'
import Studio from './Studio'
import ArtifactsPanel from './ArtifactsPanel'
import TodoPanel from './TodoPanel'
import CommandPalette from './CommandPalette'
import HwMonitor from './HwMonitor'
import ConfirmDialog from './ConfirmDialog'
import UndoToast from './UndoToast'
import Ajustes from './screens/Ajustes'
import { useUi, isDoneName } from './store'
import { api, type HistorySession } from './api'
import { convertTranscript, missionUnfinished } from './mission'
import { useMissionResume } from './useMissionResume'
import { AgentChain, ChainActions } from './PipelineStepper'

export const TOP_TABS = [
  { key: 'misiones', label: 'Chat', icon: MessageSquare },
  { key: 'identidad', label: 'Agentes', icon: Users },
  { key: 'ajustes', label: 'Ajustes', icon: Settings },
]

export const NAV = [
  ...TOP_TABS,
  { key: 'modelos', label: 'Ajustes · Modelos', icon: Box },
  { key: 'skills', label: 'Ajustes · Skills', icon: Wrench },
  { key: 'config', label: 'Ajustes · Permisos', icon: Settings },
  { key: 'estado', label: 'Ajustes · Telemetría', icon: Activity },
]

function notifyDone(name: string, data: Record<string, unknown>) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  if (!document.hidden) return
  const body = name === 'task_done' ? 'Misión completada ✅' : name === 'task_error' ? `Error: ${String(data.message ?? '')}` : 'Misión abortada'
  try {
    // eslint-disable-next-line no-new
    new Notification('OtterCode', { body, icon: '/m/icon-192.png' })
  } catch { /* noop */ }
}

export default function App() {
  const view = useUi((s) => s.view)
  const setView = useUi((s) => s.setView)
  const focus = useUi((s) => s.focus)
  const toggleFocus = useUi((s) => s.toggleFocus)
  const studio = useUi((s) => s.studio)
  const closeStudio = useUi((s) => s.closeStudio)
  const openStudio = useUi((s) => s.openStudio)
  const clearMission = useUi((s) => s.clearMission)
  const artifactsOpen = useUi((s) => s.artifactsOpen)
  const setArtifactsOpen = useUi((s) => s.setArtifactsOpen)
  const taskId = useUi((s) => s.taskId)
  const mission = useUi((s) => s.mission)
  const streaming = useUi((s) => s.streaming)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [palette, setPalette] = useState(false)
  const [hwOpen, setHwOpen] = useState(false)
  const [hideLogs, setHideLogs] = useState(false)
  const [history, setHistory] = useState<HistorySession[]>([])
  const [search, setSearch] = useState('')
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  const [undo, setUndo] = useState<{ id: string; label: string } | null>(null)
  const undoTimer = useState<{ t?: number }>({})[0]
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editVal, setEditVal] = useState('')
  const notice = useUi((s) => s.notice)

  const loadHistory = async (q = '') => {
    try {
      const r = await api.history(q)
      setHistory(r.sessions)
    } catch { /* noop */ }
  }

  useEffect(() => {
    void loadHistory(search)
  }, [search, view])

  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => useUi.getState().setNotice(''), 4000)
    return () => window.clearTimeout(t)
  }, [notice])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((v) => !v)
      }
      if (e.key === 'Escape') {
        const st = useUi.getState()
        if (st.studio || st.artifactsOpen) {
          e.preventDefault()
          st.closeStudio()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (location.pathname.startsWith('/m') && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/m/sw.js').catch(() => undefined)
    }
  }, [])

  useMissionResume()
  useEffect(() => {
    if (mission.length > 0 && isDoneName(mission[mission.length - 1].name)) {
      notifyDone(mission[mission.length - 1].name, mission[mission.length - 1].data)
      void loadHistory(search)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mission.length, mission])

  const loadPastSession = async (id: string) => {
    const st = useUi.getState()
    const currentRunning = st.taskId === id && missionUnfinished(st.mission)
    let backendRunning = false
    try {
      const act = await api.activity()
      backendRunning = Boolean(act.running && act.task_id === id)
    } catch {
      /* noop */
    }
    if (currentRunning || backendRunning) {
      useUi.setState({ taskId: id, view: 'misiones' })
      return
    }
    try {
      const detail = await api.historyDetail(id)
      const evs = convertTranscript(detail)
      useUi.setState({ taskId: id, mission: evs, view: 'misiones' })
    } catch {
      /* noop */
    }
    setSidebarOpen(false)
  }

  const reallyDeleteSession = async (id: string) => {
    try {
      await api.historyDelete(id)
      if (useUi.getState().taskId === id) clearMission()
      void loadHistory(search)
    } catch { /* noop */ }
  }

  const scheduleDeleteSession = (id: string, label: string) => {
    setHistory((h) => h.filter((s) => s.id !== id))
    setUndo({ id, label })
    if (undoTimer.t) window.clearTimeout(undoTimer.t)
    undoTimer.t = window.setTimeout(() => {
      void reallyDeleteSession(id)
      setUndo(null)
    }, 8000)
  }

  const askNotify = () => {
    if ('Notification' in window) void Notification.requestPermission()
  }

  const newChat = () => {
    clearMission()
    setView('misiones')
    setSidebarOpen(false)
  }

  const toggleStudio = () => {
    if (view === 'misiones') {
      setArtifactsOpen(!artifactsOpen)
    } else if (studio) {
      closeStudio()
    } else {
      openStudio({ taskId: taskId ?? '', path: '' })
    }
  }

  const activeTab = view
  const showChain = view === 'misiones' && (mission.length > 0 || streaming)

  return (
    <div className={`focus-mode-root flex h-screen overflow-hidden bg-canvas text-ink ${focus ? 'focus-mode' : ''}`}>
      <a href="#oc-main" className="sr-only focus:not-sr-only focus:absolute focus:z-[70] focus:bg-panel focus:p-2">
        Saltar al chat
      </a>
      {/* Barra lateral fina (V2): buscador, reintentos, selector de modelo */}
      {sidebarOpen && (
        <div className="oc-focus-hide fixed inset-0 z-40 bg-black/40 backdrop-blur-xs md:hidden" onClick={() => setSidebarOpen(false)} />
      )}
      <aside
        aria-label="Barra lateral"
        className={`fixed inset-y-0 left-0 z-50 flex w-[250px] shrink-0 flex-col border-r border-line bg-panel transition-transform md:static md:z-30 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}
      >
        {/* Cabecera */}
        <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-line/70 px-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-accentink">
              <Bot className="h-4 w-4" />
            </div>
            <span className="text-sm font-semibold tracking-tight">OtterCode</span>
            <span className="rounded-full bg-canvas px-1.5 py-0.5 text-[10px] text-muted">v3.0.0</span>
          </div>
          <button type="button" aria-label="Cerrar menú" onClick={() => setSidebarOpen(false)} className="rounded-md p-1 text-muted hover:bg-canvas hover:text-ink md:hidden">
            <Menu className="h-4 w-4" />
          </button>
        </div>

        {/* Nuevo chat */}
        <div className="p-2.5">
          <button
            type="button"
            onClick={newChat}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-line bg-canvas py-2 text-xs font-semibold text-ink transition-colors hover:border-ink/25 hover:bg-panel"
          >
            <Plus className="h-4 w-4" />
            Nueva sesión
          </button>
        </div>

        {/* Selector de modelo (id="modelSel") */}
        <div className="px-2.5 pb-2.5">
          <ModelSelect />
        </div>

        {/* Buscador */}
        <div className="px-2.5">
          <div className="relative flex items-center">
            <Search className="absolute left-2.5 h-3.5 w-3.5 text-muted" />
            <input
              id="histSearch"
              aria-label="Buscar conversaciones"
              className="w-full rounded-lg border border-line bg-canvas py-1.5 pl-8 pr-2.5 text-xs text-ink focus:outline-none focus:border-ink/30"
              placeholder="Buscar chats…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        {/* Historial */}
        <div className="flex-1 overflow-y-auto px-2 py-2.5 space-y-0.5">
          <p className="px-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-muted">Recientes</p>
          {history.slice(0, 15).map((h) => {
            const titles = useUi.getState().sessionTitles
            const label = titles[h.id] || h.task
            const current = taskId === h.id
            return (
            <div
              key={h.id}
              className={`group flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-xs ${
                current ? 'bg-panel2 text-ink' : 'text-muted hover:bg-panel2 hover:text-ink'
              }`}
            >
              {editingId === h.id ? (
                <input
                  autoFocus
                  aria-label="Nuevo título"
                  className="min-w-0 flex-1 rounded border border-line bg-canvas px-1 py-0.5 text-xs text-ink"
                  value={editVal}
                  onChange={(e) => setEditVal(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      const name = editVal.trim()
                      setEditingId(null)
                      if (!name) return
                      useUi.setState((s) => ({ sessionTitles: { ...s.sessionTitles, [h.id]: name } }))
                      void api.historyRename(h.id, name).then(() => void loadHistory(search)).catch(() => undefined)
                    }
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                  onBlur={() => setEditingId(null)}
                />
              ) : (
              <button
                type="button"
                aria-current={current ? 'page' : undefined}
                onClick={() => void loadPastSession(h.id)}
                className="flex min-w-0 flex-1 items-center gap-2 truncate text-left"
                title="Doble clic para renombrar"
                onDoubleClick={(e) => {
                  e.preventDefault()
                  setEditingId(h.id)
                  setEditVal(label)
                }}
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{label}</span>
              </button>
              )}
              <button
                type="button"
                aria-label={`Eliminar conversación ${label}`}
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirmDel(h.id)
                }}
                className="rounded p-0.5 hover:bg-panel hover:text-danger"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
            )
          })}
        </div>

        {/* Footer */}
        <div className="border-t border-line/70 p-2.5">
          <button
            type="button"
            onClick={toggleFocus}
            className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-1.5 text-xs font-semibold transition-colors ${
              focus ? 'bg-accent text-accentink' : 'text-muted hover:bg-canvas hover:text-ink'
            }`}
          >
            <Focus className="h-4 w-4" />
            <span>Modo foco</span>
          </button>
          <p className="px-2.5 pt-1 text-[10px] text-muted">motor FastAPI · Ollama local</p>
        </div>
      </aside>

      {/* Área principal */}
      <div className="flex min-w-0 flex-1 flex-col" id="oc-main">
        {/* Cabecera superior */}
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line bg-surface px-4">
          <div className="flex items-center gap-2">
            <button type="button" aria-label="Abrir menú" onClick={() => setSidebarOpen(true)} className="rounded-lg p-1 text-muted hover:bg-panel hover:text-ink md:hidden">
              <Menu className="h-5 w-5" />
            </button>
            <h1 className="text-sm font-semibold tracking-tight">
              {TOP_TABS.find((t) => t.key === activeTab)?.label ?? NAV.find((n) => n.key === activeTab)?.label ?? 'OtterCode'}
            </h1>
          </div>
          <div className="flex items-center gap-1.5">
            <LiveModelBadge />
            <TokenStats />
            <button
              type="button"
              aria-label="Permitir notificaciones"
              onClick={askNotify}
              className="rounded-lg p-1.5 text-muted hover:bg-panel hover:text-ink"
              title="Permitir notificaciones"
            >
              <Bell className="h-4 w-4" />
            </button>
          </div>
        </header>

        {/* Pestañas superiores */}
        <nav className="flex h-10 shrink-0 items-center gap-1 border-b border-line bg-surface px-2">
          {TOP_TABS.map((t) => {
            const Icon = t.icon
            const on = view === t.key
            return (
              <button
                key={t.key}
                type="button"
                onClick={() => setView(t.key)}
                className={`inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-colors ${
                  on || (t.key === 'ajustes' && ['ajustes', 'modelos', 'skills', 'config', 'estado'].includes(view))
                    ? 'bg-accent/10 text-accent'
                    : 'text-muted hover:bg-panel2 hover:text-ink'
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {t.label}
              </button>
            )
          })}
        </nav>

        {/* Cadena de agentes + acciones */}
        {showChain && (
          <AgentChain
            mission={mission}
            actions={
              <ChainActions
                hideLogs={hideLogs}
                onToggleLogs={() => setHideLogs((v) => !v)}
                studioOpen={view === 'misiones' ? artifactsOpen : Boolean(studio)}
                onToggleStudio={toggleStudio}
                onCompact={() => {
                  const st = useUi.getState()
                  const id = st.taskId
                  if (!id) {
                    st.setNotice('No hay misión activa para compactar')
                    return
                  }
                  void api.compactNow(id).then((r) => {
                    st.setNotice(r.ok ? (r.still_over ? 'Compactado, el contexto sigue alto' : 'Contexto compactado') : 'No se pudo compactar')
                  }).catch((e: unknown) => st.setNotice((e as Error).message))
                }}
              />
            }
          />
        )}

        {/* Cuerpo */}
        {view === 'misiones' ? (
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <div className="flex min-w-0 flex-1 flex-col">
              <Misiones hideLogs={hideLogs} />
            </div>
            <TodoPanel />
            {artifactsOpen && (
              <div className="hidden h-full w-[44%] shrink-0 md:block xl:w-[40%]">
                <ArtifactsPanel />
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {view === 'identidad' && <Identidad />}
            {(view === 'ajustes' || view === 'modelos' || view === 'skills' || view === 'config' || view === 'estado') && <Ajustes />}
            {view === 'sesiones' && <Sesiones />}
          </div>
        )}
      </div>

      {/* Overlays / Modales */}
      {studio && view !== 'misiones' && (
        <div className="fixed inset-0 z-[80]">
          <Studio />
        </div>
      )}
      <HwMonitor open={hwOpen} onClose={() => setHwOpen(false)} />
      {!hwOpen && (
        <button
          type="button"
          aria-label="Mostrar monitor de hardware"
          onClick={() => setHwOpen(true)}
          className="fixed bottom-4 right-4 z-40 flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-surface text-muted shadow-lg transition-colors hover:text-ink"
          title="Mostrar monitor de hardware"
        >
          <Activity className="h-4 w-4" />
        </button>
      )}
      <CommandPalette open={palette} onClose={() => setPalette(false)} nav={NAV.map((n) => ({ key: n.key, label: n.label, icon: '›' }))} />
      <ConfirmDialog
        open={Boolean(confirmDel)}
        itemLabel={history.find((h) => h.id === confirmDel)?.task || 'esta conversación'}
        onCancel={() => setConfirmDel(null)}
        onConfirm={() => {
          const id = confirmDel
          const label = history.find((h) => h.id === id)?.task || 'conversación'
          setConfirmDel(null)
          if (id) scheduleDeleteSession(id, label)
        }}
      />
      {undo && (
        <UndoToast
          label={undo.label}
          onUndo={() => {
            if (undoTimer.t) window.clearTimeout(undoTimer.t)
            setUndo(null)
            void loadHistory(search)
          }}
        />
      )}
      {notice && (
        <div className="fixed bottom-16 left-1/2 z-[55] -translate-x-1/2 rounded-xl border border-line bg-panel px-3 py-2 text-xs text-ink shadow-lg" role="status">
          {notice}
        </div>
      )}
    </div>
  )
}

function TokenStats() {
  const totalTokens = useUi((s) => s.totalTokens)
  const tokensPerSec = useUi((s) => s.tokensPerSec)
  return (
    <span
      id="tokenStats"
      className="hidden select-none items-center gap-2 rounded-full border border-line bg-panel2 px-2.5 py-1 text-[10px] font-medium text-muted sm:flex"
      title="Tokens generados por el modelo en esta conversación"
    >
      <span className="inline-flex items-center gap-1">
        <Zap className="h-3 w-3 text-accent" />
        {tokensPerSec > 0 ? `${tokensPerSec.toFixed(1)} tok/s` : '— tok/s'}
      </span>
      <span className="inline-flex items-center gap-1">
        <Hash className="h-3 w-3 text-accent" />
        {totalTokens.toLocaleString('es')} tokens
      </span>
    </span>
  )
}

function ModelSelect() {
  const model = useUi((s) => s.model)
  const setModel = useUi((s) => s.setModel)
  const [models, setModels] = useState<string[]>([])

  useEffect(() => {
    api
      .models()
      .then((r) => {
        if (r.models.length === 0) return
        setModels(r.models)
        // Si el modelo persistido ya no existe en el backend, elegir uno
        // válido en su lugar. Sin esto, lanzar sin cambiar el selector envía
        // un modelo inexistente y el backend responde task_error → la web se
        // queda "colgada" sin terminar.
        const current = useUi.getState().model
        if (!r.models.includes(current)) {
          setModel(r.models[0])
        }
      })
      .catch(() => undefined)
  }, [setModel])

  return (
    <div className="flex items-center rounded-lg border border-line bg-canvas px-2.5 py-1.5 text-xs">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
      <select
        id="modelSel"
        className="ml-2 w-full cursor-pointer bg-transparent font-medium text-ink focus:outline-none"
        value={model}
        onChange={(e) => setModel(e.target.value)}
      >
        {models.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
        {models.length === 0 && <option value={model}>{model}</option>}
      </select>
    </div>
  )
}

function LiveModelBadge() {
  const model = useUi((s) => s.model)
  const live = useUi((s) => s.liveModel)
  const agent = useUi((s) => s.liveAgent)
  const streaming = useUi((s) => s.streaming)
  const shown = live || model
  return (
    <span
      className="hidden max-w-[220px] truncate rounded-full border border-line bg-panel2 px-2 py-0.5 text-[10px] font-medium text-muted sm:inline"
      title="Modelo activo"
    >
      {streaming ? 'cargando · ' : ''}
      {agent ? `${agent} · ` : ''}
      {shown}
    </span>
  )
}