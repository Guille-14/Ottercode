/**
 * Artefactos: Panel lateral derecho avanzado de OtterCode (estilo Claude / Gemini).
 * Se integra a la derecha del chat dividiendo la pantalla. Dispone de pestañas para
 * "Previsualizar" (HTML/SVG en iframe) y "Código" (con visor de código e índices de línea),
 * descarga de ZIP, copia rápida de código al portapapeles y modo maximizar.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Archive, ExternalLink, FileText, Eye, X, Globe, FileCode, Palette, Copy, Maximize2, Minimize2
} from 'lucide-react'
import { api, fetchWithAuth } from './api'
import { useUi, isDoneName } from './store'
import { F } from './features'

interface FlatFile {
  path: string
  size?: number
}

function flattenTree(nodes: unknown): FlatFile[] {
  const out: FlatFile[] = []
  const walk = (list: unknown, base: string[]) => {
    if (!Array.isArray(list)) return
    for (const n of list) {
      if (!n || typeof n !== 'object') continue
      const node = n as { name?: unknown; type?: unknown; size?: unknown; children?: unknown }
      const direct = typeof node.path === 'string' ? node.path : ''
      if (direct && node.type !== 'dir') {
        out.push({ path: direct, size: typeof node.size === 'number' ? node.size : undefined })
        continue
      }
      const name = typeof node.name === 'string' ? node.name : ''
      if (!name) continue
      const path = [...base, name].join('/')
      if (node.type === 'dir') walk(node.children, [...base, name])
      else out.push({ path, size: typeof node.size === 'number' ? node.size : undefined })
    }
  }
  walk(nodes, [])
  return out
}

const MAX_PREVIEW_SIZE = 500_000

function BaseName({ path }: { path: string }) {
  const seg = path.split('/')
  return <span>{seg[seg.length - 1]}</span>
}

function getFileIcon(path: string) {
  const ext = path.toLowerCase().split('.').pop() ?? ''
  switch (ext) {
    case 'html':
    case 'htm':
      return <Globe className="h-4 w-4 text-accent" />
    case 'css':
      return <Palette className="h-4 w-4 text-muted" />
    case 'js':
    case 'ts':
    case 'jsx':
    case 'tsx':
      return <FileCode className="h-4 w-4 text-ink2" />
    default:
      return <FileText className="h-4 w-4 text-muted" />
  }
}

function CodeViewer({ code }: { code: string }) {
  const lines = code.split('\n')
  return (
    <div className="oc-mono h-full overflow-auto rounded-xl border border-line bg-panel p-4 text-xs leading-relaxed select-text">
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className="hover:bg-canvas/40 transition-colors">
              <td className="w-12 select-none pr-4 text-right text-muted/40 font-medium align-top border-r border-line/30">
                {i + 1}
              </td>
              <td className="pl-4 whitespace-pre-wrap break-all text-ink align-top selection:bg-accent/20">
                {line || ' '}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ArtifactsPanel() {
  const { taskId, mission, streaming, setArtifactsOpen } = useUi()
  const studio = useUi((s) => s.studio)
  
  const [files, setFiles] = useState<FlatFile[]>([])
  const [hooks, setHooks] = useState<Record<string, { ok?: boolean; issues?: string[] }>>({})
  const [filesErr, setFilesErr] = useState('')
  const [selected, setSelected] = useState('')
  const [follow, setFollow] = useState(true)
  const [raw, setRaw] = useState('')
  const [loadErr, setLoadErr] = useState('')
  const [activeTab, setActiveTab] = useState<'preview' | 'code'>('preview')
  const [maximized, setMaximized] = useState(false)
  const [copied, setCopied] = useState(false)

  const liveFiles = useMemo(() => F.deriveLiveFiles(mission), [mission])
  const done = useMemo(() => {
    if (streaming) return false
    for (let i = mission.length - 1; i >= 0; i--) {
      const n = mission[i].name
      if (isDoneName(n)) return true
      if (n === 'agent_start' || n === 'task_start') return false
    }
    return false
  }, [mission, streaming])

  const refresh = useCallback(() => {
    if (!taskId) return
    api
      .workspace(taskId)
      .then((r) => {
        const list: FlatFile[] = flattenTree(r.tree).filter(
          (f) => {
            const base = f.path.split('/').pop()
            return Boolean(base) && !F.isHiddenWorkspaceFile(f.path)
          },
        )
        setHooks((r as { hooks?: Record<string, { ok?: boolean; issues?: string[] }> }).hooks || {})
        setFilesErr('')
        setFiles(list)
        if (follow) {
          const last = liveFiles[liveFiles.length - 1]
          if (last) {
            const resolved = list.find((f) => f.path === last || f.path.endsWith('/' + last)) ?? last
            setSelected(typeof resolved === 'string' ? resolved : resolved.path)
          }
        }
      })
      .catch((e: unknown) => setFilesErr((e as Error).message))
  }, [taskId, liveFiles, follow])

  useEffect(() => {
    if (!taskId) return
    refresh()
  }, [taskId, refresh])

  useEffect(() => {
    if (!taskId) return
    if (done && !streaming) return
    refresh()
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [taskId, done, streaming, refresh])

  // Sincronizar archivo seleccionado con la petición desde el chat (Studio target)
  useEffect(() => {
    if (studio && studio.taskId === taskId && studio.path) {
      setFollow(false)
      setSelected(studio.path)
      // Ajustar pestaña por defecto
      const ext = studio.path.toLowerCase().split('.').pop() ?? ''
      if (ext === 'html' || ext === 'htm' || ext === 'svg') {
        setActiveTab('preview')
      } else {
        setActiveTab('code')
      }
    }
  }, [studio, taskId])

  useEffect(() => {
    if (!taskId || !selected) {
      setRaw('')
      setLoadErr('')
      return
    }
    let cancelled = false
    setLoadErr('')
    setRaw('')
    fetchWithAuth(api.fileUrl(taskId, selected))
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const len = Number(r.headers.get('content-length') ?? 0)
        if (len > MAX_PREVIEW_SIZE) throw new Error('archivo demasiado grande para el panel')
        return r.text()
      })
      .then((t) => {
        if (!cancelled) setRaw(t)
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadErr((e as Error).message)
      })
    return () => {
      cancelled = true
    }
  }, [taskId, selected, liveFiles.length, streaming])

  // Ajustar la pestaña automáticamente al cambiar de archivo
  useEffect(() => {
    if (selected) {
      const ext = selected.toLowerCase().split('.').pop() ?? ''
      if (ext === 'html' || ext === 'htm' || ext === 'svg') {
        setActiveTab('preview')
      } else {
        setActiveTab('code')
      }
    }
  }, [selected])

  const previewExt = (() => {
    if (!selected) return 'text'
    const m = selected.toLowerCase().match(/\.([a-z0-9]+)$/)
    return m ? m[1] : 'text'
  })()

  const previewHtml =
    (previewExt === 'html' || previewExt === 'htm' || previewExt === 'svg') && !loadErr

  const handleCopy = () => {
    if (!raw) return
    navigator.clipboard.writeText(raw).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => undefined)
  }

  const fileButtons = files.map((f) => {
    const isLive = liveFiles.some((l) => f.path === l || f.path.endsWith('/' + l))
    const active = f.path === selected
    return (
      <button
        key={f.path}
        onClick={() => {
          setFollow(false)
          setSelected(f.path)
        }}
        title={f.path}
        className={`oc-mono flex items-center gap-2 shrink-0 rounded-xl border px-3.5 py-2 text-xs transition-all ${
          active
            ? 'border-accent/40 bg-accent/10 text-accent font-semibold shadow-xs ring-1 ring-accent/10'
            : 'border-line bg-panel text-muted hover:bg-canvas hover:text-ink'
        }`}
      >
        {isLive && !active && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent animate-pulse" />}
        {getFileIcon(f.path)}
        <span className="truncate max-w-[140px]">
          <BaseName path={f.path} />
        </span>
        {hooks[f.path] && hooks[f.path].ok === false && (
          <span className="text-[10px] font-bold text-danger" title={(hooks[f.path].issues || []).join('\n')}>
            syntax
          </span>
        )}
      </button>
    )
  })

  const asideClass = maximized
    ? 'fixed inset-0 z-40 flex flex-col bg-canvas'
    : 'flex flex-1 min-w-0 h-full flex-col border-l border-line bg-canvas'

  return (
    <section className={asideClass} aria-label="Artefactos">
      {/* Cabecera Principal */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-panel px-4">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-accent" />
          <p className="text-sm font-semibold text-ink">Explorador de Workspace</p>
          <span className="rounded-full bg-canvas border border-line px-2 py-0.5 text-[10px] font-bold text-muted">
            {liveFiles.length} archivos
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {taskId && (
            <a
              href={api.zipUrl(taskId)}
              title="Descargar todo como ZIP"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-canvas hover:text-ink"
            >
              <Archive className="h-4 w-4" />
            </a>
          )}
          <button
            type="button"
            aria-label={maximized ? 'Restaurar panel' : 'Maximizar panel'}
            onClick={() => setMaximized(!maximized)}
            title={maximized ? 'Restaurar' : 'Maximizar'}
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-canvas hover:text-ink"
          >
            {maximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button
            type="button"
            aria-label="Cerrar artefactos"
            onClick={() => setArtifactsOpen(false)}
            title="Cerrar vista partida"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-canvas hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Subcabecera de Seguimiento */}
      <div className="flex shrink-0 items-center gap-2 border-b border-line bg-canvas px-4 py-2 justify-between">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setFollow((f) => !f)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-colors border ${
              follow
                ? 'bg-accent/10 border-accent/20 text-accent'
                : 'bg-panel border-line text-muted hover:text-ink'
            }`}
            title="Seguir automáticamente el último archivo generado"
          >
            <Eye className="h-3 w-3" />
            auto-seguir
          </button>
          <span className="text-[11px] font-medium text-muted">
            {streaming ? 'grabando…' : done ? 'misión terminada' : mission.length === 0 ? 'en espera' : 'en pausa'}
          </span>
        </div>
        <p className="oc-mono truncate text-[11px] font-semibold text-muted" title={selected}>
          {selected ? `Ruta: ${selected}` : 'Ningún archivo abierto'}
        </p>
      </div>

      {/* IDE Tab List (Archivos) */}
      <div className="flex shrink-0 gap-2 overflow-x-auto border-b border-line bg-panel/30 p-2.5 scrollbar-thin">
        {filesErr ? (
          <p className="px-2 py-1 text-xs text-danger">{filesErr}</p>
        ) : files.length === 0 ? (
          <p className="px-2 py-1 text-xs text-muted">Sin archivos todavía.</p>
        ) : (
          fileButtons
        )}
      </div>

      {/* Barra de Pestañas de Visualización (Previsualizar / Código) */}
      {selected && (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-panel px-4">
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => setActiveTab('preview')}
              disabled={!previewHtml}
              className={`flex items-center gap-1.5 px-3 h-10 text-xs font-semibold border-b-2 transition-all ${
                activeTab === 'preview' && previewHtml
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink disabled:opacity-30 disabled:hover:text-muted'
              }`}
            >
              <Eye className="h-3.5 w-3.5" />
              <span>Vista Previa</span>
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('code')}
              className={`flex items-center gap-1.5 px-3 h-10 text-xs font-semibold border-b-2 transition-all ${
                activeTab === 'code'
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              <FileCode className="h-3.5 w-3.5" />
              <span>Código fuente</span>
            </button>
          </div>

          <div className="flex items-center gap-2">
            {raw && (
              <button
                type="button"
                onClick={handleCopy}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 text-xs font-semibold text-muted transition-colors hover:text-ink hover:border-line/80"
                title="Copiar código"
              >
                <Copy className="h-3.5 w-3.5" />
                <span>{copied ? '¡Copiado!' : 'Copiar'}</span>
              </button>
            )}
            {taskId && (
              <a
                href={api.fileUrl(taskId, selected)}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-canvas text-muted transition-colors hover:text-ink"
                title="Abrir en pestaña nueva"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        </div>
      )}

      {/* Contenedor del Visor */}
      <div className="min-h-0 flex-1 p-4 bg-canvas/30">
        {!selected ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
            <div className="rounded-full bg-panel p-4 border border-line">
              <FileText className="h-8 w-8 text-muted" />
            </div>
            <p className="text-sm font-semibold text-ink">Explorador de Archivos</p>
            <p className="text-xs text-muted max-w-sm">
              Selecciona un archivo del listado superior. Las páginas web creadas aparecerán y se actualizarán automáticamente.
            </p>
          </div>
        ) : loadErr ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-xs text-muted">
            <p className="text-danger font-semibold">{loadErr}</p>
          </div>
        ) : activeTab === 'preview' && previewHtml ? (
          <iframe
            className="h-full w-full rounded-xl border border-line bg-canvas shadow-sm"
            style={{ width: '100%', height: '100%', border: 'none' }}
            sandbox="allow-scripts allow-forms allow-popups"
            title="preview artefacto"
            srcDoc={F.hardenSrcdoc(raw)}
          />
        ) : (
          <CodeViewer code={raw || '…'} />
        )}
      </div>
    </section>
  )
}
