# OtterCode v2 · GAP ANALYSIS vs referencias (Claude Code · opencode · ChatGPT · DeepSeek)

> Método: lectura directa del código real (`index.html`, `static/js/{core,sessions,chat,panels}.js`,
> `static/css/app.css`, `backend.py`, `tools.py`, `events.py`, `frontend/mobile/index.html`, `sw.js`).
> Cada gap cita archivo:línea verificado. Tamaños: **S** (<30 min) · **M** (<2 h).
> Este documento es la guía de la siguiente ronda de mejora.

---

## 1. TABLA DE PUNTUACIONES (0–10)

| # | Dimensión | OtterCode | Claude | opencode | ChatGPT | DeepSeek |
|---|---|---|---|---|---|---|
| 1 | Streaming de respuesta | **7** | 10 | 9 | 10 | 9 |
| 2 | Artifacts/preview en vivo | **6** | 9 | 7 | 8 | 5 |
| 3 | Gestión de conversaciones | **5** | 8 | 8 | 10 | 8 |
| 4 | Entrada de comandos | **5** | 9 | 10 | 7 | 5 |
| 5 | Planificación/aprobación | **6** | 9 | 8 | 6 | 5 |
| 6 | Herramientas visibles | **5** | 9 | 9 | 5 | 4 |
| 7 | Errores y estados | **6** | 8 | 8 | 8 | 7 |
| 8 | Diseño visual | **6** | 8 | 8 | 9 | 7 |
| 9 | Onboarding | **3** | 8 | 7 | 9 | 7 |
| 10 | Móvil/PWA | **4** | 7 | 6 | 10 | 9 |
| | **MEDIA** | **5.3** | 8.5 | 8.1 | 8.2 | 7.0 |

---

## 2. GAPS POR DIMENSIÓN

### D1 · Streaming de respuesta — 7/10
✅ Token a token real (`backend.py:895,913`), think colapsable con cronómetro vivo y "pensó Xs"
(`chat.js:94-107,206-227`), contador aproximado por burbuja (`chat.js:229-231`), watchdog
anti-cuelgue 45 s (`chat.js:578-593`), heartbeats SSE 15 s (`backend.py:3389-3394`),
scroll-lock por intención (`sessions.js:91-108`). Supera a DeepSeek en control del think.

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| Sin markdown EN VIVO: mientras streamea, `paintBubble` vuelca texto plano con `.textContent`; fences/listas solo se maquetan al finalizar (`renderFences` desde `finalizeBubble`) → "salto" visual al acabar | `chat.js:228`, `chat.js:808-818` | En `paintBubble`, separar buffer en bloques cerrados vs cola abierta; cerrados → `appendPre`/`makeCodeBlock`; reutilizar `mdLite` (ya existe, `panels.js:788-810`) para texto estable. Claude/opencode maquetan en vivo | **M** |
| Sin tok/s ni tiempo total por respuesta (solo crono de think) | `chat.js:89-92` | Medir tokens/Δt desde `agent_start` y pintar `· ~42 tok · 8.3 tok/s` en `.msg-count` dentro de `queuePaint`. Lo hacen ChatGPT/Cline/DeepSeek | **S** |
| Contador "~tok" es chars/4, no tokens reales | `chat.js:231` | Ollama devuelve `eval_count` real al final: propagarlo en `agent_end`/`task_done` y sustituir la estimación al cerrar la burbuja | **S** |

