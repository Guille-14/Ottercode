// Reenlace de una misión en curso tras recargar/cambiar de pestaña: la UI ya no
// recibe SSE (el stream murió con la conexión), pero el backend sigue escribiendo
// en el workspace. Mientras corre, el panel de artefactos repuebla por polling;
// aquí esperamos a que termine para sustituir la misión congelada por el
// transcript COMPLETO (el código generado se ve entero al volver).

import { useEffect } from 'react'
import { api } from './api'
import { useUi } from './store'
import { convertTranscript, missionUnfinished } from './mission'

export function useMissionResume(): void {
  const taskId = useUi((s) => s.taskId)
  const mission = useUi((s) => s.mission)
  const streaming = useUi((s) => s.streaming)

  useEffect(() => {
    if (streaming || !taskId || !missionUnfinished(mission)) return
    let cancelled = false
    const tick = async () => {
      try {
        const act = await api.activity()
        if (cancelled) return
        if (act.running && act.task_id === taskId) return
        const detail = await api.historyDetail(taskId)
        if (cancelled) return
        useUi.setState({ mission: convertTranscript(detail), streaming: false })
      } catch {
        /* la sesión aún no está persistida: reintentar en el siguiente tick */
      }
    }
    void tick()
    const id = setInterval(tick, 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [taskId, mission, streaming])
}