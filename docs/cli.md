# OtterCode CLI / TUI

Misma lógica que la web: `tools.py`, sandbox, RAG, MCP, `run_agent_turn`. La terminal es un **adaptador**, no un segundo agente.

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/ottercode chat
```

Ejecutable:

```bash
ottercode chat
python -m ottercode_cli chat
```

TUI (Textual va en las deps; si falta, curses): tres columnas —
**archivos | agente | estado+diff**. F1 ayuda, **F2 modelo**, F3 árbol,
F4 diff, Tab completa `/`, Esc/Ctrl+C aborta generación, Ctrl+Y YOLO.
Click un archivo del árbol para verlo en el panel de tools.

## Comandos

| Comando | Efecto |
| --- | --- |
| `ottercode chat` | REPL (o `--tui`) |
| `ottercode edit <archivo>` | Enfoca `edit_file` |
| `ottercode run <cmd>` | Sandbox (`execute_bash`) |
| `ottercode learn [tema]` | Tutor (system inject) |
| `ottercode explain` | Explica último error |
| `ottercode review` | Revisión del cwd |
| `ottercode test` | pytest en sandbox |
| `ottercode resume [id]` | Sesión en `~/.ottercode/sessions/` |
| `ottercode sessions` | Lista sesiones |

## Slash (dentro de `chat`)

`/help` `/status` `/model` `/diff` `/apply` `/reject` `/run` `/explain` `/learn` `/yolo` `/rag` `/files` `/open` `/edit` `/test` `/history` `/resume` `/permissions` `/mcp` `/tools` `/config`

## Seguridad

- `OTTERCODE_SANDBOX_REQUIRED` y denylist de `execute_bash` se respetan.
- `OTTERCODE_ASK_PERMISSIONS=1`: las tools peligrosas siguen el mismo flujo que la web (YOLO: `/yolo` o env `0`).
- No se desactiva el sandbox por defecto.

## Arquitectura

```
ottercode_cli (adapter)
    → backend.turn.run_agent_turn / OtterRun
    → tools.ToolExecutor / sandbox / rag / mcp
FastAPI + React (adapter web) → el mismo core
```
