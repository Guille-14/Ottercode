// Cliente REST tipado del backend OtterCode (vía proxy /api → :8000/:8099)

export interface GpuModel {
  name: string
  size_vram?: number
  size_ram?: number
  expires_at?: string
}

export interface Activity {
  running: boolean
  current?: string
  agent?: string
  task_id?: string
  started_at?: number
  elapsed_s?: number
  text?: string
}

export interface SystemStats {
  ok: boolean
  os?: string
  cpu: { count: number; percent: number }
  ram: { total: number; used: number; free: number }
  vram: { total: number; used: number; models: GpuModel[] }
}

export interface Pulse {
  ok: boolean
  version: string
  default_model: string
  models: string[]
  ps: GpuModel[]
  running: boolean
  activity: Activity
  num_ctx_default: number
  vram_total?: number
}

export interface Status {
  ok: boolean
  version: string
  ollama: { ok: boolean; url: string; models: string[]; models_count: number }
  default_model: string
  running: boolean
  max_review_rounds: number
  dynamic_agents: number
  api: string
  num_ctx_default: number
  num_predict_default: number
}

export interface Skill {
  name: string
  cat: string
  desc: string
  writes_fs: boolean
  enabled: boolean
  kind?: string
}

export interface SkillsResponse {
  skills: Skill[]
  disabled: string[]
}

export interface AgentInfo {
  id: string
  nombre: string
  rol?: string
  role?: string
  system_prompt?: string
  icon?: string
  color_neon?: string
  color?: string
  tools_disponibles?: string[]
  tools?: string[]
  dynamic?: boolean
  readonly?: boolean
  created_in?: string
}

export interface AgentsResponse {
  agents: AgentInfo[]
}

export interface Profile {
  name: string
  display_name: string
  model: string
  temperature: number
  top_p: number
  num_ctx: number
  system_override: string
}

export interface HistorySession {
  id: string
  task: string
  model: string
  mode: string
  start_agent: string
  created_at?: string
  status?: string
  files?: number
}

export interface HistoryResponse {
  sessions: HistorySession[]
}

export interface TranscriptEntry {
  kind: string
  agent?: string
  iteration?: number
  text?: string
  tool?: string
  ok?: boolean
  output?: string
  error?: string
}

export interface HistoryDetail {
  task_id: string
  task: string
  transcript: TranscriptEntry[]
  files?: string[]
}

export interface ProfileResponse {
  profiles: Profile[]
  active: string
}

export interface TreeNode {
  name: string
  type: 'dir' | 'file' | string
  size?: number
  children?: TreeNode[]
}

export interface WorkspaceStats {
  files: number
  dirs: number
  bytes: number
  human: string
}

export interface WorkspaceResponse {
  ok: boolean
  task_id: string
  name: string
  stats: WorkspaceStats
  truncated?: boolean
  tree: TreeNode[]
  hooks?: Record<string, { ok?: boolean; issues?: string[] }>
}

export interface ProfileActiveResponse {
  active: Profile
}

export interface IdentityDoc {
  content: string
  path: string
}

const BASE = '/api'

// ── Timeouts de red ────────────────────────────────────────────────────
// Timeout por defecto (en ms) para todas las llamadas fetch. Se aplica vía
// AbortSignal; los callers pueden pasarlo como 3er parámetro para sobreescribirlo.
const BASE_TIMEOUT_MS = 15000

// ── Autenticación con OTTERCODE_TOKEN ────────────────────────────────
// El backend permite activar un token (X-Otter-Token). La UI lo pide al
// arrancar vía /api/auth/token (solo responde a clientes locales) y lo
// inyecta en todas las peticiones automáticamente.
let authToken = ''

export function setAuthToken(t: string): void {
  authToken = t
}

export function getAuthToken(): string {
  return authToken
}

const rawFetch: typeof fetch = globalThis.fetch

export function fetchWithAuth(
  url: string,
  init: RequestInit = {},
  timeoutMs = BASE_TIMEOUT_MS,
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (authToken) headers.set('X-Otter-Token', authToken)
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
  if (init.signal) {
    if (init.signal.aborted) controller.abort()
    else init.signal.addEventListener('abort', () => controller.abort(), { once: true })
  }
  return rawFetch(url, { ...init, headers, signal: controller.signal }).finally(
    () => clearTimeout(timeoutId),
  )
}

export function ensureAuthToken(): Promise<void> {
  if (authToken) return Promise.resolve()
  return rawFetch(`${BASE}/auth/token`)
    .then((r) => (r.ok ? (r.json() as Promise<{ token?: string }>) : Promise.resolve({ token: '' })))
    .then((b) => {
      authToken = b?.token ?? ''
    })
    .catch(() => {
      authToken = ''
    })
}

