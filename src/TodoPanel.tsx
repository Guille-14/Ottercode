// Checklist de Todo visible (paleta Trade Republic). Poll al workspace de la misión.

import { useEffect, useState } from 'react'
import { ListChecks } from 'lucide-react'
import { api } from './api'
import { useUi } from './store'

interface TodoItem {
  content?: string
  status?: string
  evidence?: string
}

function mark(status: string): string {
  const s = (status || 'pending').toLowerCase()
  if (s === 'completed' || s === 'done') return '[x]'
  if (s === 'in_progress' || s === 'en_curso') return '[~]'
  return '[ ]'
}

export default function TodoPanel() {
  const taskId = useUi((s) => s.taskId)
  const streaming = useUi((s) => s.streaming)
  const mission = useUi((s) => s.mission)
  const [todos, setTodos] = useState<TodoItem[]>([])

  useEffect(() => {
    if (!taskId) {
      setTodos([])
      return
    }
    const load = () => {
      api.todos(taskId).then((r) => setTodos(r.todos || [])).catch(() => undefined)
    }
    load()
    if (!streaming) return
    const id = setInterval(load, 2500)
    return () => clearInterval(id)
  }, [taskId, streaming, mission.length])

  const doneN = todos.filter((t) => {
    const s = String(t.status || '').toLowerCase()
    return s === 'completed' || s === 'done'
  }).length
  const started = useUi((s) => s.missionStartedAt)
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!started) return
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(id)
  }, [started])

  if (!taskId || todos.length === 0) return null

  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0')
  const ss = String(elapsed % 60).padStart(2, '0')

  return (
    <aside className="shrink-0 border-l border-line bg-surface p-3 w-[220px] hidden lg:block">
      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink">
        <ListChecks className="h-3.5 w-3.5" /> Todo · Paso {doneN} de {todos.length} · {mm}:{ss}
      </p>
      <ul className="space-y-1.5">
        {todos.map((t, i) => (
          <li key={i} className="rounded-lg border border-line bg-panel px-2 py-1.5 text-[11px] text-ink">
            <span className="oc-mono text-muted">{mark(String(t.status))}</span>{' '}
            <span>{t.content}</span>
          </li>
        ))}
      </ul>
    </aside>
  )
}
