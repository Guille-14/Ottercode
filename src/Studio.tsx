// Studio: Visor de nivel profesional para OtterCode.
// Muestra el Explorador de archivos en una columna, y distribuye el espacio de forma
// inteligente: si es un archivo web, muestra el editor de código a la izquierda y
// la previsualización en vivo a la derecha (vista side-by-side). Para otros formatos,
// el editor de código ocupa todo el espacio de trabajo.

import { useEffect, useMemo, useState } from 'react'
import { api, fetchWithAuth } from './api'
import { useUi } from './store'
import { Button } from './ui'
import { F } from './features'
import { Globe, FileCode, Palette, FileText, Copy, Archive, X, ExternalLink } from 'lucide-react'

interface TreeNode {
  name: string
  type: string
  size?: number
  children?: TreeNode[]
}

function flatNodes(nodes: TreeNode[] | undefined, base: string[], out: { path: string; size?: number }[]) {
  for (const n of nodes ?? []) {
    const path = [...base, n.name].join('/')
    if (n.type === 'dir') flatNodes(n.children, [...base, n.name], out)
    else out.push({ path, size: n.size })
  }
}

function getFileIcon(path: string) {
  const ext = path.toLowerCase().split('.').pop() ?? ''
  switch (ext) {
    case 'html':
    case 'htm':
      return <Globe className="h-4 w-4 text-emerald-400" />
    case 'css':
      return <Palette className="h-4 w-4 text-pink-400" />
    case 'js':
    case 'ts':
    case 'jsx':
    case 'tsx':
      return <FileCode className="h-4 w-4 text-amber-400" />
    default:
      return <FileText className="h-4 w-4 text-sky-400" />
  }
}

