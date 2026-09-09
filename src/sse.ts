// Parseo del flujo SSE de misiones: convierte los frames `event:`/`data:` en
// eventos tipados estructurados (independiente del pipe de transporte).

export type SseName =
  | 'system'
  | 'session_id'
  | 'task_start'
  | 'task_done'
  | 'task_error'
  | 'task_aborted'
  | 'agent_start'
  | 'agent_end'
  | 'vram_flush'
  | 'token'
  | 'tool_call'
  | 'tool_result'
  | 'delegate'
  | 'agent_injected'
  | 'loop_iter'
  | 'loop_exhausted'
  | 'diff'
  | 'perm_request'
  | 'ctx_hint'

export interface SseEvent {
  name: SseName | string
  data: Record<string, unknown>
}

export interface ToolCallData {
  agent?: string
  iteration?: number
  tool?: string
  args?: string
  title?: string
  name: string // alias, cuando el frame lo trae como `name`
}

export interface ToolResultData {
  agent?: string
  tool?: string
  ok?: boolean
  output?: string
}

/**
 * Convierte una cadena cruda SSE (`event:\n data:\n\n`) en una lista de eventos.
 * Devuelve también el payload de session_id (task_id) si ya llegó.
 */
export function parseSse(raw: string): SseEvent[] {
  const events: SseEvent[] = []
  let name = ''
  let dataBuf: string[] = []
  const push = () => {
    if (!name && dataBuf.length === 0) return
    const dataTxt = dataBuf.join('\n').replace(/^data: /, '')
    let data: Record<string, unknown> = {}
    try {
      const parsed = JSON.parse(dataTxt)
      if (parsed && typeof parsed === 'object') data = parsed as Record<string, unknown>
    } catch {
      data = { text: dataTxt }
    }
    events.push({ name: name || 'message', data })
    name = ''
    dataBuf = []
  }
  for (const line of raw.split('\n')) {
    if (line === '') {
      push()
      continue
    }
    if (line.startsWith('event:')) {
      name = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataBuf.push(line.slice(5).trimStart())
    }
  }
  push()
  return events
}

export function toolName(data: Record<string, unknown>): string {
  const t = data['tool']
  const n = data['name']
  return (typeof t === 'string' && t) || (typeof n === 'string' && n) || 'herramienta'
}

/** Normaliza una ruta de fichero: barras y prefijos absolutos de workspace. */
export function normPath(path: string): string {
  let p = path.replace(/\\/g, '/').replace(/^\/+/, '')
  const m = p.match(/(^|\/)workspace\/(.+)$/)
  return m ? m[2] : p
}

/** Extrae el argumento `args` de un frame de herramienta (string JSON u objeto). */
export function toolArgs(data: Record<string, unknown>): Record<string, unknown> {
  const a = data['args']
  if (a && typeof a === 'object') return a as Record<string, unknown>
  if (typeof a === 'string') {
    try {
      const parsed = JSON.parse(a)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>
    } catch {
      /* noop */
    }
  }
  return {}
}

/** Ruta de archivo objetivo de un frame `tool_call` (filepath/path/file). */
/** Reintenta un fetch SSE con backoff exponencial (no duplica POST si el caller lo evita). */
export async function withSseReconnect<T>(
  fn: () => Promise<T>,
  tries = 3,
): Promise<T> {
  let delay = 400
  let last: unknown
  for (let i = 0; i < tries; i++) {
    try {
      return await fn()
    } catch (e) {
      last = e
      await new Promise((r) => setTimeout(r, delay))
      delay *= 2
    }
  }
  throw last
}

export function toolCallFilepath(data: Record<string, unknown>): string | null {
  const p = data['filepath'] ?? toolArgs(data)['filepath'] ?? toolArgs(data)['path'] ?? toolArgs(data)['file']
  return typeof p === 'string' && p.trim() ? normPath(p.trim()) : null
}
