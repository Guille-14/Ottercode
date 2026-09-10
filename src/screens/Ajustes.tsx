// Panel único de Ajustes: secciones internas, sin pantallas duplicadas.

import { useUi } from '../store'
import OllamaConfig from './OllamaConfig'
import Modelos from './Modelos'
import Skills from './Skills'
import Configuracion from './Configuracion'
import { MemoryManager } from './Identidad'
import Estado from './Estado'

export const AJUSTES_SECTIONS = [
  { id: 'parametros', label: 'Parámetros' },
  { id: 'modelos', label: 'Modelos' },
  { id: 'skills', label: 'Skills' },
  { id: 'memoria', label: 'Memoria' },
  { id: 'permisos', label: 'Permisos' },
  { id: 'mcp', label: 'MCP' },
  { id: 'telemetria', label: 'Telemetría' },
] as const

export default function Ajustes() {
  const sec = useUi((s) => s.settingsSection)
  const setSection = useUi((s) => s.setSettingsSection)
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav className="flex flex-wrap gap-1 border-b border-line bg-surface px-3 py-2" aria-label="Secciones de ajustes">
        {AJUSTES_SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSection(s.id)}
            className={`rounded-lg px-2.5 py-1 text-xs font-medium ${
              sec === s.id ? 'bg-accent/10 text-accent' : 'text-muted hover:bg-panel2 hover:text-ink'
            }`}
          >
            {s.label}
          </button>
        ))}
      </nav>
      <div className="flex-1 overflow-y-auto">
        {sec === 'parametros' && <OllamaConfig />}
        {sec === 'modelos' && <Modelos />}
        {sec === 'skills' && <Skills />}
        {sec === 'memoria' && (
          <div className="mx-auto max-w-3xl px-4 py-6">
            <MemoryManager />
          </div>
        )}
        {sec === 'permisos' && <Configuracion pane="permisos" />}
        {sec === 'mcp' && <Configuracion pane="mcp" />}
        {sec === 'telemetria' && <Estado />}
      </div>
    </div>
  )
}
