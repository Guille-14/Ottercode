// Vista de diff unificado (```diff…```) con colores rojo/verde.

import type { ReactNode } from 'react'

function Row({ children, tone }: { children: ReactNode; tone: 'add' | 'del' | 'hunk' | 'ctx' }) {
  const cls =
    tone === 'add'
      ? 'bg-accent/10 text-accent'
      : tone === 'del'
        ? 'bg-danger/15 text-danger'
        : tone === 'hunk'
          ? 'bg-panel text-muted'
          : 'text-ink/80'
  return <div className={`px-2 py-px oc-mono text-xs ${cls}`}>{children}</div>
}

export default function DiffView({
  text,
  onAccept,
  onReject,
}: {
  text: string
  onAccept?: () => void
  onReject?: () => void
}) {
  const fence = /```diff\s*\n([\s\S]*?)```/
  const m = text.match(fence)
  const body = (m ? m[1] : text).split('\n')
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-canvas py-1">
      {(onAccept || onReject) && (
        <div className="flex justify-end gap-2 px-2 py-1">
          {onReject && (
            <button type="button" className="rounded border border-line px-2 py-0.5 text-[11px] text-danger" onClick={onReject}>
              Rechazar
            </button>
          )}
          {onAccept && (
            <button type="button" className="rounded border border-accent/40 px-2 py-0.5 text-[11px] text-accent" onClick={onAccept}>
              Aceptar
            </button>
          )}
        </div>
      )}
      {body.map((l, i) => {
        const tone = l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('@@') ? 'hunk' : 'ctx'
        return (
          <Row key={i} tone={tone}>
            {l || ' '}
          </Row>
        )
      })}
    </div>
  )
}