// Pantalla Sesiones: historial de misiones, transcript detallado y zip.

import { useEffect, useState } from 'react'
import { api, type HistoryDetail, type HistorySession } from '../api'
import { Badge, Button, Card } from '../ui'
import { F } from '../features'
import { useUi } from '../store'

export default function Sesiones() {
  const startMission = useUi((s) => s.startMission)
  const setView = useUi((s) => s.setView)
  const [sessions, setSessions] = useState<HistorySession[]>([])
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<HistoryDetail | null>(null)
  const [err, setErr] = useState('')

  const load = async (s = '') => {
    try {
      const r = await api.history(s)
      setSessions(r.sessions)
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const open = async (id: string) => {
    try {
      setSelected(await api.historyDetail(id))
    } catch (e) {
      setErr((e as Error).message)
    }
  }

  const remove = async (id: string) => {
    await api.historyDelete(id)
    await load(search)
    if (selected?.task_id === id) setSelected(null)
  }

  const cont = async (id: string, task: string) => {
    setView('misiones')
    await startMission({ task, continue_task: id, mode: 'chat' })
  }

  const exportMd = (sel: HistoryDetail) => {
    F.exportChatMd({
      task: sel.task,
      taskId: sel.task_id,
      files: sel.files,
      transcript: sel.transcript ?? [],
    })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Sesiones</h2>
          <p className="text-sm text-muted">historial de misiones y sus transcripts</p>
        </div>
        <div className="flex gap-2">
          <input
            id="histSearch"
            className="rounded-md border border-line bg-canvas px-3 py-1.5 text-sm focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            placeholder="Buscar…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void load(search)}
          />
          <Button variant="ghost" onClick={() => void load(search)}>
            Buscar
          </Button>
        </div>
      </div>

      {err && <Card className="p-3 text-sm text-danger">{err}</Card>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-2">
          {sessions.length === 0 ? (
            <p className="p-3 text-sm text-muted">Sin misiones registradas.</p>
          ) : (
            <div className="max-h-[70vh] space-y-1 overflow-y-auto">
              {sessions.map((s) => (
                <div
                  key={s.id}
                  className="flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm hover:bg-panel"
                  onClick={() => void open(s.id)}
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">{s.task}</p>
                    <p className="oc-mono text-xs text-muted">{s.id}</p>
                  </div>
                  <Badge>{s.mode}</Badge>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-4">
          {!selected ? (
            <p className="text-sm text-muted">Selecciona una sesión para ver su transcript.</p>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold">{selected.task}</h3>
                  <p className="oc-mono text-xs text-muted">{selected.task_id}</p>
                </div>
                <div className="flex gap-2">
                  <a
                    className="inline-flex items-center rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accentink hover:opacity-90"
                    href={api.zipUrl(selected.task_id)}
                  >
                    ZIP
                  </a>
                  <Button variant="ghost" onClick={() => cont(selected.task_id, selected.task)}>
                    Continuar
                  </Button>
                  <Button variant="ghost" onClick={() => exportMd(selected)}>
                    Exportar .md
                  </Button>
                  <Button variant="ghost" onClick={() => void remove(selected.task_id)}>
                    Borrar
                  </Button>
                </div>
              </div>
              <div className="max-h-[60vh] space-y-1 overflow-y-auto text-sm">
                {(selected.transcript ?? []).map((t, i) => (
                  <TranscriptRow key={i} t={t} />
                ))}
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}

function TranscriptRow({ t }: { t: NonNullable<HistoryDetail['transcript']>[number] }) {
  if (t.kind === 'agent') {
    return (
      <div className="rounded-md px-3 py-2">
        <p className="oc-mono text-xs text-muted">{t.agent}</p>
        <p className="whitespace-pre-wrap leading-relaxed">{t.text}</p>
      </div>
    )
  }
  if (t.kind === 'tool') {
    const ok = t.ok === true
    return (
      <div className="rounded-md border border-line bg-canvas px-3 py-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted">
          🛠️ {t.tool} {ok ? '· ok' : '· error'}
        </p>
        {t.output && <p className="oc-mono mt-1 whitespace-pre-wrap text-xs">{t.output}</p>}
      </div>
    )
  }
  return (
    <div className="rounded-md bg-panel px-3 py-2">
      <p className="whitespace-pre-wrap text-muted">{t.text ?? t.error ?? ''}</p>
    </div>
  )
}
