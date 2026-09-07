// Shell de la app estilo OtterCode V2: barra lateral fina (250px), cabecera
// superior, cadena de agentes, pestañas Chat/Agentes/Modelos/Skills, columna
// derecha de artefactos y widget flotante de hardware.

import { useEffect, useState } from 'react'
import {
  MessageSquare, Users, Box, Wrench, Plus, Search, Trash2,
  Bell, Menu, Activity, Focus, Bot, Settings, Zap, Hash, Sun, Moon,
} from 'lucide-react'
import Estado from './screens/Estado'
import Misiones from './screens/Misiones'
import Sesiones from './screens/Sesiones'
import Configuracion from './screens/Configuracion'
import Identidad from './screens/Identidad'
import Modelos from './screens/Modelos'
import Skills from './screens/Skills'
import OllamaConfig from './screens/OllamaConfig'
import Studio from './Studio'
import ArtifactsPanel from './ArtifactsPanel'
import CommandPalette from './CommandPalette'
import HwMonitor from './HwMonitor'
import { useUi, isDoneName } from './store'
import { api, type HistorySession } from './api'
import { convertTranscript, missionUnfinished } from './mission'
import { useMissionResume } from './useMissionResume'
import { AgentChain, ChainActions } from './PipelineStepper'

export const TOP_TABS = [
  { key: 'misiones', label: 'Chat', icon: MessageSquare },
  { key: 'identidad', label: 'Agentes', icon: Users },
  { key: 'modelos', label: 'Modelos', icon: Box },
  { key: 'skills', label: 'Skills', icon: Wrench },
  { key: 'ajustes', label: 'Ajustes', icon: Settings },
]

export const NAV = [
  ...TOP_TABS,
  { key: 'sesiones', label: 'Historial', icon: Bot },
  { key: 'estado', label: 'Monitor', icon: Activity },
  { key: 'config', label: 'Configuración', icon: Bot },
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
  const theme = useUi((s) => s.theme)
  const toggleTheme = useUi((s) => s.toggleTheme)

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
  }, [theme])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [palette, setPalette] = useState(false)
  const [hwOpen, setHwOpen] = useState(false)
  const [hideLogs, setHideLogs] = useState(false)
  const [history, setHistory] = useState<HistorySession[]>([])
  const [search, setSearch] = useState('')

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
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((v) => !v)
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

  const deletePastSession = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    try {
      await api.historyDelete(id)
      if (useUi.getState().taskId === id) clearMission()
      void loadHistory(search)
    } catch { /* noop */ }
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
      {/* Barra lateral fina (V2): buscador, reintentos, selector de modelo */}
      {sidebarOpen && (
        <div className="oc-focus-hide fixed inset-0 z-40 bg-black/40 backdrop-blur-xs md:hidden" onClick={() => setSidebarOpen(false)} />
      )}
      <aside className={`fixed inset-y-0 left-0 z-50 flex w-[250px] shrink-0 flex-col border-r border-line bg-panel transition-transform md:static md:z-30 ${sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}`}>
        {/* Cabecera */}
        <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-line/70 px-3">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-accentink">
              <Bot className="h-4 w-4" />
            </div>
            <span className="text-sm font-semibold tracking-tight">OtterCode</span>
            <span className="rounded-full bg-canvas px-1.5 py-0.5 text-[10px] text-muted">v2.6</span>
          </div>
          <button type="button" onClick={() => setSidebarOpen(false)} className="rounded-md p-1 text-muted hover:bg-canvas hover:text-ink md:hidden">
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
          {history.slice(0, 15).map((h) => (
            <div
              key={h.id}
              onClick={() => void loadPastSession(h.id)}
              className="group flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-xs text-muted hover:bg-canvas hover:text-ink"
            >
              <div className="flex items-center gap-2 truncate">
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{h.task}</span>
              </div>
              <button
                type="button"
                onClick={(e) => void deletePastSession(e, h.id)}
                className="hidden rounded p-0.5 hover:bg-panel hover:text-danger group-hover:block"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
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
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Cabecera superior */}
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line bg-surface px-4">
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setSidebarOpen(true)} className="rounded-lg p-1 text-muted hover:bg-panel hover:text-ink md:hidden">
              <Menu className="h-5 w-5" />
            </button>
            <h1 className="text-sm font-semibold tracking-tight">
              {TOP_TABS.find((t) => t.key === activeTab)?.label ?? NAV.find((n) => n.key === activeTab)?.label ?? 'OtterCode'}
            </h1>
          </div>
          <div className="flex items-center gap-1.5">
            <TokenStats />
            <button
              type="button"
              onClick={toggleTheme}
              className="rounded-lg p-1.5 text-muted hover:bg-panel hover:text-ink"
              title={theme === 'dark' ? 'Tema claro' : 'Tema oscuro'}
              aria-label="Invertir colores"
            >
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <button
              type="button"
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
                  on ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-panel2 hover:text-ink'
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
            {artifactsOpen && (
              <div className="hidden h-full w-[44%] shrink-0 md:block xl:w-[40%]">
                <ArtifactsPanel />
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {view === 'identidad' && <Identidad />}
            {view === 'modelos' && <Modelos />}
            {view === 'skills' && <Skills />}
            {view === 'ajustes' && <OllamaConfig />}
            {view === 'sesiones' && <Sesiones />}
            {view === 'estado' && <Estado />}
            {view === 'config' && <Configuracion />}
          </div>
        )}
      </div>

      {/* Overlays / Modales */}
      {studio && view !== 'misiones' && <Studio />}
      <HwMonitor open={hwOpen} onClose={() => setHwOpen(false)} />
      {!hwOpen && (
        <button
          type="button"
          onClick={() => setHwOpen(true)}
          className="fixed bottom-4 right-4 z-40 flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-surface text-muted shadow-lg transition-colors hover:text-ink"
          title="Mostrar monitor de hardware"
        >
          <Activity className="h-4 w-4" />
        </button>
      )}
      <CommandPalette open={palette} onClose={() => setPalette(false)} nav={NAV.map((n) => ({ key: n.key, label: n.label, icon: '›' }))} />
    </div>
  )
}

function TokenStats() {
  const totalTokens = useUi((s) => s.totalTokens)
  const tokensPerSec = useUi((s) => s.tokensPerSec)
  if (totalTokens <= 0) return null
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