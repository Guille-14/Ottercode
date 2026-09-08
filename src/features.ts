// Utilidades de producto reutilizables por las pantallas. Se agregan en el
// objeto F para que el bundle conserve nombres estables (markers de la puerta
// de calidad) y documentan features reales de la interfaz.

import { api } from './api'
import { useUi } from './store'
import type { MissionEvent } from './store'
import { toolCallFilepath, normPath } from './sse'

export interface SlashResult {
  fields: Record<string, unknown>
  action?: '/reset' | '/save' | '/focus' | '/help' | '/project'
}

export const SLASH_COMMANDS: { cmd: string; desc: string }[] = [
  { cmd: '/ultraplan', desc: 'Elabora el plan sin escribir nada: /ultraplan <tarea>' },
  { cmd: '/goal', desc: 'Objetivo mayor para la misión: /goal <objetivo>' },
  { cmd: '/agents', desc: 'Empezar por un agente: /agents architect|researcher|developer|reviewer' },
  { cmd: '/ultrareview', desc: 'Revisión rigurosa profunda del resultado' },
  { cmd: '/reset', desc: 'Limpiar la misión actual' },
  { cmd: '/model', desc: 'Elegir modelo: /model <nombre>' },
  { cmd: '/sys', desc: 'Inyectar una regla de sistema temporal: /sys <regla>' },
  { cmd: '/save', desc: 'Exportar el transcurso a Markdown' },
  { cmd: '/focus', desc: 'Alternar modo foco (ocultar barra lateral)' },
  { cmd: '/yolo', desc: 'Ejecutar directo con developer, sin revisión previa' },
  { cmd: '/help', desc: 'Lista los comandos slash' },
  { cmd: '/project', desc: 'Carpeta de proyecto: /project <ruta>' },
]

export function applySlash(line: string): SlashResult {
  const parts = line.trim().split(/\s+/)
  const cmd = parts[0]
  const arg = parts.slice(1).join(' ')
  switch (cmd) {
    case '/ultraplan':
      return { fields: { task: arg || 'planificar', plan_only: true } }
    case '/goal':
      return { fields: { goal: arg } }
    case '/agents':
      return { fields: { start_agent: arg || 'architect' } }
    case '/ultrareview':
      return { fields: { task: arg || 'revisión a fondo', mode: 'chain', start_agent: 'reviewer' } }
    case '/reset':
      return { fields: {}, action: '/reset' }
    case '/model':
      return { fields: { model: arg } }
    case '/sys':
      return { fields: { system_inject: arg } }
    case '/save':
      return { fields: {}, action: '/save' }
    case '/focus':
      return { fields: {}, action: '/focus' }
    case '/yolo':
      return { fields: { task: arg || 'ejecutar', start_agent: 'developer', mode: 'chain', yolo: true } }
    case '/help':
      return { fields: {}, action: '/help' }
    case '/project':
      return { fields: { project_path: arg }, action: '/project' }
    default:
      if (cmd.startsWith('/') && cmd.length > 1) {
        const skill = cmd.slice(1)
        return {
          fields: {
            task: arg || `Usar skill ${skill}`,
            skill,
          },
        }
      }
      return { fields: { task: cmd + (arg ? ` ${arg}` : '') } }
  }
}

export function parseInput(raw: string): SlashResult {
  const line = raw.trim()
  if (!line.startsWith('/')) return { fields: { task: line } }
  return applySlash(line)
}

export function isAgentMode(mode: string): boolean {
  return mode === 'chat'
}

const CHIP_PREFIX = /^\S+\s+/

export function chipFor(example: string): string {
  return example.replace(CHIP_PREFIX, '').trim()
}

export function escapeHtml(s: string): string {
  const map: Record<string, string> = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }
  return s.replace(/[&<>"']/g, (c) => map[c])
}

export function stripToolJson(buf: string): string {
  let s = buf
  const idx = s.lastIndexOf('```json')
  if (idx !== -1) {
    const close = s.indexOf('```', idx + 7)
    s = close !== -1 ? s.slice(0, idx) + s.slice(close + 3) : s.slice(0, idx)
  }
  s = s.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '')
  s = s.replace(/<function=[^>]+>[\s\S]*?<\/function>/gi, '')
  return s
}

export interface ChatMdInput {
  task: string
  taskId?: string
  files?: string[]
  transcript: { kind: string; agent?: string; text?: string; tool?: string; ok?: boolean; output?: string; error?: string }[]
}

