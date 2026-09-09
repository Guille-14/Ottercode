import { useRef, useState } from 'react'
import { fetchWithAuth } from './api'
import { useUi } from './store'

export default function VoiceControls({ onText }: { onText?: (t: string) => void }) {
  const [rec, setRec] = useState(false)
  const media = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const start = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const mr = new MediaRecorder(stream)
    chunks.current = []
    mr.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data) }
    mr.onstop = () => {
      const blob = new Blob(chunks.current, { type: 'audio/webm' })
      void fetchWithAuth('/api/voice/transcribe', { method: 'POST', body: blob, headers: { 'Content-Type': 'application/octet-stream' } })
        .then((r) => r.json())
        .then((j) => {
          if (j.text) onText?.(j.text)
          else useUi.getState().setNotice(j.error || 'stt')
        })
    }
    media.current = mr
    mr.start()
    setRec(true)
  }
  return (
    <button
      type="button"
      aria-label="Voz"
      className={`mb-2 text-xs ${rec ? 'text-danger' : 'text-muted'}`}
      onClick={() => {
        if (rec) { media.current?.stop(); setRec(false) } else void start()
      }}
    >
      mic
    </button>
  )
}