export const API_HISTORY = '/api/history'
export const API_IDENTITY = '/api/identity/'
export const API_PROFILES = '/api/profiles'
export const API_TASK = '/api/task'
import { parseSse } from './sse'

async function j<T>(req: Promise<Response>): Promise<T> {
  const res = await req
  if (!res.ok) {
    let detail = res.statusText
    try {
      const b = await res.json()
      detail = b.detail ?? detail
    } catch {
      /* cuerpo no JSON */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  status: () => j<Status>(fetchWithAuth(`${BASE}/status`)),
  pulse: () => j<Pulse>(fetchWithAuth(`${BASE}/pulse`)),
  system: () => j<SystemStats>(fetchWithAuth(`${BASE}/system`)),
  settings: () =>
    j<{ ok: boolean; settings: Record<string, unknown>; ctx_bench?: Record<string, unknown> }>(
      fetchWithAuth(`${BASE}/settings`),
    ),
  saveSettings: (settings: Record<string, unknown>) =>
    j<{ ok: boolean; settings: Record<string, unknown> }>(
      fetchWithAuth(`${BASE}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings }),
      }),
    ),
  resetSettings: () =>
    j<{ ok: boolean; settings: Record<string, unknown> }>(
      fetchWithAuth(`${BASE}/settings/reset`, { method: 'POST' }),
    ),
  applyProfile: () =>
    j<{ ok: boolean; profile?: string; settings: Record<string, unknown> }>(
      fetchWithAuth(`${BASE}/settings/apply-profile`, { method: 'POST' }),
    ),
  ps: () => j<{ ok: boolean; models: GpuModel[] }>(fetchWithAuth(`${BASE}/ps`)),
  models: () => j<{ ollama_ok: boolean; models: string[] }>(fetchWithAuth(`${BASE}/models`)),
  flush: (model?: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`${BASE}/flush`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: model ?? null }),
      }),
    ),
  pullModel: (
    model: string,
    onEvent: (name: string, data: Record<string, unknown>) => void,
  ): Promise<void> =>
    new Promise<void>((resolve, reject) => {
      let buf = ''
      fetchWithAuth(`${BASE}/models/pull`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      })
        .then(async (r) => {
          if (!r.ok) {
            let detail = `HTTP ${r.status}`
            try {
              const b = await r.json()
              if (b && b.detail) detail = String(b.detail)
            } catch {
              /* cuerpo no JSON */
            }
            throw new Error(detail)
          }
          if (!r.body) return resolve()
          const reader = r.body.getReader()
          const dec = new TextDecoder()
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buf += dec.decode(value, { stream: true })
            let i
            while ((i = buf.indexOf('\n\n')) >= 0) {
              const chunk = buf.slice(0, i)
              buf = buf.slice(i + 2)
              const parsed = parseSse(chunk)
              for (const ev of parsed) onEvent(ev.name, ev.data)
            }
          }
          resolve()
        })
        .catch(reject)
    }),
  deleteModel: (model: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`${BASE}/models/delete`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      }),
    ),
  copyModel: (source: string, destination: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`${BASE}/models/copy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, destination }),
      }),
    ),
  showModel: (model: string) =>
    j<any>(
      fetchWithAuth(`${BASE}/model/show`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model }),
      }),
    ),
  skills: () => j<SkillsResponse>(fetchWithAuth(`${BASE}/skills`)),
  setSkill: (tool: string, enabled: boolean) =>
    j<{ ok: boolean; tool: string; enabled: boolean }>(
      fetchWithAuth(`${BASE}/skills/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool, enabled }),
      }),
    ),
  agents: () => j<AgentsResponse>(fetchWithAuth(`${BASE}/agents`)),
  getMemory: () => j<{ content: string }>(fetchWithAuth(`${BASE}/memory`)),
  addMemory: (item: string) => j<any>(fetchWithAuth(`${BASE}/memory/add`, { method: 'POST', body: JSON.stringify({ item }) })),
  deleteMemory: (index: number) => j<any>(fetchWithAuth(`${BASE}/memory/delete`, { method: 'POST', body: JSON.stringify({ index }) })),
  activity: () => j<Activity>(fetchWithAuth(`${BASE}/activity`)),
  history: (search = '', limit = 50, offset = 0) =>
    j<HistoryResponse>(fetchWithAuth(`${API_HISTORY}?search=${encodeURIComponent(search)}&limit=${limit}&offset=${offset}`)),
  historyDetail: (taskId: string) =>
    j<HistoryDetail>(fetchWithAuth(`${API_HISTORY}/${encodeURIComponent(taskId)}`)),
  historyDelete: (taskId: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`${API_HISTORY}/${encodeURIComponent(taskId)}`, { method: 'DELETE' }),
    ),
  historyRename: (taskId: string, name: string) =>
    j<{ ok: boolean; id: string; name: string }>(
      fetchWithAuth(`${API_HISTORY}/${encodeURIComponent(taskId)}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }),
    ),
  missionUndo: (taskId?: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`/api/mission/undo${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`, {
        method: 'POST',
      }),
    ),
  profiles: () => j<ProfileResponse>(fetchWithAuth(API_PROFILES)),
  activeProfile: () => j<ProfileActiveResponse>(fetchWithAuth(`${API_PROFILES}/active`)),
  setActiveProfile: (name: string) =>
    j<{ ok: boolean; active: Profile }>(
      fetchWithAuth(`${API_PROFILES}/active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }),
    ),
  keepProfile: (p: Profile) =>
    j<{ ok: boolean; profile: Profile }>(
      fetchWithAuth(`${API_PROFILES}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(p),
      }),
    ),
  soul: () => j<IdentityDoc>(fetchWithAuth(`${API_IDENTITY}soul`)),
  user: () => j<IdentityDoc>(fetchWithAuth(`${API_IDENTITY}user`)),
  saveSoul: (content: string) =>
    j<{ ok: boolean; content: string }>(
      fetchWithAuth(`${API_IDENTITY}soul`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }),
    ),
  saveUser: (content: string) =>
    j<{ ok: boolean; content: string }>(
      fetchWithAuth(`${API_IDENTITY}user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      }),
    ),
  abort: (taskId: string) =>
    j<{ ok: boolean }>(
      fetchWithAuth(`${BASE}/task/${encodeURIComponent(taskId)}/abort`, { method: 'POST' }),
    ),
  tree: (taskId?: string, path = '.') =>
    j<{ tree: unknown }>(
      fetchWithAuth(`${BASE}/tree?${new URLSearchParams(taskId ? { task_id: taskId, path } : { path })}`),
    ),
  checkpoints: () =>
    j<{ unfinished: { task_id: string; last: Record<string, unknown>; steps: number }[] }>(
      fetchWithAuth(`${BASE}/checkpoints`),
    ),
  workspace: (taskId: string) =>
    j<WorkspaceResponse>(fetchWithAuth(`${BASE}/workspace?task_id=${encodeURIComponent(taskId)}`)),
  zipUrl: (taskId: string) => `${BASE}/task/${encodeURIComponent(taskId)}/zip`,
  fileUrl: (taskId: string, path: string) =>
    `${BASE}/file?task_id=${encodeURIComponent(taskId)}&path=${encodeURIComponent(path)}`,
  saveFile: (taskId: string, path: string, content: string) =>
    j<{ ok: boolean; path: string; bytes: number }>(
      fetchWithAuth(`${BASE}/file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, path, content }),
      }),
    ),
  project: () => j<{ path: string; ok: boolean }>(fetchWithAuth(`${BASE}/project`)),
  setProject: (path: string) =>
    j<{ ok: boolean; path: string }>(
      fetchWithAuth(`${BASE}/project`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      }),
    ),
  todos: (taskId?: string) =>
    j<{ ok: boolean; task_id: string; todos: { content?: string; status?: string; evidence?: string }[] }>(
      fetchWithAuth(`${BASE}/todos${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`),
    ),
  compactNow: (taskId?: string) =>
    j<{ ok: boolean; summary?: string; still_over?: boolean }>(
      fetchWithAuth(`${BASE}/compact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId || null }),
      }),
    ),
  ctxBench: (
    model: string,
    onEvent: (name: string, data: Record<string, unknown>) => void,
  ): Promise<void> =>
    new Promise<void>((resolve, reject) => {
      let buf = ''
      fetchWithAuth(
        `${BASE}/ctx-bench`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model }),
        },
        600_000,
      )
        .then(async (r) => {
          if (!r.ok) {
            let detail = `HTTP ${r.status}`
            try {
              const b = await r.json()
              if (b && b.detail) detail = String(b.detail)
            } catch {
              /* */
            }
            throw new Error(detail)
          }
          if (!r.body) return resolve()
          const reader = r.body.getReader()
          const dec = new TextDecoder()
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buf += dec.decode(value, { stream: true })
            let i
            while ((i = buf.indexOf('\n\n')) >= 0) {
              const chunk = buf.slice(0, i)
              buf = buf.slice(i + 2)
              const parsed = parseSse(chunk)
              for (const ev of parsed) onEvent(ev.name, ev.data)
            }
          }
          resolve()
        })
        .catch(reject)
    }),
  applyCtx: (num_ctx: number) =>
    j<{ ok: boolean; num_ctx: number }>(
      fetchWithAuth(`${BASE}/ctx-bench/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ num_ctx }),
      }),
    ),
  mcp: () =>
    j<{ ok: boolean; ready: boolean; servers: { name: string; connected: boolean; tools: string[] }[]; tools: string[] }>(
      fetchWithAuth(`${BASE}/mcp`),
    ),
}
