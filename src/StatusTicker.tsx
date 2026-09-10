// Ticker Hermes: herramienta en curso, agente y tiempo, pegado al composer.
import { useMemo } from 'react'
import { Loader2, Wrench } from 'lucide-react'
import { useUi } from './store'

export default function StatusTicker() {
  const streaming = useUi((s) => s.streaming)
  const mission = useUi((s) => s.mission)
  const liveAgent = useUi((s) => s.liveAgent)
  const tokensPerSec = useUi((s) => s.tokensPerSec)
  const tool = useMemo(() => {
    let last = ''
    let pending = false
    for (const e of mission) {
      if (e.name === 'tool_call') {
        last = String(e.data.title || e.data.tool || e.data.name || 'herramienta')
        pending = true
      } else if (e.name === 'tool_result') {
        pending = false
      }
    }
    return pending ? last : ''
  }, [mission])
  if (!streaming) return null
  return (
    <div className="mx-auto mb-2 flex w-full max-w-3xl items-center gap-2 px-4 text-[11px] text-muted">
      <Loader2 className="h-3.5 w-3.5 animate-spin text-ink" />
      <span className="font-medium text-ink">{liveAgent || 'Otter'}</span>
      {tool ? (
        <span className="inline-flex min-w-0 items-center gap-1 truncate rounded-full border border-line bg-panel px-2 py-0.5">
          <Wrench className="h-3 w-3 shrink-0" />
          {tool}
        </span>
      ) : (
        <span>generando…</span>
      )}
      {tokensPerSec > 0 ? <span className="ml-auto oc-mono">{tokensPerSec.toFixed(1)} tok/s</span> : null}
    </div>
  )
}
