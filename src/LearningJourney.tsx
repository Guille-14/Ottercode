import { useEffect, useState } from 'react'
import { fetchWithAuth } from './api'

type Node = { id: string; tipo: string; contenido: string; timestamp: number }

export default function LearningJourney() {
  const [nodes, setNodes] = useState<Node[]>([])
  const [filt, setFilt] = useState('all')
  const [idx, setIdx] = useState(0)
  const [play, setPlay] = useState(false)
  useEffect(() => {
    void fetchWithAuth(`/api/journey?filter=${encodeURIComponent(filt)}`)
      .then((r) => r.json())
      .then((j) => setNodes(j.nodes || []))
      .catch(() => undefined)
  }, [filt])
  useEffect(() => {
    if (!play) return
    const t = window.setInterval(() => setIdx((i) => (i + 1) % Math.max(nodes.length, 1)), 800)
    return () => window.clearInterval(t)
  }, [play, nodes.length])
  const shown = nodes.slice(0, idx + 1)
  return (
    <div className="mx-auto max-w-3xl space-y-2 px-4 py-4 text-sm">
      <div className="flex gap-2 text-xs">
        {['all', 'used', 'learned'].map((f) => (
          <button key={f} type="button" onClick={() => setFilt(f)} className={filt === f ? 'text-accent' : 'text-muted'}>{f}</button>
        ))}
        <button type="button" onClick={() => setPlay((p) => !p)} className="text-muted">{play ? 'pause' : 'play'}</button>
      </div>
      <input type="range" min={0} max={Math.max(0, nodes.length - 1)} value={idx} onChange={(e) => setIdx(Number(e.target.value))} className="w-full" />
      <ul className="space-y-1">
        {shown.map((n) => (
          <li key={n.id} className="rounded border border-line bg-panel px-2 py-1 text-xs">
            <span className="text-muted">{n.tipo}</span> {n.id} — {n.contenido.slice(0, 120)}
          </li>
        ))}
      </ul>
    </div>
  )
}