### D2 · Artifacts/preview en vivo — 6/10
✅ Auto-apertura estilo Claude: fence grande en streaming abre el Studio y renderiza progresivo
(`liveArtifactTick`, `chat.js:243-272`); `write_file` OK abre el archivo desde disco
(`chat.js:657-664`); tarjeta de artefacto con descarga (`chat.js:355-379`); ZIP del workspace
(`panels.js:108-111`); file-bar con descarga (`index.html:339-344`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **El Studio no edita**: `pane-code` es `textContent` de solo lectura; sin guardar de vuelta | `sessions.js:255-257`, `chat.js:337-341` | `contenteditable` + botón 💾 → nuevo endpoint `POST /api/file/save` (~15 líneas junto a `api_file`, `backend.py:3564`) que escriba vía `ToolExecutor.write_file`. Claude permite editar el artifact | **M** |
| **iframe SIN `sandbox`**: los artifacts corren con acceso same-origin completo | `index.html:367` | `sandbox="allow-scripts allow-modals"` (sin allow-same-origin). claude.ai aísla siempre | **S** |
| Placeholder "📎 [código X KB — en Studio]" no es clicable ni carga nada | `chat.js:155-166` (`collapseBigFences`) | Convertirlo en botón `.fence-ref` que llame a `studioFile(path)` cuando el fence matchee un archivo en `s.files` | **S** |
| Sin diffs: `edit_file` devuelve solo "OK: editado X (N → M caracteres)" | `tools.py:1111-1130`; UI `chat.js:651-671` | `difflib.unified_diff` en `edit_file`/`write_file` y campo `diff` en el evento `tool_result` (`backend.py:1709-1712`); pintarlo rojo/verde en `toolBlock.done` | **M** |

### D3 · Gestión de conversaciones — 5/10
✅ Sesiones paralelas en segundo plano (`sessions.js:1-53`), buscador cliente
(`chat.js:983-1021`), export MD (`chat.js:1024-1053`), borrar con confirmación
(`chat.js:1056-1069`), busy-bar de misión en otra sesión (`sessions.js:345-371`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **Conversaciones de un disparo**: cada `sendMessage` crea task_id nuevo y LIMPIA la página; no hay follow-up dentro del mismo chat (el backend no recibe el transcript previo) → imposible iterar ("ahora añade tests") | `chat.js:911-917`, `backend.py:3334` | Aceptar `continue_task` en TaskRequest y precargar entradas del transcript en `build_chat_prompt` vía `_compact_context` (`backend.py:1405`); frontend: NO limpiar página si `TC.tid` existe salvo `/clear`. Es EL salto misión→conversación (paridad ChatGPT/opencode) | **M** |
| Búsqueda solo sobre título/task, nunca sobre contenido; historial capado a 50 | `chat.js:989`; `backend.py:3601-3603` | Parámetro `q` en `/api/history` que también greppee `ottercode_transcript.json`; paginación `limit/offset` | **M** |
| Replay pierde iconos de agentes dinámicos (todo sale 🦦 si no es CORE_CHAIN) y metadatos de think | `chat.js:1099-1107` | Usar `entry.agent` contra `registeredAgents` (ya cargados por `/api/agents`) antes del fallback 🦦 | **S** |
| Sin renombrar/fijar chats | `chat.js:995-1019` | Botón ✏️ en `hist-item` + `POST /api/history/{id}/rename` persistiendo `title` en el meta | **S** |

### D4 · Entrada de comandos — 5/10
✅ Popup slash (`index.html:177-235`), Esc global para abortar (`sessions.js:419-424`),
hints bajo el input (`index.html:266-269`), consola con histórico ↑↓ (`panels.js:454-468`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **El popup NO filtra al teclear**: se abre completo si el input empieza por "/", sin matching, sin ↑↓, sin Enter (solo clic) | `sessions.js:403-407` | Array único `SLASH_COMMANDS=[{cmd,desc,run}]`; filtrar por prefijo en el handler `input` y navegar con ArrowUp/Down/Enter/Tab en `keydown`. opencode tiene esto como mínimo viable | **S** |
| Sin paleta Ctrl+K (único atajo global: Esc; `grep ctrlKey static/js` = 0 resultados) | `sessions.js:419-424` | Overlay Ctrl+K con comandos + vistas + chats recientes. Estándar opencode/Cursor/ChatGPT | **M** |
| Lista duplicada HTML ↔ lógica (`applySlash`/`parseInput` en 2 ficheros) → riesgo de desincronización | `index.html:177-235` vs `sessions.js:426-481` | Generar el DOM del popup desde `SLASH_COMMANDS` al cargar (fuente única) | **S** |
| Sin `/help` | `parseInput` `sessions.js:464-481` | Comando que imprima tabla de comandos con `sysLine` | **S** |

### D5 · Planificación/aprobación — 6/10
✅ `/ultraplan` → tarjeta de aprobación Ejecutar/Copiar (`chat.js:707-737`), checklist todo_write
colapsable con contador x/y (`chat.js:553-574`, `index.html:140-149`), merge de planes desde
texto (`planFromText`, `chat.js:539-545`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| El plan no permite RECHAZAR/regenerar/editar: solo Ejecutar o Copiar | `chat.js:713-732` | Botón "🔄 Regenerar" (relanza `plan_only` con feedback del rechazo) y textarea editable antes de ejecutar. Claude Code ofrece reject-with-feedback | **S** |
| Plan como texto plano; sin checklist clicable ni % vinculado a progreso real | `chat.js:720-721` | Parsear `- [ ]`/`- [x]` con `planFromText` y renderizar mini-checklist sincronizable con `todo_write` posterior | **S** |
| La plan-bar marca "activo" siempre el primer ítem pendiente, no el paso realmente en curso | `chat.js:561-573` | Marcar activo el ítem cuyo texto solape con el `title` del `tool_call` actual (matching laxo) | **M** |

### D6 · Herramientas visibles — 5/10
✅ Bloques terminal colapsables con duración ms y salida ok/err (`chat.js:401-439`), chips de
fuentes web (`chat.js:382-398`), badge readonly/full en vista Agentes (`panels.js:527`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **Sin permisos/confirmación**: TODO se autoejecuta, incluido `execute_bash`/`python_exec` (denylist pasiva aparte) | `backend.py:1703-1712` (dispatch directo) | Toggle "preguntar antes de ejecutar/escribir": backend emite `tool_approval_required {id,tool,args}` y pausa hasta `POST /api/approve`. Exactamente el modelo de Claude Code | **M** |
| Los `args` viajan en SSE pero la UI jamás los pinta (solo `d.tool`+`d.title`; args solo para todo_write) | `chat.js:641-648` vs payload `backend.py:1657-1660,1703-1706` | Sección colapsada de argumentos en toolBlock (`filepath`, `cmd`, primeros 200 chars). Transparencia tipo opencode | **S** |
| Sin diff de ediciones | ver D2 | idem D2 | **M** |
| En replay, tool huérfanas tras un mensaje user abren burbuja de agente nueva genérica | `chat.js:1104-1111` | Agrupar tools bajo la burbuja previa del mismo agente | **S** |

### D7 · Errores y estados — 6/10
✅ Watchdog 45 s explicativo (`chat.js:578-593`), aviso cold-start VRAM
(`backend.py:3371-3384`), reintento pre-primer-token (`backend.py:935-944`), abort Esc/botón,
busy-bar con toma de control, heartbeats anti-proxy.

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| Si el fetch muere a mitad de misión solo aparece línea ❌; sin Reintentar ni reanudar | `core.js:110-114`; `chat.js:793-803` | En `task_error`, tarjeta "🔁 Reintentar" que relance `TC.lastTask` (ya guardado en `chat.js:917`). Retry con 1 clic existe en todas las referencias | **S** |
| Diálogos nativos `confirm/prompt/alert` (takeover, borrar chat, copiar modelo, token) | `chat.js:902,1058`, `panels.js:260,271`, `core.js:14` | Generalizar `openInfoModal` (`panels.js:238-257`) a `otterConfirm(msg):Promise<bool>` y sustituir los 4 usos | **S** |
| Errores HTTP crudos ("HTTP 409 en …") tirando el `detail` amigable del backend | `core.js:59-63` | En `BC.json`, leer `r.json().detail` y lanzar ese mensaje | **S** |
| Sin progreso de turno (iteración/máx) visible en el chat | `agent_start` trae `iteration`; hoy solo en vista Agentes (`panels.js:509`) | Pintar `iter N` en `.msg-model` del header de burbuja | **S** |

### D8 · Diseño visual — 6/10
✅ Tokens consistentes (`app.css:7-43`), focus-visible completo (`app.css:1836-1842`),
`prefers-reduced-motion` (`app.css:1937`), identidad cyberpunk coherente.

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **CERO responsive**: el único `@media` es reduced-motion; <~1100px vram-panel fijo + preview-col estrangulan el chat | `app.css:1937` (única media query del fichero) | Breakpoints: <1280px vram-panel→drawer colapsable; <960px ocultar gauge y compactar agent-chain. Todas las referencias colapsan sidebar | **M** |
| Iconografía mixta emojis ↔ FontAwesome en controles equivalentes | `index.html:25-64` vs `:109-131` | FA para acciones UI; emojis solo identidad/agentes | **S** |
| Estilos inline masivos en HTML generado | `chat.js:740-767`, `panels.js:538-541` | Migrar a clases CSS (.files-row ya existe parcialmente) | **S** |
| Atajos de teclado indocumentados en UI (2 hints bajo input) | `index.html:266-269` | Modal "?" con la tabla de atajos (Esc, futuros Alt+1..5, Ctrl+K) | **S** |

### D9 · Onboarding — 3/10
✅ Welcome screen con chips de ejemplo (`chat.js:961-973`), modelo persistente, tooltips abundantes.

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| **BUG: los 3 chips llaman a `useChip(this)` que NO EXISTE en ningún JS → ReferenceError; click muerto. La primera interacción del producto está rota** | `chat.js:968-970`; grep `function useChip` = 0 resultados | Definir `function useChip(el){ input.value=el.textContent.replace(/^\S+\s/,''); input.focus(); }` | **S** |
| Select arranca con 5 modelos HARDCODEADOS que quizá no existen localmente; elegir uno inexistente = misión fallida | `index.html:34-40` | Option inicial "cargando modelos…" deshabilitado hasta `refreshModels()`; disable send sin modelo válido | **S** |
| Sin tour de primera vez: modos 🤖/⚓, ctx, loop e hacker solo se descubren por tooltip | `sessions.js:130-183` | Flag localStorage primera visita → secuencia sysLine de 4 tips + modal "guía rápida" | **M** |
| `/help` no existe | ver D4 | ver D4 | **S** |

### D10 · Móvil/PWA — 4/10
✅ Instalable (manifest dinámico `backend.py:2424-2444`), SW cachea solo shell y JAMÁS la API
(`sw.js:21-24`), safe-areas, token parity, abort en el propio botón enviar
(`mobile/index.html:138-144`).

| Gap | Evidencia | Propuesta | Tamaño |
|---|---|---|---|
| Paridad mínima: sin plan-bar, tarjetas de artefacto, Studio/explorer, slash, export; tool_result cortado a 160 chars | `mobile/index.html:205-207` | Fase barata: tool colapsable con salida completa + chips de `task_done.files` enlazando a `/api/file` | **M** |
| Texto de agentes en replay truncado a 4000 chars silenciosamente | `mobile/index.html:251` | Quitar slice o añadir "…mostrar más" | **S** |
| Una sola sesión (`window.__tid`); cambiar de chat durante misión pierde la vista del stream | `mobile/index.html:141,246` | Badge "● misión en curso" en header + reconexión por polling `/api/activity` | **M** |
| Sin notificación al terminar misión en background | — | `Notification.requestPermission()` + notify en `task_done` (SW ya registrado) | **S** |

---

## 3. TOP 10 MEJORAS (ordenadas por impacto/effort)

| # | Qué | Por qué (qué haría la referencia) | Cómo (archivo + enfoque) | Tamaño |
|---|---|---|---|---|
| **1** | **Arreglar chips de bienvenida rotos** (`useChip` indefinido) | ChatGPT/Claude: los suggestions SIEMPRE funcionan; es la primera interacción y hoy lanza ReferenceError | `static/js/chat.js:961-973`: definir `useChip(el)` que vuelque el texto sin emoji al `#msgInput` y haga focus | **S** |
| **2** | **Slash autocomplete real**: filtro al teclear + ↑↓ + Enter + fuente única de verdad | opencode: la paleta filtra, navega por teclado y ejecuta sin ratón; hoy es un menú estático de clic con lista duplicada | `SLASH_COMMANDS[]` en `sessions.js`; re-render de `#slashPopup` en handler `input` (:403), teclas en `keydown` (:409); generar popup DOM y `parseInput` desde ese array | **S** |
| **3** | **Diff visual rojo/verde en write/edit_file** | Claude Code muestra TODO cambio como diff: es la señal nº1 de confianza en una herramienta de código | `tools.py:1111` difflib.unified_diff (y diff-vs-vacío en `write_file`:457); campo `diff` en `tool_result` (`backend.py:1709`); render `tb-diff-add/del` en `toolBlock.done` (`chat.js:421-438`) | **M** |
| **4** | **Botón Reintentar tras task_error** | Todas las referencias ofrecen retry 1-clic; hoy el usuario recopia el prompt a mano tras un fallo de red/Ollama | `chat.js:793-803`: tarjeta `.plan-approve` reutilizada con botón que ponga `input.value = TC.lastTask; sendMessage()` | **S** |
| **5** | **Gate opcional de permisos para execute_bash/python_exec/write*** | Claude Code pide permiso por herramienta peligrosa; sin gate, un 7B alucinando fuera de denylist ejecuta sin freno | `backend.py:1703`: si `run.ask_permissions` y tool ∈ set peligroso → emitir `tool_approval_required`, pausar en cola, resolver con `POST /api/approve`; UI: toggle junto a hacker-btn + tarjeta de aprobación | **M** |
| **6** | **Markdown en vivo durante streaming** | Ninguna referencia moderna muestra texto plano 30 s y "salta" al maquetar al final | `chat.js:169-234` (`paintBubble`): bloques cerrados → `appendPre`/`makeCodeBlock` cacheados por índice; cola abierta → textContent. Evita repintado completo comparando longitud ya renderizada | **M** |
| **7** | **Paleta Ctrl+K** (comandos + vistas + chats recientes) | opencode/Cursor: Ctrl+K es el centro de mando; OtterCode tiene 0 atajos globales salvo Esc | `commandPalette()` en `panels.js` alimentada por `SLASH_COMMANDS` + vistas + `BC.history()`; listener global en `sessions.js:419` | **M** |
| **8** | **Follow-up conversacional en el mismo chat** | ChatGPT/Claude: hilo continuo e iterativo; hoy cada mensaje borra la página y abre tarea nueva SIN memoria del transcript → no se puede decir "ahora añade tests" | Backend: `continue_task` en TaskRequest + precarga del transcript en `build_chat_prompt` pasando por `_compact_context` (`backend.py:1405`) si >30k chars; Frontend `chat.js:911`: no limpiar página si `TC.tid` existe salvo `/clear` | **M** |
| **9** | **Responsive desktop** (drawer del panel izquierdo <1280px) | Todas colapsan la sidebar; a media ventana OtterCode es inusable hoy | Media queries nuevas en `app.css`; clase `.vram-panel.collapsed` + botón hamburguesa en `.agent-chain`; persistir en localStorage | **M** |
| **10** | **`sandbox` en el iframe del Studio** | claude.ai aísla los artifacts; hoy corren same-origin con acceso completo al origen (cookies, fetch /api/*) | `index.html:367`: `sandbox="allow-scripts allow-modals"` (sin allow-same-origin) | **S** |

Menciones honrosas (todas S): editar/guardar artifact en Studio, `/help`, select de modelos
hardcodeado (`index.html:34-40`), modal genérico confirm/prompt, rename de chats,
tok/s por burbuja.

---

## 4. CONCLUSIÓN

OtterCode destaca donde ninguna referencia llega en local: **honestidad de sistema**
(VRAM real por proceso, flush keep_alive:0, watchdog, heartbeats SSE, cold-start notice) y
**pipeline de agentes visible**. Pierde frente a todas en los tres ejes que definen la sensación
de herramienta pulida:

1. **Confianza en las herramientas (D6)** — sin permisos ni diffs el usuario no puede auditar qué
   hace el agente antes/durante: la mayor distancia con Claude Code/opencode.
2. **Continuidad conversacional (D3)** — misiones de un disparo vs hilos iterativos.
3. **Teclado y entrada (D4)** — autocomplete sin filtrar, sin paleta, sin atajos.

Atacar el Top 1–6 (todo S/M, sin tocar denylist ni guard SSRF) sube la media de 5.3 a ~7.5.

