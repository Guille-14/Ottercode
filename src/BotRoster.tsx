import { useEffect, useState } from 'react'
import { fetchWithAuth } from './api'

type Bot = { name: string; title?: string; description?: string }

export default function BotRoster() {
  const [bots, setBots] = useState<Bot[]>([])
  const [tab, setTab] = useState<'sessions' | 'bots'>('bots')
  useEffect(() => {
    void fetchWithAuth('/api/bots').then((r) => r.json()).then((j) => setBots(j.bots || [])).catch(() => undefined)
  }, [])
  return (
    <div className="px-4 py-3 text-xs">
      <div className="mb-2 flex gap-2">
        <button type="button" onClick={() => setTab('sessions')} className={tab === 'sessions' ? 'text-accent' : 'text-muted'}>Sessions</button>
        <button type="button" onClick={() => setTab('bots')} className={tab === 'bots' ? 'text-accent' : 'text-muted'}>Bots</button>
      </div>
      {tab === 'bots' && bots.map((b) => (
        <div key={b.name} className="rounded border border-line bg-panel px-2 py-1">{b.title || b.name}</div>
      ))}
    </div>
  )
}