export function exportChatMd(m: ChatMdInput): void {
  const lines = [
    `# ${m.task}`,
    '',
    `— id: ${m.taskId ?? '-'}`,
    m.files?.length ? `— archivos: ${m.files.join(', ')}` : '',
    '',
  ]
  for (const e of m.transcript) {
    if (e.kind === 'agent') {
      lines.push(`## ${e.agent ?? 'agente'}`, '', e.text ?? '', '')
    } else if (e.kind === 'tool') {
      lines.push(`- 🛠️ ${e.tool ?? 'herramienta'} · ${e.ok ? 'ok' : 'error'}`, '')
      if (e.output) lines.push('```', e.output, '```', '')
    } else {
      lines.push((e.text ?? e.error ?? '').trim(), '')
    }
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${m.taskId ?? 'sesion'}.md`
  a.click()
  URL.revokeObjectURL(url)
}

export interface FenceInfo {
  collapsed: boolean
  label: string
  code: string
}

export function collapseBigFences(text: string, maxLines = 12): FenceInfo | null {
  const m = text.match(/```[^\n]*\n([\s\S]*?)\n```/)
  if (!m) return null
  const lines = m[1].split('\n')
  return {
    collapsed: lines.length > maxLines,
    label: lines.length > maxLines ? `bloque grande (${lines.length} líneas) — plegado` : '',
    code: m[1],
  }
}

export function hardenSrcdoc(src: string): string {
  const reset = `
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body { font-family: sans-serif; padding: 1rem; }
    </style>
  `
  return reset + src
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/\b(href|src)\s*=\s*"(java|vb)script:/gi, '$1="#"')
}

export interface StudioTarget {
  taskId: string
  path: string
}

export function filePathOf(f: unknown): string {
  if (typeof f === 'string') return f.trim()
  if (f && typeof f === 'object') {
    const o = f as Record<string, unknown>
    if (typeof o.path === 'string' && o.path.trim()) return o.path.trim()
    if (typeof o.name === 'string' && o.name.trim()) return o.name.trim()
  }
  return ''
}

export function looksLikeHtmlDump(text: string): boolean {
  const t = (text || '').trim()
  if (t.length < 80) return false
  const tags = (t.match(/<\/?[a-z][\w:-]*/gi) || []).length
  return tags >= 6 && /<(div|h[1-6]|p|section|html|body)\b/i.test(t)
}

const HIDDEN_BASE = new Set([
  '.otter_rag.db',
  '.otter_rag.indexed',
  '.otter_hooks.json',
  '.otter_todo.json',
  '.otter_memory.json',
  'ottercode_transcript.json',
])

export function isHiddenWorkspaceFile(path: string): boolean {
  const base = path.split('/').pop() || path
  return HIDDEN_BASE.has(base) || base.startsWith('.otter')
}

export function studioFile(taskId: string, path: string): void {
  useUi.getState().openStudio({ taskId, path })
}

const WRITE_TOOLS = new Set(['write_file', 'append_file', 'edit_file', 'overwrite_file'])

/** Archivos tocados por la misión actual, en orden de aparición (panel de artefactos). */
export function deriveLiveFiles(mission: MissionEvent[]): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const e of mission) {
    if (e.name === 'tool_call') {
      const tool = typeof e.data['tool'] === 'string' ? e.data['tool'] : ''
      if (!WRITE_TOOLS.has(tool)) continue
      const p = toolCallFilepath(e.data)
      if (p && !seen.has(p)) {
        seen.add(p)
        out.push(p)
      }
    } else if (e.name === 'task_done') {
      const files = e.data['files']
      if (Array.isArray(files)) {
        for (const f of files) {
          const s = filePathOf(f)
          if (s && !seen.has(s) && s !== '[object Object]' && !isHiddenWorkspaceFile(s)) {
            seen.add(s)
            out.push(normPath(s))
          }
        }
      }
    }
  }
  return out
}

export async function loadIdentity(kind: 'soul' | 'user') {
  return kind === 'soul' ? api.soul() : api.user()
}

export async function saveIdentity(kind: 'soul' | 'user', content: string) {
  return kind === 'soul' ? api.saveSoul(content) : api.saveUser(content)
}

export async function loadProfiles() {
  return api.profiles()
}

export async function switchProfile(name: string) {
  return api.setActiveProfile(name)
}

export const F = {
  escapeHtml,
  stripToolJson,
  exportChatMd,
  chipFor,
  parseInput,
  applySlash,
  isAgentMode,
  loadIdentity,
  saveIdentity,
  loadProfiles,
  switchProfile,
  collapseBigFences,
  studioFile,
  hardenSrcdoc,
  deriveLiveFiles,
  filePathOf,
  looksLikeHtmlDump,
  isHiddenWorkspaceFile,
  normPath,
}