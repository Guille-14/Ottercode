// Conversión de un detalle de sesión (/api/history/{id}) a eventos de misión.
// Compartida por App (loadPastSession) y useMissionResume para que la reentrada
// desde Recientes y el reenlace tras recarga reconstruyan la MISMA vista.

import { isDoneName, type MissionEvent } from './store'

export function convertTranscript(detail: any): MissionEvent[] {
  const evs: MissionEvent[] = []
  let s = 1
  evs.push({ id: s++, name: 'task_start', data: { task: detail.task }, at: Date.now() })
  if (detail.transcript) {
    for (const t of detail.transcript) {
      if (t.kind === 'agent') {
        evs.push({ id: s++, name: 'agent_start', data: { agent: t.agent, iteration: t.iteration ?? 1 }, at: Date.now() })
        evs.push({ id: s++, name: 'token', data: { token: t.text ?? '' }, at: Date.now() })
        evs.push({ id: s++, name: 'agent_end', data: { agent: t.agent, iteration: t.iteration ?? 1 }, at: Date.now() })
      } else if (t.kind === 'tool') {
        evs.push({ id: s++, name: 'tool_call', data: { id: t.tool, tool: t.tool, args: t.args ?? {}, title: `🛠️ ${t.tool}` }, at: Date.now() })
        evs.push({ id: s++, name: 'tool_result', data: { id: t.tool, tool: t.tool, ok: t.ok ?? true, output: t.output ?? '' }, at: Date.now() })
      } else if (t.kind === 'system') {
        const text = t.text ?? ''
        const name = text.startsWith('✅') ? 'task_done' : text.startsWith('❌') ? 'task_error' : text.startsWith('⏹') ? 'task_aborted' : 'system'
        evs.push({ id: s++, name, data: { text, files: detail.files ?? [] }, at: Date.now() })
      }
    }
  }
  return evs
}

/** La misión guardada sigue abierta (no alcanzó task_done/error/aborted). */
export function missionUnfinished(mission: MissionEvent[]): boolean {
  const last = mission[mission.length - 1]
  return Boolean(last && !isDoneName(last.name))
}