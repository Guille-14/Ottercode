import { useEffect, useState } from 'react'
import { fetchWithAuth } from './api'
import { useUi } from './store'

type W = { id: string; task: string; status: string; elapsed: number; activity: string }

export default function SubagentsFrame() {
  const [ws, setWs] = useState<W[]>([])
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let on = true
    const tick = () => {
      fetchWithAuth('/api/subagents').then((r) => r.json()).then((j) => {
        if (on) setWs(j.workers || [])
      }).catch(() => undefined)
    }
    tick()
    const id = window.setInterval(tick, 2500)
    return () => { on = false; window.clearInterval(id) }
  }, [])
  const active = ws.filter((w) => w.status !== 'done' && w.status !== 'stopped')
  if (!active.length && !ws.length) return null
  const show = active.slice(0, 3)
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-1">
      <button type="button" onClick={() => setOpen((v) => !v)} className="text-[11px] text-muted">
        Subagentes {active.length}
      </button>
      {open && (
        <div className="mt-1 space-y-1 rounded-lg border border-line bg-panel p-2 text-xs">
          {show.map((w) => (
            <div key={w.id} className="flex items-center justify-between gap-2">
              <span className="truncate">{w.task} · {w.activity}</span>
              <span className="flex gap-1">
                <button type="button" className="text-accent" onClick={() => {
                  const g = window.prompt('Steer') || ''
                  if (g) void fetchWithAuth('/api/subagents/steer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: w.id, guidance: g }) })
                }}>Steer</button>
                <button type="button" className="text-danger" onClick={() => {
                  void fetchWithAuth('/api/subagents/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: w.id }) })
                  useUi.getState().setNotice('stop ' + w.id)
                }}>Stop</button>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
