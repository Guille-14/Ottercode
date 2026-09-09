// Hero de bienvenida estilo OpenWebUI para cuando no hay mensajes activos.

import { Sparkles, Code2, Search, Compass, Bot } from 'lucide-react'
import { F } from '../features'

const PROMPT_SUGGESTIONS = [
  {
    icon: Compass,
    title: 'Genera una web interactiva',
    desc: '▶ Crea una app según lo que te pida el usuario',
  },
  {
    icon: Search,
    title: 'Auditoría del proyecto',
    desc: '▶ Revisa el código de este workspace y propone mejoras',
  },
  {
    icon: Sparkles,
    title: 'Planificación arquitectónica',
    desc: '▶ Planifica (sin escribir) una landing de una cafetería',
  },
  {
    icon: Code2,
    title: 'Script con ejecución local',
    desc: '▶ Crea un script Python y verifica su salida con execute_bash',
  },
]

export default function ChatHero({
  model,
  mode,
  onSelectPrompt,
}: {
  model: string
  mode: 'chat' | 'chain'
  onSelectPrompt: (prompt: string) => void
}) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
      {/* Icono OpenWebUI con aura suave */}
      <div className="relative mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-line bg-panel shadow-sm">
        <Bot className="h-8 w-8 text-ink" />
        <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-[10px] text-accentink">
          🦦
        </span>
      </div>

      <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
        ¿En qué puedo ayudarte hoy?
      </h1>
      <p className="mt-2 max-w-md text-sm text-muted">
        OtterCode orquesta modelos locales en Ollama con herramientas de sistema,
        ficheros y ejecución en sandbox.
      </p>
      <ol className="mt-4 max-w-md space-y-1 text-left text-xs text-muted">
        <li>1. Arranca Ollama (`ollama serve`).</li>
        <li>2. `ollama pull qwen2.5-coder:7b` (o elige un modelo abajo).</li>
        <li>3. Escribe la primera tarea en el chat.</li>
      </ol>

      <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1 text-xs text-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        <span>Modelo activo: <strong className="text-ink">{model}</strong></span>
        <span>·</span>
        <span>
          {F.isAgentMode(mode)
            ? 'modo agente: un solo Otter con herramientas'
            : 'modo cadena: escuadrón secuencial'}
        </span>
      </div>

      {/* Grid de sugerencias estilo OpenWebUI (.w-cards para selftest) */}
      <div className="w-cards mt-8 grid w-full max-w-2xl grid-cols-1 gap-3 sm:grid-cols-2">
        {PROMPT_SUGGESTIONS.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.title}
              type="button"
              onClick={() => onSelectPrompt(F.chipFor(item.desc))}
              className="group flex items-start gap-3 rounded-xl border border-line bg-panel p-3.5 text-left transition-all hover:border-accent/40 hover:bg-panel2 hover:shadow-sm"
            >
              <div className="mt-0.5 rounded-lg border border-line bg-canvas p-2 text-muted transition-colors group-hover:text-ink">
                <Icon className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-ink">{item.title}</p>
                <p className="mt-0.5 truncate text-[11px] text-muted">{item.desc}</p>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