function Explorer({ onOpen, selected }: { onOpen: (p: string) => void; selected: string }) {
  const [files, setFiles] = useState<{ path: string; size?: number }[]>([])
  const taskId = useUi((s) => s.studio?.taskId) ?? ''
  const [err, setErr] = useState('')

  useEffect(() => {
    setErr('')
    api
      .workspace(taskId)
      .then((r) => {
        const out: { path: string; size?: number }[] = []
        flatNodes(r.tree, [], out)
        setFiles(out.filter(f => f.path.split('/').pop() !== '.otter_rag.db'))
      })
      .catch((e: unknown) => setErr((e as Error).message))
  }, [taskId])

  return (
    <aside className="w-56 shrink-0 overflow-y-auto border-r border-line bg-panel p-3">
      <p className="px-1.5 pb-3 text-[10px] font-bold uppercase tracking-wider text-muted">
        Archivos
      </p>
      {err ? (
        <p className="px-1.5 text-xs text-danger">{err}</p>
      ) : files.length === 0 ? (
        <p className="px-1.5 text-xs text-muted">sin archivos</p>
      ) : (
        <div className="space-y-1">
          {files.map((f) => {
            const active = f.path === selected
            return (
              <button
                key={f.path}
                onClick={() => onOpen(f.path)}
                className={`oc-mono flex w-full items-center gap-2 rounded-xl px-2.5 py-2 text-left text-xs transition-colors ${
                  active
                    ? 'bg-accent/10 border border-accent/20 text-accent font-semibold'
                    : 'text-muted border border-transparent hover:bg-canvas hover:text-ink'
                }`}
                title={f.path}
              >
                {getFileIcon(f.path)}
                <span className="truncate flex-1">{f.path}</span>
                {f.size != null && (
                  <span className="text-[10px] text-muted font-normal">
                    {(f.size / 1024).toFixed(1)}K
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}
    </aside>
  )
}

function StudioCodeViewer({ code, path }: { code: string; path: string }) {
  const [copied, setCopied] = useState(false)
  const lines = code.split('\n')

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => undefined)
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-line bg-panel">
      {/* Editor Header */}
      <header className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-panel px-4">
        <div className="flex items-center gap-1.5 min-w-0">
          <FileCode className="h-4 w-4 text-accent shrink-0" />
          <span className="text-xs font-semibold text-muted truncate">{path.split('/').pop()}</span>
        </div>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex h-7 items-center gap-1.5 rounded-lg border border-line bg-canvas px-2.5 text-[11px] font-semibold text-muted transition-colors hover:text-ink"
        >
          <Copy className="h-3 w-3" />
          <span>{copied ? '¡Copiado!' : 'Copiar'}</span>
        </button>
      </header>

      {/* Editor Body */}
      <div className="oc-mono flex-1 overflow-auto p-4 text-xs leading-relaxed select-text">
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
    </div>
  )
}

export default function Studio() {
  const { studio, closeStudio } = useUi()
  const [content, setContent] = useState('')
  const [loadErr, setLoadErr] = useState('')
  const [openPath, setOpenPath] = useState(studio?.path ?? '')

  const taskId = studio?.taskId ?? ''

  useEffect(() => {
    setOpenPath(studio?.path ?? '')
  }, [studio])

  useEffect(() => {
    if (!taskId || !openPath) {
      setContent('')
      return
    }
    setLoadErr('')
    setContent('')
    fetchWithAuth(api.fileUrl(taskId, openPath))
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.text()
      })
      .then((t) => setContent(t))
      .catch((e) => setLoadErr((e as Error).message))
  }, [taskId, openPath])

  const hard = useMemo(() => F.hardenSrcdoc(content), [content])

  const previewExt = (() => {
    if (!openPath) return 'text'
    const m = openPath.toLowerCase().match(/\.([a-z0-9]+)$/)
    return m ? m[1] : 'text'
  })()

  const previewHtml =
    (previewExt === 'html' || previewExt === 'htm' || previewExt === 'svg') && !loadErr

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-canvas">
      {/* Header General del Studio */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-line bg-panel px-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded-md bg-accent/15 px-2 py-0.5 text-xs font-semibold text-accent">
              STUDIO WORKSPACE
            </span>
            <p className="oc-mono truncate text-sm font-semibold text-ink">{openPath || 'sin archivo'}</p>
          </div>
          <p className="oc-mono text-[10px] text-muted">Sesión ID: {taskId}</p>
        </div>
        <div className="flex gap-2">
          <a
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 text-xs font-semibold text-muted transition-colors hover:text-ink hover:border-line/80"
            href={api.zipUrl(taskId)}
          >
            <Archive className="h-4 w-4" />
            <span>Descargar ZIP</span>
          </a>
          <Button variant="ghost" onClick={closeStudio} className="h-8 rounded-lg">
            <X className="h-4 w-4 mr-1.5" />
            <span>Cerrar Studio</span>
          </Button>
        </div>
      </header>

      {/* Workspace de Trabajo */}
      <div className="flex flex-1 overflow-hidden">
        <Explorer onOpen={(p) => setOpenPath(p)} selected={openPath} />

        <div className="flex-1 overflow-hidden p-4 bg-canvas/30">
          {!openPath || loadErr ? (
            <div className="flex h-full flex-col items-center justify-center rounded-xl border border-line bg-panel text-sm text-muted">
              {loadErr || 'Selecciona un archivo del listado izquierdo para editar o previsualizar.'}
            </div>
          ) : previewHtml ? (
            /* Vista partida: Código a la izquierda, Iframe a la derecha */
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 h-full w-full">
              <StudioCodeViewer code={content} path={openPath} />
              <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-line bg-panel">
                <header className="flex h-10 shrink-0 items-center justify-between border-b border-line bg-panel px-4">
                  <div className="flex items-center gap-1.5">
                    <Globe className="h-4 w-4 text-emerald-400" />
                    <span className="text-xs font-semibold text-muted">Previsualización web</span>
                  </div>
                  <a
                    href={api.fileUrl(taskId, openPath)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg p-1 text-muted hover:bg-canvas hover:text-ink"
                    title="Abrir en pestaña nueva"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </header>
                <div className="flex-1 p-4 bg-canvas/30">
                  <iframe
                    className="h-full w-full rounded-xl border border-line bg-white shadow-sm"
                    sandbox="allow-scripts allow-forms allow-popups"
                    title="preview"
                    srcDoc={hard}
                  />
                </div>
              </div>
            </div>
          ) : (
            /* Vista completa: Solo Editor de código */
            <div className="h-full w-full">
              <StudioCodeViewer code={content} path={openPath} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
