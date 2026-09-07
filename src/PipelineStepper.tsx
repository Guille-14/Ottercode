// Barra de cadena de agentes estilo V2 (OtterCode v2): nodos Arquitecto →
// Investigador → Programador → Revisor con dot indicador (done/active/wait) y
// acciones (Logs, Studio). Se alimenta de los eventos de la misión.
import type { ReactNode } from 'react'
import { Check, Loader2, MessagesSquare, MonitorSmartphone } from 'lucide-react'

export interface ChainNode {
  id: string
  label: string
}

export const DEFAULT_CHAIN: ChainNode[] = [
  { id: 'architect', label: 'Arquitecto' },
  { id: 'researcher', label: 'Investigador' },
  { id: 'developer', label: 'Programador' },
  { id: 'reviewer', label: 'Revisor' },
]

export function deriveChainState(mission: any[]) {
  const active = new Set(mission.filter((e) => e.name === 'agent_start').map((e) => e.data?.agent))
  const done = new Set(mission.filter((e) => e.name === 'agent_end').map((e) => e.data?.agent))
  const last = mission[mission.length - 1]
  const current = last?.name === 'agent_start' ? last.data?.agent : null
  return { active, done, current }
}

export function AgentChain({
  mission,
  actions,
}: {
  mission: any[]
  actions?: ReactNode
}) {
  const { active, current } = deriveChainState(mission)

  // Modo agente único (Claude Code): una sola burbuja Otter.
  const single = active.has('agent')
  const nodes: ChainNode[] = single ? [{ id: 'agent', label: 'Otter' }] : DEFAULT_CHAIN

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-x-auto">
        <span className="shrink-0 text-[11px] font-medium text-muted">Cadena</span>
        {nodes.map((n, i) => {
          const isCurrent = current === n.id
          const hasRun = active.has(n.id)
          return (
            <div key={n.id} className="flex shrink-0 items-center gap-2">
              <div
                className={`flex items-center gap-1.5 text-xs whitespace-nowrap transition-colors ${
                  isCurrent
                    ? 'text-ink font-medium'
                    : hasRun
                      ? 'text-ink2'
                      : 'text-muted'
                }`}
              >
                <span
                  className={`h-2 w-2 rounded-full ${
                    isCurrent
                      ? 'bg-accent shadow-[0_0_8px_var(--oc-accent)]'
                      : hasRun
                        ? 'bg-accent/70'
                        : 'bg-line2'
                  }`}
                />
                {n.label}
                {isCurrent && <Loader2 className="h-3 w-3 animate-spin text-accent" />}
              </div>
              {i < nodes.length - 1 && <span className="text-[11px] text-line2">→</span>}
            </div>
          )
        })}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

// Botones estándar de acciones de la cadena (Logs / Studio), estilo V2.
export function ChainActions({
  hideLogs,
  onToggleLogs,
  studioOpen,
  onToggleStudio,
}: {
  hideLogs: boolean
  onToggleLogs: () => void
  studioOpen: boolean
  onToggleStudio: () => void
}) {
  const btn = (on: boolean) =>
    `inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-xs transition-colors ${
      on
        ? 'border-accent/40 bg-accent/10 text-accent'
        : 'border-line2 text-muted hover:text-ink'
    }`
  return (
    <>
      <button type="button" className={btn(hideLogs)} onClick={onToggleLogs} title="Mostrar/ocultar razonamiento intermedio">
        <MessagesSquare className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Logs</span>
      </button>
      <button type="button" className={btn(studioOpen)} onClick={onToggleStudio} title="Panel de artefactos (Studio)">
        <MonitorSmartphone className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Studio</span>
      </button>
    </>
  )
}

// Backwards-compat: stepper embebido (usado por vistas que no usan App shell).
export function PipelineStepper({ mission }: { mission: any[] }) {
  const { active, done, current } = deriveChainState(mission)
  const single = active.has('agent')
  const nodes: ChainNode[] = single ? [{ id: 'agent', label: 'Otter' }] : DEFAULT_CHAIN
  return (
    <div className="mb-3 flex items-center gap-2 overflow-x-auto rounded-lg border border-line bg-panel2 p-2">
      {nodes.map((n, i) => {
        const isDone = done.has(n.id)
        const isCurrent = current === n.id
        return (
          <div key={n.id} className="flex items-center gap-1.5 whitespace-nowrap text-[10px] font-semibold uppercase tracking-wider text-muted">
            {isDone && <Check className="h-3 w-3 text-success" />}
            {isCurrent && <Loader2 className="h-3 w-3 animate-spin text-accent" />}
            {!isDone && !isCurrent && <span className="h-2 w-2 rounded-full bg-line2" />}
            <span className={isCurrent ? 'text-accent' : isDone ? 'text-ink' : 'text-muted'}>
              {n.label}
            </span>
            {i < nodes.length - 1 && <span className="text-line2">→</span>}
          </div>
        )
      })}
    </div>
  )
}