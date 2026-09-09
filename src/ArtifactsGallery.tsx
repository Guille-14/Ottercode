import { useEffect, useState } from 'react'
import { fetchWithAuth } from './api'
import { useUi } from './store'

type Item = { session_id: string; path: string; type: string; size: number }

export default function ArtifactsGallery({ sessionId = '' }: { sessionId?: string }) {
  const [items, setItems] = useState<Item[]>([])
  const [q, setQ] = useState('')
  useEffect(() => {
    const u = `/api/artifacts?session_id=${encodeURIComponent(sessionId)}&q=${encodeURIComponent(q)}`
    void fetchWithAuth(u).then((r) => r.json()).then((j) => setItems(j.items || [])).catch(() => undefined)
  }, [sessionId, q])
  return (
    <div className="p-3 text-xs">
      <input className="mb-2 w-full rounded border border-line bg-canvas px-2 py-1" placeholder="buscar" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="grid grid-cols-2 gap-2">
        {items.map((it) => (
          <button
            key={it.session_id + it.path}
            type="button"
            className="truncate rounded border border-line bg-panel px-2 py-2 text-left"
            onClick={() => {
              useUi.getState().setNotice(it.path)
              if (it.session_id) useUi.getState().setView('misiones')
            }}
          >
            {it.type} · {it.path}
          </button>
        ))}
      </div>
    </div>
  )
}
