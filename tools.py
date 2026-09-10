#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 OTTERCODE · tools.py — Sistema de Skills (estilo Claude Code / gptme / crewAI)
================================================================================
 Herramientas REALES que los agentes invocan emitiendo comandos JSON.
 El backend (backend.py) parsea el JSON y delega aquí.

 Skills disponibles (por categoría):
   FS              read_file · write_file · mkdir · list_dir · tree
   Terminal        execute_bash  (bash aislado en el workdir, stdout/stderr)
   Código          python_exec · git_status · git_diff · git_log
   Web             web_fetch · web_search · wikipedia_search · arxiv_search
                   · youtube_transcript
   Datos           sqlite_query · csv_peek · json_query
   Memoria         memory_save · memory_recall   (.otter_memory.json)
   Ollama          image_describe (visión) · embed_text · model_info
                   · model_pull
   Obsidian        vault_search   (busca en OTTERCODE_VAULT)

 Contención:
   - Cada tarea tiene su workdir (workspace/<task_id>/); rutas SIEMPRE relativas.
   - Modo solo lectura (researcher/reviewer): write_file, mkdir, python_exec y
     memory_save quedan bloqueados.
   - execute_bash: timeout 120 s + denylist de patrones peligrosos (guardrail).
   - web_fetch/web_search: guard SSRF (sin localhost/IPs privadas), tope de
     descarga y de texto devuelto al modelo.

 ⚠️ Uso local de confianza: la denylist es una barrera de buenas prácticas,
    no un contenedor. No expongas el puerto 8000 a internet.
================================================================================
"""

from __future__ import annotations

import base64
import csv
import difflib
import hashlib
import html as html_lib
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import sandbox
import rag
import mcp_client

TOOLS: Dict[str, Dict[str, Any]] = {
    # ── FS ──
    "read_file":    {"cat": "FS", "writes_fs": False, "desc": "Lee un archivo del workspace",
                     "example": '{"tool": "read_file", "arguments": {"filepath": "ruta/relativa"}}'},
    "write_file":   {"cat": "FS", "writes_fs": True, "desc": "Crea un archivo NUEVO (contenido completo). PROHIBIDO si el archivo ya existe: usa edit_file",
                     "example": '{"tool": "write_file", "arguments": {"filepath": "ruta/nueva.py", "content": "CONTENIDO"}}'},
    "append_file":  {"cat": "FS", "writes_fs": True, "desc": "AÑADE contenido al final de un archivo (lo crea si no existe). Para archivos grandes: escribe POR PARTES",
                     "example": '{"tool": "append_file", "arguments": {"filepath": "index.html", "content": "<!-- siguiente parte -->"}}'},
    "mkdir":        {"cat": "FS", "writes_fs": True, "desc": "Crea un directorio (con padres si hacen falta)",
                     "example": '{"tool": "mkdir", "arguments": {"path": "src"}}'},
    "list_dir":     {"cat": "FS", "writes_fs": False, "desc": "Lista los contenidos de un directorio",
                     "example": '{"tool": "list_dir", "arguments": {"path": "."}}'},
    "tree":         {"cat": "FS", "writes_fs": False, "desc": "Árbol de directorios del proyecto (contexto)",
                     "example": '{"tool": "tree", "arguments": {"path": "."}}'},
    # ── Terminal ──
    "execute_bash": {"cat": "Terminal", "writes_fs": False, "desc": "Ejecuta un comando bash en el workspace (stdout/stderr)",
                     "example": '{"tool": "execute_bash", "arguments": {"cmd": "python3 -m py_compile main.py"}}'},
    "sys_info":     {"cat": "Terminal", "writes_fs": False, "desc": "Info del sistema: SO, CPU, RAM de disco libre, workspace",
                     "example": '{"tool": "sys_info", "arguments": {}}'},
    # ── Código ──
    "python_exec":  {"cat": "Código", "writes_fs": True, "desc": "Ejecuta código Python (subprocess, timeout 30 s, cwd workspace)",
                     "example": '{"tool": "python_exec", "arguments": {"code": "print(sum(range(10)))"}}'},
    "git_status":   {"cat": "Código", "writes_fs": False, "desc": "Estado del repo git del workspace (porcelain)",
                     "example": '{"tool": "git_status", "arguments": {}}'},
    "git_diff":     {"cat": "Código", "writes_fs": False, "desc": "Diff del repo (staged=true para --staged)",
                     "example": '{"tool": "git_diff", "arguments": {"staged": false}}'},
    "git_log":      {"cat": "Código", "writes_fs": False, "desc": "Historial de commits (oneline, máx 20)",
                     "example": '{"tool": "git_log", "arguments": {"max": 10}}'},
    "hash_text":    {"cat": "Código", "writes_fs": False, "desc": "Hash SHA-256 y MD5 de un texto",
                     "example": '{"tool": "hash_text", "arguments": {"text": "hola"}}'},
    "base64_code":  {"cat": "Código", "writes_fs": False, "desc": "Codifica o decodifica Base64 (mode encode|decode)",
                     "example": '{"tool": "base64_code", "arguments": {"text": "hola", "mode": "encode"}}'},
    "uuid_gen":     {"cat": "Código", "writes_fs": False, "desc": "Genera UUID v4 únicos (count 1-20)",
                     "example": '{"tool": "uuid_gen", "arguments": {"count": 3}}'},
    # ── Web ──
    "web_fetch":    {"cat": "Web", "writes_fs": False, "desc": "Descarga una URL pública y devuelve texto limpio (guard SSRF)",
                     "example": '{"tool": "web_fetch", "arguments": {"url": "https://example.com"}}'},
    "web_search":   {"cat": "Web", "writes_fs": False, "desc": "Búsqueda web DuckDuckGo (sin API key, top 5)",
                     "example": '{"tool": "web_search", "arguments": {"query": "fastapi sse ejemplo"}}'},
    "wikipedia_search": {"cat": "Web", "writes_fs": False, "desc": "Busca en Wikipedia (lang es/en)",
                     "example": '{"tool": "wikipedia_search", "arguments": {"query": "algoritmo dijkstra"}}'},
    "arxiv_search": {"cat": "Web", "writes_fs": False, "desc": "Busca papers en arXiv (título, resumen, id)",
                     "example": '{"tool": "arxiv_search", "arguments": {"query": "transformer attention", "max": 5}}'},
    "youtube_transcript": {"cat": "Web", "writes_fs": False, "desc": "Transcripción de un vídeo de YouTube (requiere pip install youtube-transcript-api)",
                     "example": '{"tool": "youtube_transcript", "arguments": {"url": "https://youtu.be/dQw4w9WgXcQ"}}'},
    # ── Web: API clients (gptme · Hermes · AnythingLLM · crewAI) ──
    "http_request": {"cat": "Web", "writes_fs": False, "desc": "Cliente REST completo: método, headers y body contra una URL pública",
                     "example": '{"tool": "http_request", "arguments": {"url": "https://api.ejemplo.com/v1/items", "method": "POST", "body": "{\\"a\\":1}"}}'},
    "weather":      {"cat": "Web", "writes_fs": False, "desc": "Tiempo actual + previsión 3 días de una ciudad (open-meteo, sin API key)",
                     "example": '{"tool": "weather", "arguments": {"city": "Villena"}}'},
    "github_search": {"cat": "Web", "writes_fs": False, "desc": "Busca repositorios en GitHub (nombre, descripción, estrellas)",
                     "example": '{"tool": "github_search", "arguments": {"query": "fastapi sse agent"}}'},
    "stack_search": {"cat": "Web", "writes_fs": False, "desc": "Busca preguntas en Stack Overflow (título, score, enlace)",
                     "example": '{"tool": "stack_search", "arguments": {"query": "uvicorn sse streaming"}}'},
    "npm_search":   {"cat": "Web", "writes_fs": False, "desc": "Busca paquetes en el registro npm",
                     "example": '{"tool": "npm_search", "arguments": {"query": "express rate limit"}}'},
    "pypi_info":    {"cat": "Web", "writes_fs": False, "desc": "Ficha de un paquete PyPI (versión, resumen, home)",
                     "example": '{"tool": "pypi_info", "arguments": {"name": "requests"}}'},
    # ── Datos ──
    "sqlite_query": {"cat": "Datos", "writes_fs": False, "desc": "Consulta SOLO LECTURA (SELECT) sobre un .sqlite del workspace",
                     "example": '{"tool": "sqlite_query", "arguments": {"db": "datos.db", "sql": "SELECT * FROM t LIMIT 10"}}'},
    "csv_peek":     {"cat": "Datos", "writes_fs": False, "desc": "Cabecera + primeras filas de un CSV + total",
                     "example": '{"tool": "csv_peek", "arguments": {"filepath": "datos.csv", "rows": 10}}'},
    "json_query":   {"cat": "Datos", "writes_fs": False, "desc": "Extrae una ruta de un JSON (a.b.0.c) y la pretty-printa",
                     "example": '{"tool": "json_query", "arguments": {"filepath": "conf.json", "path": "servidor.puerto"}}'},
    # ── Memoria ──
    "memory_save":  {"cat": "Memoria", "writes_fs": True, "desc": "Guarda un recuerdo persistente (key → valor) del workspace",
                     "example": '{"tool": "memory_save", "arguments": {"key": "decision", "value": "usamos SQLite por simplicidad"}}'},
    "memory_recall": {"cat": "Memoria", "writes_fs": False, "desc": "Recuerda todo o una clave concreta",
                     "example": '{"tool": "memory_recall", "arguments": {"key": "decision"}}'},
    # ── Ollama ──
    "image_describe": {"cat": "Ollama", "writes_fs": False, "desc": "Describe una imagen del workspace con un modelo de visión (llava)",
                     "example": '{"tool": "image_describe", "arguments": {"filepath": "captura.png"}}'},
    "embed_text":   {"cat": "Ollama", "writes_fs": False, "desc": "Embedding de un texto (dims + muestra) vía /api/embed",
                     "example": '{"tool": "embed_text", "arguments": {"text": "hola mundo"}}'},
    "model_info":   {"cat": "Ollama", "writes_fs": False, "desc": "Ficha de un modelo (familia, params, quant, modelfile)",
                     "example": '{"tool": "model_info", "arguments": {"model": "qwen2.5-coder:7b"}}'},
    "model_pull":   {"cat": "Ollama", "writes_fs": False, "desc": "Descarga un modelo de Ollama (progreso por hitos)",
                     "example": '{"tool": "model_pull", "arguments": {"model": "qwen2.5:3b"}}'},
    "model_list":   {"cat": "Ollama", "writes_fs": False, "desc": "Lista los modelos instalados en Ollama con su tamaño",
                     "example": '{"tool": "model_list", "arguments": {}}'},
    "ollama_consult": {"cat": "Ollama", "writes_fs": False, "desc": "Segunda opinión: consulta a OTRO modelo local instalado",
                     "example": '{"tool": "ollama_consult", "arguments": {"model": "qwen2.5:3b", "prompt": "revisa esta idea: ..."}}'},
    # ── Obsidian ──
    "vault_search": {"cat": "Obsidian", "writes_fs": False, "desc": "Busca texto en el vault de Obsidian (env OTTERCODE_VAULT)",
                     "example": '{"tool": "vault_search", "arguments": {"query": "regla de oro"}}'},
    "vault_read":   {"cat": "Obsidian", "writes_fs": False, "desc": "Lee una nota del vault (ruta relativa al vault)",
                     "example": '{"tool": "vault_read", "arguments": {"path": "Proyectos/idea.md"}}'},
    "vault_write":  {"cat": "Obsidian", "writes_fs": True, "desc": "Crea o actualiza una nota markdown en el vault",
                     "example": '{"tool": "vault_write", "arguments": {"path": "Notas/informe.md", "content": "# Informe\\n..."}}'},
    # ── Claude Code: edición quirúrgica y búsqueda ──
    "edit_file":    {"cat": "FS", "writes_fs": True, "desc": "Edita un archivo reemplazando old_string por new_string (debe ser único)",
                     "example": '{"tool": "edit_file", "arguments": {"filepath": "app.py", "old_string": "def vieja():", "new_string": "def nueva():"}}'},
    "apply_patch":  {"cat": "FS", "writes_fs": True, "desc": "Aplica unified diff o bloques SEARCH/REPLACE sobre un archivo",
                     "example": '{"tool": "apply_patch", "arguments": {"filepath": "app.py", "patch": "<<<<<<< SEARCH\\nfoo\\n=======\\nbar\\n>>>>>>> REPLACE"}}'},
    "git_commit":   {"cat": "Código", "writes_fs": True, "desc": "Commit en el workdir (message, paths opcionales)",
                     "example": '{"tool": "git_commit", "arguments": {"message": "feat: x"}}'},
    "grep_search":  {"cat": "FS", "writes_fs": False, "desc": "Busca una regex en los archivos del workspace (estilo grep -rn)",
                     "example": '{"tool": "grep_search", "arguments": {"pattern": "TODO|FIXME", "path": "."}}'},
    "glob_files":   {"cat": "FS", "writes_fs": False, "desc": "Encuentra archivos por patrón glob (**/*.py)",
                     "example": '{"tool": "glob_files", "arguments": {"pattern": "src/**/*.js"}}'},
    # ── Plan de misión (todo-list estilo Claude Code) ──
    "todo_write":   {"cat": "Plan", "writes_fs": True, "desc": "Guarda el plan de la misión como lista de pasos [{content, status, agent}]",
                     "example": '{"tool": "todo_write", "arguments": {"todos": [{"content": "crear index.html", "status": "pending", "agent": "developer"}]}}'},
    "todo_read":    {"cat": "Plan", "writes_fs": False, "desc": "Lee el plan de la misión guardado",
                     "example": '{"tool": "todo_read", "arguments": {}}'},
    # ── RAG y Búsqueda Semántica (sqlite-vec) ──
    "semantic_search": {"cat": "Memoria", "writes_fs": False, "desc": "Búsqueda semántica por significado/vectores en el repositorio y notas",
                        "example": '{"tool": "semantic_search", "arguments": {"query": "autenticación de usuarios", "top_k": 4}}'},
    "index_workspace": {"cat": "Memoria", "writes_fs": False, "desc": "Indexa semánticamente todos los archivos de código del proyecto para RAG",
                        "example": '{"tool": "index_workspace", "arguments": {}}'},
    "memory": {"cat": "Memoria", "writes_fs": True, "desc": "Memoria Hermes: add|replace|remove sobre memory|user",
               "example": '{"tool": "memory", "arguments": {"action": "add", "target": "memory", "text": "prefiero pytest"}}'},
    "use_skill": {"cat": "Memoria", "writes_fs": False, "desc": "Invoca una SKILL.md de ~/.ottercode/skills",
                  "example": '{"tool": "use_skill", "arguments": {"name": "mi-skill"}}'},
    "cronjob": {"cat": "Plan", "writes_fs": False, "desc": "CRUD de cron: create|list|update|pause|resume|run|remove",
                "example": '{"tool": "cronjob", "arguments": {"action": "list"}}'},
    "delegate_task": {"cat": "Plan", "writes_fs": False, "desc": "Lanza un subagente en background (cola GPU)",
                      "example": '{"tool": "delegate_task", "arguments": {"task": "revisa tests"}}'},
    "session_search": {"cat": "Memoria", "writes_fs": False, "desc": "Busca mensajes reales (FTS5) en sesiones",
                       "example": '{"tool": "session_search", "arguments": {"query": "oauth"}}'},
}

# Nombres alternativos aceptados (compatibilidad / slash commands)
ALIASES: Dict[str, str] = {
    "run_command": "execute_bash",
    "execute_command": "execute_bash",
    "ls": "list_dir",
    "search": "web_search",
    "rag_search": "semantic_search",
    "vector_search": "semantic_search",
    "index": "index_workspace",
    "fetch": "web_fetch",
    "python": "python_exec",
    "git": "git_status",
    "edit": "edit_file",
    "grep": "grep_search",
    "glob": "glob_files",
    "gh": "github_search",
    "sof": "stack_search",
    "npm": "npm_search",
    "pip": "pypi_info",
    "consult": "ollama_consult",
}

TOOL_NAMES: Tuple[str, ...] = tuple(TOOLS.keys())

# ---------------------------------------------------------------------------
# Límites
# ---------------------------------------------------------------------------

COMMAND_TIMEOUT = 120          # segundos máx. por execute_bash
MAX_FILE_READ_CHARS = 20_000   # tope de lectura de un archivo
MAX_OUTPUT_CHARS = 20_000      # tope de stdout/stderr devuelto al modelo
TREE_MAX_LINES = 400           # tope de líneas del árbol
TREE_MAX_DEPTH = 6
LIST_DIR_MAX_ENTRIES = 200

# Skills nuevas
PYTHON_TIMEOUT = 30            # segundos máx. por python_exec
GIT_MAX_CHARS = 8_000          # tope de salida de los wrappers git
WEB_TIMEOUT = 10               # segundos por petición web
WEB_MAX_BYTES = 300_000        # tope de descarga
WEB_TEXT_CHARS = 8_000         # tope de texto devuelto al modelo
SEARCH_MAX_RESULTS = 5
SQLITE_MAX_ROWS = 50
JSON_OUT_CHARS = 4_000
MEMORY_FILE = ".otter_memory.json"
VAULT_MAX_HITS = 10
OLLAMA_TOOLS_TIMEOUT = 180     # visión / pull pueden tardar
TODO_FILE = ".otter_todo.json" # plan de misión persistente
GREP_MAX_HITS = 40
GLOB_MAX_FILES = 60
HTTP_REQ_TIMEOUT = 15
CONSULT_TIMEOUT = 120          # segunda opinión de otro modelo
USER_AGENT = "OtterCode/3.0 (agente local; +https://github.com/ottercode)"

def ollama_base() -> str:
    """URL base de Ollama compartida con backend.py (misma env var)."""
    return os.environ.get("OTTERCODE_OLLAMA", "http://localhost:11434").rstrip("/")

# ---------------------------------------------------------------------------
# Política de comandos bloqueados (execute_bash)
# ---------------------------------------------------------------------------

_DANGEROUS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("rm raíz/usuario",        re.compile(r"\brm\s+(-[a-zA-Z]+\s+)*(--no-preserve-root\s+)?(/|~|\$HOME)(\s|$)")),
    ("rm --no-preserve-root",  re.compile(r"\brm\s+.*--no-preserve-root")),
    ("mkfs",                   re.compile(r"\bmkfs(\.[a-z0-9]+)?\b")),
    ("dd a /dev",              re.compile(r"\bdd\b[^\n]*\bof=/dev/")),
    ("apagado/reinicio",       re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+[06])\b")),
    ("fork bomb",              re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("chmod -R 777 /",         re.compile(r"\bchmod\s+-R\s+777\s+/")),
    ("chmod 777 /",            re.compile(r"\bchmod\s+777\s+/\s*$")),
    ("particionado /dev",      re.compile(r"\b(mkswap|fdisk|parted|sgdisk)\b[^\n]*/dev/")),
    ("escritura directa disco", re.compile(r">\s*/dev/(sd|nvme|hd|mmcblk|vd)")),
    ("curl|sh remoto",         re.compile(r"\bcurl\b[^\n]*\|\s*(ba|z|da)?sh\b")),
    ("wget|sh remoto",         re.compile(r"\bwget\b[^\n]*\|\s*(ba|z|da)?sh\b")),
    ("sudo",                   re.compile(r"\bsudo\b")),
    ("crontab",                re.compile(r"\bcrontab\b")),
    ("gestión de usuarios",    re.compile(r"\b(useradd|userdel|usermod|passwd|visudo)\b")),
    ("firewall",               re.compile(r"\b(iptables|ufw|firewalld|nft)\b")),
    ("servicios del sistema",  re.compile(r"\b(systemctl|service|launchctl)\b")),
    ("kill masivo",            re.compile(r"\bkill(all|-9|-SIGKILL)\s")),
    ("pkill",                  re.compile(r"\bpkill\b")),
    ("mount/umount",           re.compile(r"\b(mount|umount)\b[^\n]*/")),
    ("chown root",             re.compile(r"\bchown\b[^\n]*\broot\b")),
    ("xargs rm",               re.compile(r"\bxargs\b[^\n]*\brm\b")),
    ("eval inyectable",        re.compile(r'\beval\b[^\n]*\(\s*["\']')),
    ("ejecución remota",       re.compile(r"\b(nc|ncat|netcat)\b[^\n]*-e\b")),
    ("drop table/database",    re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+table)\b", re.I)),
    ("formateo de disco",      re.compile(r"\bformat\s+[a-zA-Z]:", re.I)),
]


class ToolError(Exception):
    """Error de skill controlado (vuelve al modelo como feedback)."""


def first_dangerous(cmd: str) -> Optional[str]:
    for name, rx in _DANGEROUS:
        if rx.search(cmd):
            return name
    return None


def _assert_public_url(url: str) -> None:
    """Guard SSRF: solo http(s) a hosts públicos (sin localhost/redes privadas)."""
    u = str(url or "").strip()
    parsed = urllib.parse.urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError(f"URL inválida (solo http/https): {url!r}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ToolError(f"No se pudo resolver el host '{parsed.hostname}': {exc}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise ToolError(
                f"URL BLOQUEADA por el guard SSRF: '{parsed.hostname}' "
                f"resuelve a una IP no pública ({ip}). Solo se permite internet público."
            )


def resolve_name(tool: str) -> str:
    """Resuelve aliases y devuelve el nombre canónico (o el mismo si no existe)."""
    tool = (tool or "").strip()
    return ALIASES.get(tool, tool)


def tool_title(tool: str, args: Dict[str, Any]) -> str:
    """Título legible para el banner de la UI."""
    if tool == "write_file":
        return f"Escribiendo en {args.get('filepath', '?')}"
    if tool == "append_file":
        return f"Añadiendo parte a {args.get('filepath', '?')}"
    if tool == "read_file":
        return f"Leyendo {args.get('filepath', '?')}"
    if tool == "mkdir":
        return f"Creando directorio {args.get('path', '?')}"
    if tool == "list_dir":
        return f"Listando {args.get('path', '.')}"
    if tool == "tree":
        return f"Árbol del proyecto {args.get('path', '.')}"
    if tool == "execute_bash":
        cmd = str(args.get("cmd", ""))
        return f"Ejecutando: {cmd if len(cmd) <= 60 else cmd[:57] + '…'}"
    if tool == "finalizar":
        return "Trabajo finalizado"
    if tool == "files":
        return "Ficheros del workspace"
    if tool == "python_exec":
        code = str(args.get("code", ""))
        first = code.strip().splitlines()[0] if code.strip() else "?"
        return f"Python: {first if len(first) <= 55 else first[:52] + '…'}"
    if tool == "git_status":
        return "Estado del repo git"
    if tool == "git_diff":
        return "Diff " + ("staged" if args.get("staged") else "de trabajo")
    if tool == "git_log":
        return f"Historial git (últimos {args.get('max', 20)})"
    if tool == "web_fetch":
        return f"Descargando {args.get('url', '?')[:60]}"
    if tool == "web_search":
        return f"Buscando en la web: {args.get('query', '?')[:50]}"
    if tool == "wikipedia_search":
        return f"Wikipedia: {args.get('query', '?')[:50]}"
    if tool == "arxiv_search":
        return f"arXiv: {args.get('query', '?')[:50]}"
    if tool == "youtube_transcript":
        return f"Transcribiendo {args.get('url', '?')[:50]}"
    if tool == "sqlite_query":
        return f"SQL sobre {args.get('db', '?')}"
    if tool == "csv_peek":
        return f"CSV {args.get('filepath', '?')}"
    if tool == "json_query":
        return f"JSON {args.get('filepath', '?')} → {args.get('path', '.')}"
    if tool == "memory_save":
        return f"Memoria ← {args.get('key', '?')}"
    if tool == "memory_recall":
        return "Memoria → " + str(args.get("key", "todo"))
    if tool == "image_describe":
        return f"Visión: {args.get('filepath', '?')}"
    if tool == "embed_text":
        return "Embedding de texto"
    if tool == "model_info":
        return f"Ficha de {args.get('model', '?')}"
    if tool == "model_pull":
        return f"Descargando modelo {args.get('model', '?')}"
    if tool == "vault_search":
        return f"Vault: {args.get('query', '?')[:50]}"
    if tool == "vault_read":
        return f"Vault ← {args.get('path', '?')}"
    if tool == "vault_write":
        return f"Vault → {args.get('path', '?')}"
    if tool == "edit_file":
        return f"Editando {args.get('filepath', '?')} (reemplazo exacto)"
    if tool == "grep_search":
        return f"Grep /{str(args.get('pattern', '?'))[:40]}/"
    if tool == "glob_files":
        return f"Glob {args.get('pattern', '**/*')}"
    if tool == "todo_write":
        n = len(args.get("todos") or [])
        return f"Plan de misión ({n} paso(s))"
    if tool == "todo_read":
        return "Leyendo plan de misión"
    if tool == "http_request":
        return f"{str(args.get('method', 'GET')).upper()} {str(args.get('url', '?'))[:55]}"
    if tool == "weather":
        return f"Tiempo en {args.get('city', '?')}"
    if tool == "github_search":
        return f"GitHub: {args.get('query', '?')[:45]}"
    if tool == "stack_search":
        return f"Stack Overflow: {args.get('query', '?')[:45]}"
    if tool == "npm_search":
        return f"npm: {args.get('query', '?')[:45]}"
    if tool == "pypi_info":
        return f"PyPI: {args.get('name', '?')}"
    if tool == "sys_info":
        return "Info del sistema"
    if tool == "hash_text":
        return "Hash del texto"
    if tool == "base64_code":
        return f"Base64 {args.get('mode', 'encode')}"
    if tool == "uuid_gen":
        return "Generando UUIDs"
    if tool == "ollama_consult":
        return f"Consultando a {args.get('model', '?')}"
    if tool == "model_list":
        return "Listando modelos instalados"
    return tool


# ---------------------------------------------------------------------------
# Ejecutor de skills
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Ejecuta skills dentro del workdir asignado a una tarea.

    readonly=True → bloquea write_file y mkdir (agentes de solo lectura:
    Investigador y Revisor). execute_bash sigue disponible para tests,
    sujeto a su propia denylist.
    """

    def __init__(self, workdir: Path | str, readonly: bool = False):
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.readonly = readonly
        self.sandbox = sandbox.SandboxExecutor(self.workdir)
        self.rag_store = rag.VectorStore(self.workdir / ".otter_rag.db")

    # ------------------------- utilidades internas -------------------------

    def _safe(self, filepath: str, is_dir_ok: bool = True) -> Path:
        raw = str(filepath or "").strip()
        if not raw or raw == ".":
            return self.workdir
        if raw.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:[\\/]", raw):
            raise ToolError(
                f"Rutas absolutas o de usuario no permitidas (usa rutas relativas al workspace): {raw}"
            )
        candidate = (self.workdir / raw).resolve()
        try:
            candidate.relative_to(self.workdir)
        except ValueError as exc:
            raise ToolError(f"Ruta fuera del workspace de la tarea: {raw}") from exc
        return candidate

    def resolve_safe(self, filepath: str) -> Path:
        """Público: valida y resuelve `filepath` dentro del workdir (anti-traversal)."""
        return self._safe(filepath)

    def _check_writable(self, tool: str) -> None:
        if self.readonly and TOOLS.get(tool, {}).get("writes_fs"):
            raise ToolError(
                f"ACCESO DENEGADO: tu agente opera en modo SOLO LECTURA y no puede usar '{tool}'."
            )

    # ----------------------------- skills FS -------------------------------

    def read_file(self, filepath: str, offset: int = 1, limit: Optional[int] = None, max_lines: Optional[int] = None) -> str:
        """Lee un archivo del disco (dentro del workdir) con soporte de paginación/límite de líneas."""
        path = self._safe(filepath)
        if not path.exists():
            raise ToolError(f"No existe el archivo: {filepath}")
        if not path.is_file():
            raise ToolError(f"No es un archivo regular: {filepath}")
        data = path.read_text(encoding="utf-8", errors="replace")
        lines = data.splitlines(keepends=True)
        lim = limit or max_lines
        if lim is not None or (offset and int(offset) > 1):
            start_idx = max(0, int(offset or 1) - 1)
            end_idx = start_idx + int(lim) if lim is not None else len(lines)
            selected = lines[start_idx:end_idx]
            data = "".join(selected)
            if end_idx < len(lines):
                data += f"\n… [lectura limitada a {len(selected)} líneas de {len(lines)} totales]"
        elif len(data) > MAX_FILE_READ_CHARS:
            data = data[:MAX_FILE_READ_CHARS] + (
                f"\n… [lectura truncada a {MAX_FILE_READ_CHARS} caracteres]"
            )
        return data

    def write_file(self, filepath: str, content: str) -> str:
        """Escribe/sobrescribe un archivo (dentro del workdir)."""
        self._check_writable("write_file")
        path = self._safe(filepath)
        if content is None:
            raise ToolError("El campo 'content' no puede ser null.")
        path.parent.mkdir(parents=True, exist_ok=True)
        old = ""
        existed = path.exists() and path.is_file()
        if existed:
            try:
                old = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                old = ""
        new = str(content)
        allow_ow = os.environ.get("OTTERCODE_ALLOW_OVERWRITE", "0").strip().lower() in ("1", "true", "yes")
        if (not existed) and path.suffix.lower() in {".html", ".htm"}:
            others: List[str] = []
            try:
                for p in self.workdir.rglob("*"):
                    if not p.is_file() or p.suffix.lower() not in {".html", ".htm"}:
                        continue
                    if p.name.startswith(".") or "node_modules" in p.parts:
                        continue
                    others.append(str(p.relative_to(self.workdir)))
                    if len(others) >= 8:
                        break
            except OSError:
                others = []
            if others:
                raise ToolError(
                    "Ya hay HTML en el workspace ("
                    + ", ".join(others)
                    + "). PROHIBIDO crear otro .html. "
                    "Usa read_file + edit_file (o append_file) sobre el archivo existente."
                )
        if existed and old.strip() and len(old) > 80 and not allow_ow:
            snippet = "\n".join(old.splitlines()[:50])[:2500]
            raise ToolError(
                json.dumps({
                    "status": "error",
                    "reason": "file_exists_use_edit_file",
                    "path": str(path.relative_to(self.workdir)),
                    "file_snippet": snippet,
                    "hint": (
                        "PROHIBIDO write_file sobre un archivo que ya existe "
                        "(NUM_PREDICT trunca y el disco no cambia). "
                        "Usa edit_file con old_string EXACTO de file_snippet."
                    ),
                }, ensure_ascii=False)
            )
        path.write_text(new, encoding="utf-8")
        rel = path.relative_to(self.workdir)
        if not existed or not old:
            return f"OK: {len(new)} caracteres escritos en {rel}"
        diff_lines = list(difflib.unified_diff(
            old.splitlines(), new.splitlines(),
            fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2,
        ))
        if not diff_lines:
            return f"OK: {len(new)} caracteres escritos en {rel} (sin cambios)"
        body = "\n".join(diff_lines[:200])
        if len(diff_lines) > 200:
            body += "\n[…diff truncado…]"
        return f"OK: {len(new)} caracteres escritos en {rel}\n\n```diff\n{body}\n```"

    def append_file(self, filepath: str, content: str) -> str:
        """AÑADE contenido al final del archivo (lo crea si no existe).

        Clave para archivos grandes: write_file sobrescribe, así que los
        modelos con límite de generación escriben POR PARTES (1ª con
        write_file, resto con append_file) sin cortar nunca el JSON."""
        self._check_writable("append_file")
        path = self._safe(filepath)
        if content is None:
            raise ToolError("El campo 'content' no puede ser null.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(str(content))
        rel = path.relative_to(self.workdir)
        return f"OK: {len(str(content))} caracteres añadidos a {rel}"

    def mkdir(self, path: str) -> str:
        """Crea un directorio (con padres) dentro del workdir."""
        self._check_writable("mkdir")
        target = self._safe(path)
        if target.exists() and not target.is_dir():
            raise ToolError(f"Ya existe un ARCHIVO con ese nombre: {path}")
        target.mkdir(parents=True, exist_ok=True)
        rel = target.relative_to(self.workdir)
        return f"OK: directorio '{rel or '.'}' listo"

    def list_dir(self, path: str = ".") -> str:
        """Lista los contenidos de un directorio (archivos con tamaño, dirs con /)."""
        base = self._safe(path)
        if not base.exists():
            raise ToolError(f"No existe el directorio: {path}")
        if not base.is_dir():
            raise ToolError(f"No es un directorio: {path}")
        entries: List[str] = []
        for e in sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if e.name == "ottercode_transcript.json":
                continue
            try:
                if e.is_dir():
                    entries.append(f"📁 {e.name}/")
                else:
                    entries.append(f"📄 {e.name}  ({e.stat().st_size} B)")
            except OSError:
                entries.append(f"❓ {e.name}")
        if not entries:
            return "(directorio vacío)"
        if len(entries) > LIST_DIR_MAX_ENTRIES:
            entries = entries[:LIST_DIR_MAX_ENTRIES] + ["… [truncado]"]
        return "\n".join(entries)

    def tree(self, path: str = ".", max_depth: int = 3) -> str:
        """Árbol de directorios del proyecto (context management)."""
        base = self._safe(path)
        if not base.exists():
            raise ToolError(f"No existe la ruta: {path}")
        try:
            depth = max(1, min(int(max_depth or 3), TREE_MAX_DEPTH))
        except (TypeError, ValueError):
            depth = 3
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".cache"}
        lines: List[str] = [f"📁 {base.name or '.'}/"]
        state = {"count": 0, "truncated": False}

        def walk(d: Path, prefix: str, depth_left: int) -> None:
            if depth_left <= 0 or state["truncated"]:
                return
            try:
                entries = sorted(
                    (e for e in d.iterdir() if e.name not in skip),
                    key=lambda e: (e.is_file(), e.name.lower()),
                )
            except OSError:
                return
            entries = [e for e in entries if e.name != "ottercode_transcript.json"]
            for i, e in enumerate(entries):
                if state["truncated"]:
                    return
                state["count"] += 1
                if state["count"] > TREE_MAX_LINES:
                    lines.append(prefix + "… [árbol truncado]")
                    state["truncated"] = True
                    return
                last = i == len(entries) - 1
                branch = "└── " if last else "├── "
                lines.append(prefix + branch + ("📁 " if e.is_dir() else "📄 ") + e.name)
                if e.is_dir():
                    walk(e, prefix + ("    " if last else "│   "), depth_left - 1)

        walk(base, "", depth)
        return "\n".join(lines)

    # ----------------------------- terminal --------------------------------

    def execute_bash(self, cmd: str, timeout: Optional[int] = None) -> str:
        """Ejecuta un comando bash en un sandbox aislado (Bubblewrap / namespaces) en el workdir."""
        command = str(cmd or "").strip()
        if not command:
            raise ToolError("El campo 'cmd' está vacío.")
        if len(command) > 4000:
            raise ToolError("Comando demasiado largo (>4000 chars).")
        # Denylist de seguridad
        dangerous = first_dangerous(command)
        if dangerous:
            raise ToolError(f"BLOQUEADO por seguridad: '{dangerous}' detectado en el comando.")
        eff_timeout = int(timeout) if timeout and int(timeout) > 0 else COMMAND_TIMEOUT
        
        # Ejecución a través del SandboxExecutor (bwrap o fallback)
        res = self.sandbox.run(command, timeout=eff_timeout)
        if res.get("timeout"):
            raise ToolError(f"El comando superó el límite de {eff_timeout}s y fue abortado.")

        out = res["stdout"] or ""
        if res["stderr"]:
            out += ("\n" if out else "") + "[stderr]\n" + res["stderr"]
        out = out.strip()
        if not out:
            out = "(sin salida)"
        if res["returncode"] != 0:
            out = f"exit code {res['returncode']}\n" + out
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n… [salida truncada]"
        return out

    # ----------------------------- código -----------------------------------

    def python_exec(self, code: str) -> str:
        """Ejecuta código Python en el sandbox aislado (timeout 30 s, cwd workspace)."""
        self._check_writable("python_exec")
        source = str(code or "").strip()
        if not source:
            raise ToolError("El campo 'code' está vacío.")
        if len(source) > 20_000:
            raise ToolError("Código demasiado largo (>20000 chars).")
        script = self.workdir / f".otter_exec_{int(time.time()*1000)}.py"
        try:
            script.write_text(source, encoding="utf-8")
            res = self.sandbox.run(f"python3 {script.name}", timeout=PYTHON_TIMEOUT)
            if res.get("timeout"):
                raise ToolError(f"El código superó el límite de {PYTHON_TIMEOUT}s y fue abortado.")
            out = res["stdout"] or ""
            if res["stderr"]:
                out += ("\n" if out else "") + "[stderr]\n" + res["stderr"]
            out = out.strip() or "(sin salida)"
            if res["returncode"] != 0:
                out = f"exit code {res['returncode']}\n" + out
        finally:
            try:
                script.unlink(missing_ok=True)
            except OSError:
                pass
        if len(out) > MAX_OUTPUT_CHARS:
            out = out[:MAX_OUTPUT_CHARS] + "\n… [salida truncada]"
        return out

    def _git(self, *git_args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *git_args],
                cwd=str(self.workdir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"git no disponible: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ToolError("git superó el timeout de 30 s.") from exc
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(sin salida)"
        if proc.returncode != 0:
            out = f"exit code {proc.returncode}\n" + out
        if len(out) > GIT_MAX_CHARS:
            out = out[:GIT_MAX_CHARS] + "\n… [salida truncada]"
        return out

    def git_status(self) -> str:
        return self._git("status", "--porcelain", "-b")

    def git_diff(self, staged: bool = False) -> str:
        return self._git("diff", "--staged") if staged else self._git("diff")

    def git_log(self, max: int = 20) -> str:
        try:
            n = max(1, min(int(max or 20), 50))
        except (TypeError, ValueError):
            n = 20
        return self._git("log", "--oneline", f"-n{n}")

    # ------------------------------- web ------------------------------------

    def web_fetch(self, url: str) -> str:
        """Descarga una URL pública y devuelve texto limpio (cap WEB_TEXT_CHARS)."""
        _assert_public_url(url)
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*"}
        )
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                ctype = resp.headers.get("Content-Type", "")
                raw = resp.read(WEB_MAX_BYTES)
        except Exception as exc:  # noqa: BLE001 — el modelo debe ver el fallo
            raise ToolError(f"No se pudo descargar {url}: {type(exc).__name__}: {exc}")
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            text = repr(raw[:2000])
        if "html" in ctype.lower() or text.lstrip()[:15].lower().startswith("<!doctype html"):
            text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = html_lib.unescape(text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n", text)
        text = text.strip()
        if not text:
            raise ToolError(f"La URL no devolvió texto extraíble ({ctype or 'sin tipo'}).")
        if len(text) > WEB_TEXT_CHARS:
            text = text[:WEB_TEXT_CHARS] + "\n… [contenido truncado]"
        return text

    def web_search(self, query: str) -> str:
        """Búsqueda web vía DuckDuckGo HTML (sin API key). Devuelve top 5."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                page = resp.read(WEB_MAX_BYTES).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"DuckDuckGo no respondió: {type(exc).__name__}: {exc}")
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page)
        hrefs = re.findall(r'class="result__a"[^>]*href="([^"]+)"', page)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, flags=re.S)
        results: List[str] = []
        for i in range(min(len(titles), SEARCH_MAX_RESULTS)):
            title = html_lib.unescape(re.sub(r"<[^>]+>", "", titles[i])).strip()
            href = hrefs[i] if i < len(hrefs) else ""
            m = re.search(r"uddg=([^&]+)", href)
            real = urllib.parse.unquote(m.group(1)) if m else href
            snip = html_lib.unescape(re.sub(r"<[^>]+>", "", snips[i])).strip() if i < len(snips) else ""
            results.append(f"{i+1}. {title}\n   {real}\n   {snip[:200]}")
        if not results:
            return "(sin resultados — DuckDuckGo pudo limitar la petición; prueba web_fetch directo)"
        return "\n".join(results)

    def wikipedia_search(self, query: str, lang: str = "es") -> str:
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        language = "en" if str(lang or "").lower().startswith("en") else "es"
        url = (
            f"https://{language}.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={urllib.parse.quote(q)}&format=json&srlimit={SEARCH_MAX_RESULTS}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                data = json.loads(resp.read(WEB_MAX_BYTES).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Wikipedia no respondió: {type(exc).__name__}: {exc}")
        hits = data.get("query", {}).get("search", [])
        if not hits:
            return "(sin resultados en Wikipedia)"
        out = []
        for i, h in enumerate(hits[:SEARCH_MAX_RESULTS], 1):
            snip = html_lib.unescape(re.sub(r"<[^>]+>", "", h.get("snippet", "")))
            link = f"https://{language}.wikipedia.org/wiki/{urllib.parse.quote(h['title'].replace(' ', '_'))}"
            out.append(f"{i}. {h['title']}\n   {link}\n   {snip[:200]}")
        return "\n".join(out)

    def arxiv_search(self, query: str, max: int = 5) -> str:
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        try:
            n = max(1, min(int(max or 5), 10))
        except (TypeError, ValueError):
            n = 5
        url = (
            "http://export.arxiv.org/api/query?search_query=all:"
            + urllib.parse.quote(q)
            + f"&max_results={n}&sortBy=relevance"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                root = ET.fromstring(resp.read(WEB_MAX_BYTES))
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"arXiv no respondió: {type(exc).__name__}: {exc}")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        if not entries:
            return "(sin resultados en arXiv)"
        out = []
        for i, e in enumerate(entries[:n], 1):
            title = re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip()
            summary = re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip()
            link = e.findtext("a:id", "", ns).strip()
            published = (e.findtext("a:published", "", ns) or "")[:10]
            out.append(f"{i}. {title} ({published})\n   {link}\n   {summary[:220]}")
        return "\n".join(out)

    def youtube_transcript(self, url: str) -> str:
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
        except ImportError as exc:
            raise ToolError(
                "Falta la librería 'youtube-transcript-api'. "
                "Instálala con: pip install youtube-transcript-api"
            ) from exc
        m = re.search(
            r"(?:youtu\.be/|v=|shorts/|embed/)([A-Za-z0-9_-]{11})",
            str(url or ""),
        ) or re.fullmatch(r"[A-Za-z0-9_-]{11}", str(url or "").strip())
        if not m:
            raise ToolError("No pude extraer el ID de vídeo de esa URL de YouTube.")
        video_id = m.group(1) if m.lastindex else m.group(0)
        try:  # API ≥ 1.0
            data = YouTubeTranscriptApi().fetch(video_id, languages=("es", "en"))
            text = " ".join(s.text for s in data)
        except AttributeError:  # API 0.x
            text = " ".join(
                s["text"] for s in YouTubeTranscriptApi.get_transcript(
                    video_id, languages=("es", "en")
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"Transcripción no disponible: {type(exc).__name__}: {exc}")
        if not text.strip():
            raise ToolError("La transcripción vino vacía.")
        if len(text) > WEB_TEXT_CHARS:
            text = text[:WEB_TEXT_CHARS] + "… [truncado]"
        return text

    # ------------------------------ datos -----------------------------------

    def sqlite_query(self, db: str, sql: str) -> str:
        path = self._safe(db)
        if not path.exists() or not path.is_file():
            raise ToolError(f"No existe la base de datos: {db}")
        statement = str(sql or "").strip().rstrip(";")
        if not statement.upper().startswith(("SELECT", "WITH", "EXPLAIN")):
            raise ToolError("Solo se permiten consultas de LECTURA (SELECT/WITH/EXPLAIN).")
        uri = path.resolve().as_uri() + "?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.Error as exc:
            raise ToolError(f"No se pudo abrir la BD (¿es sqlite?): {exc}")
        try:
            cur = conn.execute(statement)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(SQLITE_MAX_ROWS + 1)
        except sqlite3.Error as exc:
            raise ToolError(f"SQL error: {exc}")
        finally:
            conn.close()
        more = len(rows) > SQLITE_MAX_ROWS
        rows = rows[:SQLITE_MAX_ROWS]
        if not cols:
            return "OK (consulta sin conjunto de resultados)"
        lines = [" | ".join(cols), "-" * min(120, max(20, sum(len(c) for c in cols)))]
        for r in rows:
            lines.append(" | ".join("NULL" if v is None else str(v)[:80] for v in r))
        if more:
            lines.append(f"… [+{SQLITE_MAX_ROWS} filas más truncadas]")
        lines.append(f"({len(rows)} fila(s))")
        return "\n".join(lines)

    def csv_peek(self, filepath: str, rows: int = 10) -> str:
        path = self._safe(filepath)
        if not path.exists() or not path.is_file():
            raise ToolError(f"No existe el archivo: {filepath}")
        try:
            n = max(1, min(int(rows or 10), 50))
        except (TypeError, ValueError):
            n = 10
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header = next(reader)
            except StopIteration:
                raise ToolError("El CSV está vacío.")
            sample = []
            total = 0
            for row in reader:
                total += 1
                if len(sample) < n:
                    sample.append(row)
        lines = [f"Columnas ({len(header)}): " + " | ".join(header)]
        for row in sample:
            lines.append("  " + " | ".join(c[:40] for c in row))
        lines.append(f"({total} fila(s) de datos)")
        out = "\n".join(lines)
        return out[:MAX_OUTPUT_CHARS]

    def json_query(self, filepath: str, path: str = "") -> str:
        path_f = self._safe(filepath)
        if not path_f.exists() or not path_f.is_file():
            raise ToolError(f"No existe el archivo: {filepath}")
        try:
            data = json.loads(path_f.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ToolError(f"JSON inválido: {exc}")
        node = data
        walked: List[str] = []
        for token in [t for t in str(path or "").split(".") if t]:
            m = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", token)
            if not m:
                raise ToolError(f"Token de ruta inválido: {token} (usa a.b.0.c)")
            key, idx = m.group(1), m.group(2)
            if isinstance(node, dict):
                if key not in node:
                    raise ToolError(f"Clave '{key}' no existe (ruta: {'.'.join(walked) or 'raíz'})")
                node = node[key]
            elif isinstance(node, list):
                try:
                    node = node[int(key)]
                except (ValueError, IndexError):
                    raise ToolError(f"Índice '{key}' inválido para lista en {'.'.join(walked) or 'raíz'}")
            else:
                raise ToolError(f"'{'.'.join(walked)}' no es dict/list; no puedo bajar por '{key}'")
            walked.append(token)
        out = json.dumps(node, ensure_ascii=False, indent=2)
        if len(out) > JSON_OUT_CHARS:
            out = out[:JSON_OUT_CHARS] + "\n… [truncado]"
        return out or "(vacío)"

    # ----------------------------- memoria ----------------------------------

    def _memory_path(self) -> Path:
        return self.workdir / MEMORY_FILE

    def memory_save(self, key: str, value: str) -> str:
        self._check_writable("memory_save")
        k = str(key or "").strip()
        if not k:
            raise ToolError("El campo 'key' está vacío.")
        mem: Dict[str, Any] = {}
        if self._memory_path().exists():
            try:
                mem = json.loads(self._memory_path().read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                mem = {}
        mem[k] = {"value": str(value or ""), "ts": time.strftime("%Y-%m-%d %H:%M")}
        self._memory_path().write_text(
            json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return f"OK: memoria '{k}' guardada ({len(mem)} recuerdo(s) en total)"

    def memory_recall(self, key: str = "") -> str:
        if not self._memory_path().exists():
            return "(memoria vacía: aún no hay recuerdos guardados)"
        try:
            mem = json.loads(self._memory_path().read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "(memoria corrupta o ilegible)"
        k = str(key or "").strip()
        if k:
            if k not in mem:
                return f"(no hay recuerdo con la clave '{k}')"
            return f"{k}: {mem[k].get('value', '')} [{mem[k].get('ts', '')}]"
        if not mem:
            return "(memoria vacía)"
        return "\n".join(f"• {kk}: {v.get('value', '')} [{v.get('ts', '')}]" for kk, v in mem.items())

    # ------------------------------ ollama ----------------------------------

    def image_describe(self, filepath: str, model: str = "llava", prompt: str = "") -> str:
        path = self._safe(filepath)
        if not path.exists() or not path.is_file():
            raise ToolError(f"No existe la imagen: {filepath}")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            raise ToolError(f"Extensión no soportada para visión: {path.suffix}")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        try:
            resp = requests.post(
                f"{ollama_base()}/api/chat",
                json={
                    "model": str(model or "llava"),
                    "stream": False,
                    "messages": [{
                        "role": "user",
                        "content": str(prompt or "Describe esta imagen con detalle técnico."),
                        "images": [b64],
                    }],
                },
                timeout=OLLAMA_TOOLS_TIMEOUT,
            )
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
        except requests.RequestException as exc:
            raise ToolError(
                f"Ollama visión no disponible (¿está '{model}' descargado?): {exc}"
            )
        if not content.strip():
            raise ToolError("El modelo de visión devolvió una descripción vacía.")
        return content[:WEB_TEXT_CHARS]

    def embed_text(self, text: str, model: str = "nomic-embed-text") -> str:
        t = str(text or "").strip()
        if not t:
            raise ToolError("El campo 'text' está vacío.")
        try:
            resp = requests.post(
                f"{ollama_base()}/api/embed",
                json={"model": str(model or "nomic-embed-text"), "input": t},
                timeout=60,
            )
            resp.raise_for_status()
            embeddings = resp.json().get("embeddings") or []
        except requests.RequestException as exc:
            raise ToolError(
                f"Embedding no disponible (¿está '{model}' descargado?): {exc}"
            )
        if not embeddings:
            raise ToolError("Ollama no devolvió embeddings.")
        vec = embeddings[0]
        return (
            f"dims={len(vec)} modelo={model}\n"
            f"primeros valores: {[round(v, 4) for v in vec[:8]]}…"
        )

    def model_info(self, model: str) -> str:
        name = str(model or "").strip()
        if not name:
            raise ToolError("El campo 'model' está vacío.")
        try:
            resp = requests.post(
                f"{ollama_base()}/api/show", json={"model": name}, timeout=30
            )
            resp.raise_for_status()
            info = resp.json()
        except requests.RequestException as exc:
            raise ToolError(f"No pude consultar {name}: {exc}")
        details = info.get("details", {})
        caps = info.get("capabilities", [])
        parts = [
            f"modelo: {name}",
            f"familia: {details.get('family', '?')} {details.get('families', '')}",
            f"parámetros: {details.get('parameter_size', '?')}",
            f"cuantización: {details.get('quantization_level', '?')}",
            f"capacidades: {', '.join(caps) or '—'}",
        ]
        modelfile = info.get("modelfile", "")
        if modelfile:
            parts.append("modelfile (extracto):\n" + modelfile[:1200])
        return "\n".join(parts)

    def model_pull(self, model: str) -> str:
        name = str(model or "").strip()
        if not re.fullmatch(r"[\w.\-/:%]+", name):
            raise ToolError(f"Nombre de modelo inválido: {name!r}")
        milestones: List[str] = []
        last_pct = -25
        try:
            with requests.post(
                f"{ollama_base()}/api/pull", json={"model": name}, stream=True,
                timeout=(10, OLLAMA_TOOLS_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = ev.get("status", "")
                    if "error" in ev:
                        raise ToolError(f"Pull falló: {ev['error']}")
                    completed, total = ev.get("completed"), ev.get("total")
                    if completed and total:
                        pct = int(completed * 100 / total)
                        if pct - last_pct >= 25:
                            last_pct = pct
                            milestones.append(f"{pct}% — {status}")
                    elif status and (not milestones or milestones[-1].split("— ")[-1] != status):
                        milestones.append(status)
                    if status == "success":
                        milestones.append("✓ descarga completada")
        except requests.RequestException as exc:
            raise ToolError(f"Pull no disponible: {exc}")
        return f"pull {name}:\n" + "\n".join(milestones[-12:])

    # ----------------------------- obsidian ---------------------------------

    def vault_search(self, query: str, max: int = 10) -> str:
        q = str(query or "").strip().lower()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        vault = os.environ.get("OTTERCODE_VAULT", "").strip()
        if not vault or not Path(vault).is_dir():
            raise ToolError(
                "Vault de Obsidian no configurado: exporta OTTERCODE_VAULT=/ruta/a/tu/vault "
                "antes de arrancar el backend."
            )
        try:
            n = max(1, min(int(max or 10), VAULT_MAX_HITS))
        except (TypeError, ValueError):
            n = VAULT_MAX_HITS
        hits: List[str] = []
        for md in sorted(Path(vault).rglob("*.md")):
            if ".obsidian" in md.parts or len(hits) >= n:
                continue
            try:
                for lineno, line in enumerate(
                    md.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if q in line.lower():
                        hits.append(f"{md.name}:{lineno}: {line.strip()[:160]}")
                        if len(hits) >= n:
                            break
            except OSError:
                continue
        return "\n".join(hits) if hits else f"(sin coincidencias para '{query}' en el vault)"

    # --------------------- claude-code: edición y búsqueda -------------------

    def edit_file(
        self,
        filepath: str,
        old_string: Optional[str] = None,
        new_string: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        new_content: Optional[str] = None,
        patch: Optional[str] = None,
    ) -> str:
        """Edición quirúrgica resiliente: soporta rangos de líneas, diffs unificados o reemplazo exacto/fuzzy."""
        if patch:
            return self.apply_patch(filepath, str(patch))
        target = self.resolve_safe(filepath)
        self._check_writable("edit_file")
        if not target.exists():
            raise ToolError(f"No existe el archivo a editar: {filepath}")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"No se pudo leer {filepath}: {exc}")

        lines = text.splitlines(keepends=True)
        replacement_text = new_content if new_content is not None else (new_string if new_string is not None else "")

        # 1. Modo rango de líneas (start_line y opcional end_line)
        if start_line is not None:
            try:
                s_idx = max(1, int(start_line)) - 1
                e_idx = max(s_idx + 1, int(end_line)) if end_line is not None else s_idx + 1
            except (TypeError, ValueError) as exc:
                raise ToolError(f"Rangos de línea inválidos: {exc}")

            if s_idx >= len(lines):
                raise ToolError(f"start_line {start_line} excede el número total de líneas ({len(lines)}).")
            
            rep_lines = replacement_text.splitlines(keepends=True)
            if replacement_text and not rep_lines[-1].endswith(("\n", "\r")):
                rep_lines[-1] += "\n"
            
            new_lines = lines[:s_idx] + rep_lines + lines[e_idx:]
            new_text = "".join(new_lines)
            target.write_text(new_text, encoding="utf-8")
            diff_lines = list(difflib.unified_diff(
                "".join(lines).splitlines(), new_text.splitlines(), lineterm="", n=2))
            return f"OK: líneas {start_line}-{e_idx} reemplazadas en {filepath}\n\n```diff\n" + "\n".join(diff_lines[:40]) + "\n```"

        # 2. Modo reemplazo de strings (exacto o fuzzy)
        old_str = str(old_string or "")
        if not old_str:
            raise ToolError("Se requiere 'old_string' o 'start_line' para editar el archivo.")

        count = text.count(old_str)
        if count == 1:
            new_text = text.replace(old_str, replacement_text, 1)
        elif count > 1:
            raise ToolError(
                f"'old_string' aparece {count} veces en {filepath}. Incluye más contexto o usa start_line/end_line."
            )
        else:
            # Normalización de saltos de línea y trailing whitespace
            norm_text = "\n".join(l.rstrip() for l in text.splitlines())
            norm_old = "\n".join(l.rstrip() for l in old_str.splitlines())
            if norm_old in norm_text:
                # Reemplazo sobre texto normalizado
                new_text = norm_text.replace(norm_old, replacement_text, 1)
            else:
                # Fuzzy matching para tolerar ligeras diferencias de indentación del LLM
                collapsed = re.sub(r"\s+", " ", norm_text)
                collapsed_old = re.sub(r"\s+", " ", norm_old)
                if collapsed_old and collapsed_old in collapsed:
                    # Reinyectar el bloque real más cercano (primera línea del old)
                    first = (norm_old.splitlines() or [""])[0].strip()
                    idx = next((i for i, ln in enumerate(text.splitlines()) if first and first in ln), -1)
                    if idx >= 0:
                        block = "\n".join(text.splitlines()[idx:idx + max(1, len(norm_old.splitlines()))])
                        new_text = text.replace(block, replacement_text, 1)
                    else:
                        new_text = None
                else:
                    new_text = None
                if new_text is None:
                    matcher = difflib.SequenceMatcher(None, norm_text, norm_old)
                    match = matcher.find_longest_match(0, len(norm_text), 0, len(norm_old))
                    thresh = max(12, int(len(norm_old) * 0.72))
                    if match.size >= thresh:
                        new_text = norm_text[:match.a] + replacement_text + norm_text[match.a + match.size:]
                    else:
                        snippet = "\n".join(text.splitlines()[:50])[:2500]
                        raise ToolError(
                            json.dumps({
                                "status": "error",
                                "reason": "old_string not found",
                                "path": filepath,
                                "file_snippet": snippet,
                                "hint": (
                                    "Copia old_string EXACTO de file_snippet (indentación y saltos) "
                                    "y reintenta edit_file. No uses write_file."
                                ),
                            }, ensure_ascii=False)
                        )

        target.write_text(new_text, encoding="utf-8")
        diff_lines = list(difflib.unified_diff(
            text.splitlines(), new_text.splitlines(),
            fromfile=f"a/{filepath}", tofile=f"b/{filepath}",
            lineterm="", n=2))
        diff_txt = "\n".join(diff_lines[:40])
        if len(diff_lines) > 40:
            diff_txt += "\n[…diff truncado…]"
        return f"OK: 1 reemplazo(s) en {filepath}\n\n```diff\n{diff_txt}\n```"


    def apply_patch(self, filepath: str, patch: str) -> str:
        """Aplica unified diff o bloques Aider SEARCH/REPLACE."""
        self._check_writable("apply_patch")
        raw = str(patch or "")
        if not raw.strip():
            raise ToolError("El campo 'patch' está vacío.")
        path = self._safe(filepath)
        existed = path.is_file()
        text = path.read_text(encoding="utf-8", errors="replace") if existed else ""
        new_text = None
        if "<<<<<<< SEARCH" in raw:
            blocks = re.findall(
                r"<<<<<<< SEARCH\n(.*?)=======\n(.*?)>>>>>>> REPLACE",
                raw, re.S,
            )
            if not blocks:
                raise ToolError("Bloques SEARCH/REPLACE inválidos.")
            new_text = text
            for search, repl in blocks:
                search = search.rstrip("\n")
                repl = repl.rstrip("\n")
                if not existed and not search.strip():
                    new_text = (new_text + ("\n" if new_text else "") + repl)
                    continue
                n = new_text.count(search)
                if n == 0:
                    raise ToolError(
                        f"SEARCH no encontrado en {filepath}. Contexto inicial:\n{text[:400]}"
                    )
                if n > 1:
                    raise ToolError(
                        f"SEARCH aparece {n} veces en {filepath}; no adivino. Añade contexto."
                    )
                new_text = new_text.replace(search, repl, 1)
        else:
            # unified diff: extrae líneas + / - del hunk
            plus, minus = [], []
            for line in raw.splitlines():
                if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
                    continue
                if line.startswith("+"):
                    plus.append(line[1:])
                elif line.startswith("-"):
                    minus.append(line[1:])
            old_chunk = "\n".join(minus)
            new_chunk = "\n".join(plus)
            if not existed and not old_chunk.strip():
                new_text = new_chunk
            elif old_chunk and old_chunk in text:
                if text.count(old_chunk) > 1:
                    raise ToolError("El hunk - aparece más de una vez; no adivino.")
                new_text = text.replace(old_chunk, new_chunk, 1)
            else:
                raise ToolError("No pude aplicar el unified diff (hunk no coincide).")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        diff_lines = list(difflib.unified_diff(
            text.splitlines(), new_text.splitlines(),
            fromfile=f"a/{filepath}", tofile=f"b/{filepath}", lineterm="", n=2,
        ))
        body = "\n".join(diff_lines[:80]) or "(sin cambios de línea)"
        return f"OK: patch aplicado a {filepath}\n\n```diff\n{body}\n```"

    def git_commit(self, message: str, paths: Any = None) -> str:
        msg = str(message or "").strip()
        if not msg:
            raise ToolError("message vacío.")
        low = msg.lower()
        if "--amend" in low or "push --force" in low or " -f" in f" {low}":
            raise ToolError("Denegado: amend / force no permitidos.")
        files = []
        if isinstance(paths, list):
            files = [str(p) for p in paths if str(p).strip()]
        if files:
            for f in files:
                self._safe(f)
            self._git("add", "--", *files)
        else:
            self._git("add", "-A")
        return self._git("commit", "-m", msg)

    def semantic_search(self, query: str, top_k: int = 4) -> str:
        """Búsqueda semántica usando embeddings vectoriales en sqlite-vec."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        try:
            k = max(1, min(int(top_k or 4), 20))
        except (TypeError, ValueError):
            k = 4
        hits = self.rag_store.search(q, limit=k)
        if not hits:
            return f"(sin coincidencias semánticas para '{q}')"
        out = [f"Resultados semánticos para '{q}':"]
        for idx, h in enumerate(hits, 1):
            out.append(f"{idx}. [{h['source']}] {h['filepath']} (distancia: {h.get('distance', 0)}):\n{h['content'][:500]}")
        return "\n\n".join(out)

    def index_workspace(self) -> str:
        """Indexa todos los archivos de código del workspace en la base de datos vectorial."""
        indexed_count = 0
        total_chunks = 0
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".cache"}
        valid_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md", ".sh", ".sql", ".rs", ".go"}
        for root, dirs, files in os.walk(self.workdir):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                p = Path(root) / f
                if p.suffix in valid_exts and p.is_file() and p.stat().st_size < 100_000:
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        rel = p.relative_to(self.workdir)
                        chunks = self.rag_store.index_file(str(rel), content, source="workspace")
                        indexed_count += 1
                        total_chunks += chunks
                    except Exception:
                        continue
        return f"OK: {indexed_count} archivos indexados ({total_chunks} fragmentos vectoriales listos para RAG)."

    _SKIP_DIRS = (".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".pytest_cache", ".cache", ".mypy_cache")

    def grep_search(self, pattern: str, path: str = ".", max: int = GREP_MAX_HITS) -> str:
        """Regex sobre archivos de texto del workspace (estilo grep -rn)."""
        pat = str(pattern or "").strip()
        if not pat:
            raise ToolError("El campo 'pattern' está vacío.")
        try:
            rx = re.compile(pat)
        except re.error as exc:
            raise ToolError(f"Regex inválida: {exc}")
        base = self.resolve_safe(path or ".")
        if not base.is_dir():
            raise ToolError(f"'{path}' no es un directorio del workspace.")
        try:
            n = max(1, min(int(max or GREP_MAX_HITS), GREP_MAX_HITS))
        except (TypeError, ValueError):
            n = GREP_MAX_HITS
        hits: List[str] = []
        for p in sorted(base.rglob("*")):
            if len(hits) >= n:
                break
            if not p.is_file() or p.is_symlink():
                continue
            if any(part in self._SKIP_DIRS for part in p.parts):
                continue
            try:
                if p.stat().st_size > 1_000_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "\x00" in text[:1024]:
                continue  # binario
            rel = p.relative_to(self.workdir)
            for lineno, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{lineno}: {line.strip()[:180]}")
                    if len(hits) >= n:
                        break
        return "\n".join(hits) if hits else f"(sin coincidencias para /{pat}/)"

    def _gitignore_specs(self) -> List[str]:
        specs: List[str] = []
        gi = self.workdir / ".gitignore"
        if gi.is_file():
            try:
                for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        specs.append(line.rstrip("/"))
            except OSError:
                pass
        return specs

    def _is_gitignored(self, p: Path) -> bool:
        import fnmatch
        try:
            rel = str(p.relative_to(self.workdir)).replace("\\", "/")
        except ValueError:
            return False
        for spec in self._gitignore_specs():
            if fnmatch.fnmatch(rel, spec) or fnmatch.fnmatch(p.name, spec) or fnmatch.fnmatch(rel, spec + "/*"):
                return True
            if any(fnmatch.fnmatch(part, spec) for part in Path(rel).parts):
                return True
        return False

    def glob_files(self, pattern: str = "**/*", max: int = GLOB_MAX_FILES) -> str:
        """Lista archivos por patrón glob relativo al workspace."""
        pat = str(pattern or "**/*").lstrip("/\\") or "**/*"
        try:
            n = max(1, min(int(max or GLOB_MAX_FILES), GLOB_MAX_FILES))
        except (TypeError, ValueError):
            n = GLOB_MAX_FILES
        out: List[str] = []
        try:
            matches = sorted(self.workdir.glob(pat))
        except (ValueError, NotImplementedError) as exc:
            raise ToolError(f"Patrón glob inválido: {exc}")
        for p in matches[:n * 2]:
            if len(out) >= n:
                break
            if p.is_symlink() or any(part in self._SKIP_DIRS for part in p.parts):
                continue
            if self._is_gitignored(p):
                continue
            if p.is_dir():
                out.append(f"📁 {p.relative_to(self.workdir)}/")
            elif p.is_file():
                out.append(f"📄 {p.relative_to(self.workdir)}  ({p.stat().st_size} B)")
        return "\n".join(out) if out else f"(sin coincidencias para '{pattern}')"

    # ----------------------------- plan de misión ----------------------------

    def todo_write(self, todos: Any) -> str:
        """Persiste el plan de la misión. Completado exige evidencia (test/archivo)."""
        self._check_writable("todo_write")
        items = todos if isinstance(todos, list) else []
        prev: List[Dict[str, Any]] = []
        p = self.workdir / TODO_FILE
        if p.exists():
            try:
                prev = json.loads(p.read_text(encoding="utf-8")) or []
            except (json.JSONDecodeError, OSError):
                prev = []
        prev_by = {str(it.get("content", "")): it for it in prev if isinstance(it, dict)}
        clean = []
        rejected = []
        for it in items[:50]:
            if isinstance(it, dict):
                content = str(it.get("content", ""))[:300]
                status = str(it.get("status", "pending")).lower().strip()
                if status in ("done", "complete", "completed", "completado", "x"):
                    status = "done"
                elif status in ("in_progress", "doing", "en_curso", "en curso", "~"):
                    status = "in_progress"
                else:
                    status = "pending"
                evidence = str(it.get("evidence") or it.get("verificacion") or "").strip()
                if status in ("done", "completed") and len(evidence) < 8 and prev_by.get(content):
                    old = prev_by.get(content) or {}
                    if str(old.get("status")) == "completed" and old.get("evidence"):
                        evidence = str(old.get("evidence"))
                    else:
                        rejected.append(content or "?")
                        status = "in_progress"
                        evidence = ""
                clean.append({
                    "content": content,
                    "status": status,
                    "agent": str(it.get("agent", "")),
                    "evidence": evidence[:500],
                })
            elif isinstance(it, str) and it.strip():
                clean.append({"content": it.strip()[:300], "status": "pending", "agent": "", "evidence": ""})
        p.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            from backend.runstate import append_checkpoint
            dummy = type("R", (), {"task_id": self.workdir.name, "start_agent": "", "files_report": []})()
            append_checkpoint(dummy, kind="todo", done=f"{len(clean)} ítems",
                              extra={"todos": clean})
        except Exception:
            pass
        msg = f"OK: plan guardado con {len(clean)} paso(s)."
        if rejected:
            msg += (" Completado RECHAZADO (sin evidencia de test/archivo): "
                    + ", ".join(rejected[:5]))
        return msg

    def todo_read(self) -> str:
        p = self.workdir / TODO_FILE
        if not p.exists():
            return "(plan vacío: usa todo_write para guardar pasos)"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "(plan corrupto)"
        lines = []
        for t in data:
            who = f"[{t['agent']}] " if t.get("agent") else ""
            st = str(t.get("status", "?"))
            if st == "completed":
                st = "done"
            lines.append(f"[{st}] {who}{t.get('content', '')}")
        return "\n".join(lines) if lines else "(plan vacío)"

    # --------------------------------- web API -------------------------------

    def http_request(self, url: str, method: str = "GET",
                     headers: Optional[Dict[str, Any]] = None,
                     body: Optional[str] = None) -> str:
        """Cliente REST completo contra una URL pública (guard SSRF activo)."""
        m = str(method or "GET").upper()
        if m not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise ToolError(f"Método no permitido: {m}")
        _assert_public_url(url)
        safe_headers = {
            str(k)[:120]: str(v)[:500]
            for k, v in (headers or {}).items()
            if str(k).lower() not in {"host", "content-length", "connection"}
        }
        req = urllib.request.Request(
            url, method=m,
            data=body.encode("utf-8") if isinstance(body, str) and m != "GET" else None,
            headers={"User-Agent": USER_AGENT, **safe_headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_REQ_TIMEOUT) as resp:
                status, ctype = resp.status, resp.headers.get("Content-Type", "")
                raw = resp.read(WEB_MAX_BYTES)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"{m} {url} falló: {type(exc).__name__}: {exc}")
        text = raw.decode("utf-8", errors="replace")
        if "json" in ctype.lower():
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        elif "html" in ctype.lower():
            text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = html_lib.unescape(re.sub(r"[ \t]+", " ", text))
        if len(text) > WEB_TEXT_CHARS:
            text = text[:WEB_TEXT_CHARS] + "\n… [truncado]"
        return f"HTTP {status} · {ctype or 'sin tipo'}\n\n{text.strip() or '(vacío)'}"

    def weather(self, city: str, days: int = 3) -> str:
        """Tiempo actual + previsión vía open-meteo (geocoding + forecast)."""
        q = str(city or "").strip()
        if not q:
            raise ToolError("Indica una ciudad.")
        geo_url = ("https://geocoding-api.open-meteo.com/v1/search?name="
                   + urllib.parse.quote(q) + "&count=1&language=es")
        try:
            geo = requests.get(geo_url, timeout=WEB_TIMEOUT,
                               headers={"User-Agent": USER_AGENT}).json()
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"Geocoding falló: {exc}")
        results = geo.get("results") or []
        if not results:
            raise ToolError(f"Ciudad no encontrada: {q}")
        g = results[0]
        try:
            n = max(1, min(int(days or 3), 7))
        except (TypeError, ValueError):
            n = 3
        fc_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={g['latitude']}"
            f"&longitude={g['longitude']}&current=temperature_2m,weather_code,wind_speed_10m"
            f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&forecast_days={n}&timezone=auto"
        )
        try:
            d = requests.get(fc_url, timeout=WEB_TIMEOUT,
                             headers={"User-Agent": USER_AGENT}).json()
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"Forecast no disponible: {exc}")
        cur = d.get("current", {})
        daily = d.get("daily", {})
        lines = [f"📍 {g['name']} ({g.get('country', '')}) — ahora: "
                 f"{cur.get('temperature_2m', '?')}°C, viento {cur.get('wind_speed_10m', '?')} km/h"]
        dates = daily.get("time", [])
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        rain = daily.get("precipitation_probability_max", [])
        for i, day in enumerate(dates):
            r = f", lluvia {rain[i]}%" if i < len(rain) else ""
            lines.append(f"  {day}: {tmin[i] if i < len(tmin) else '?'}–"
                         f"{tmax[i] if i < len(tmax) else '?'}°C{r}")
        return "\n".join(lines)

    def github_search(self, query: str, max: int = 5) -> str:
        """Búsqueda de repos públicos en GitHub (API sin auth, top N)."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        try:
            n = max(1, min(int(max or 5), 10))
        except (TypeError, ValueError):
            n = 5
        url = ("https://api.github.com/search/repositories?q="
               + urllib.parse.quote(q) + f"&per_page={n}&sort=stars")
        try:
            resp = requests.get(url, timeout=WEB_TIMEOUT,
                                headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"GitHub no respondió: {exc}")
        items = data.get("items") or []
        if not items:
            return f"(sin repos para '{q}')"
        out = [f"GitHub · {data.get('total_items') if isinstance(data.get('total_items'), int) else len(items)} resultados para '{q}':"]
        for it in items[:n]:
            desc = (it.get("description") or "")[:140]
            out.append(f"★{it.get('stargazers_count', 0)} · {it.get('full_name')} "
                       f"[{it.get('language') or '?'}] — {desc}\n   {it.get('html_url', '')}")
        return "\n".join(out)

    def stack_search(self, query: str, max: int = 5) -> str:
        """Búsqueda en Stack Overflow vía API pública de StackExchange."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        try:
            n = max(1, min(int(max or 5), 10))
        except (TypeError, ValueError):
            n = 5
        url = ("https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance"
               "&site=stackoverflow&pagesize=" + str(n) + "&q=" + urllib.parse.quote(q))
        try:
            resp = requests.get(url, timeout=WEB_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"StackExchange no respondió: {exc}")
        items = data.get("items") or []
        if not items:
            return f"(sin preguntas para '{q}')"
        out = [f"Stack Overflow · top {len(items[:n])}:"]
        for it in items[:n]:
            solved = "✓ resuelta" if it.get("is_answered") else "sin respuesta"
            tags = ",".join((it.get("tags") or [])[:4])
            out.append(f"[{it.get('score', 0)} pts · {solved}] {it.get('title', '')}\n"
                       f"   {it.get('link', '')}   ({tags})")
        return "\n".join(out)

    def npm_search(self, query: str, max: int = 5) -> str:
        """Búsqueda en el registro público de npm."""
        q = str(query or "").strip()
        if not q:
            raise ToolError("El campo 'query' está vacío.")
        try:
            n = max(1, min(int(max or 5), 10))
        except (TypeError, ValueError):
            n = 5
        url = ("https://registry.npmjs.org/-/v1/search?text="
               + urllib.parse.quote(q) + f"&size={n}")
        try:
            resp = requests.get(url, timeout=WEB_TIMEOUT,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"npm registry no respondió: {exc}")
        objs = data.get("objects") or []
        if not objs:
            return f"(sin paquetes npm para '{q}')"
        out = ["npm · resultados:"]
        for obj in objs[:n]:
            pkg = obj.get("package", {})
            out.append(f"{pkg.get('name')}@{pkg.get('version')} — "
                       f"{(pkg.get('description') or '')[:130]}\n"
                       f"   {pkg.get('links', {}).get('npm', '')}")
        return "\n".join(out)

    def pypi_info(self, name: str) -> str:
        """Ficha de un paquete publicado en PyPI."""
        pkg = str(name or "").strip().strip("/")
        if not pkg or not re.fullmatch(r"[A-Za-z0-9._\-]+", pkg):
            raise ToolError(f"Nombre de paquete inválido: {name!r}")
        try:
            resp = requests.get(f"https://pypi.org/pypi/{pkg}/json",
                                timeout=WEB_TIMEOUT, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            info = resp.json().get("info", {})
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"No se pudo consultar '{pkg}' en PyPI: {exc}")
        lines = [
            f"{info.get('name', pkg)} v{info.get('version', '?')}",
            f"Resumen : {(info.get('summary') or '')[:200]}",
            f"Autor   : {info.get('author') or info.get('maintainer') or '?'}",
            f"Home    : {info.get('home_page') or (info.get('project_urls') or {}).get('Homepage', '—')}",
            f"Licencia: {(info.get('license') or '?')[:80]}",
            f"Python  : {info.get('requires_python') or '—'}",
        ]
        deps = info.get("requires_dist") or []
        if deps:
            lines.append("Dependencias: " + ", ".join(deps[:8]) + ("…" if len(deps) > 8 else ""))
        return "\n".join(lines)

    # ------------------------------ utilidades -------------------------------

    def sys_info(self) -> str:
        du = shutil.disk_usage(self.workdir)
        mem_line = ""
        try:
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemAvailable"):
                        mem_line = f"RAM libre {int(line.split()[1]) // 1024} MB"
                        break
        except OSError:
            pass
        return (
            f"Sistema : {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Python  : {platform.python_version()} · PID {os.getpid()}\n"
            f"CPU     : {os.cpu_count()} núcleos · {platform.processor() or 'n/a'}"
            + (f"\nMemoria : {mem_line}" if mem_line else "") +
            f"\nDisco   : {du.free / 2**30:.1f} GB libres de {du.total / 2**30:.1f} GB\n"
            f"Workspace: {self.workdir}"
        )

    def hash_text(self, text: str) -> str:
        data = str(text or "").encode("utf-8")
        return (f"SHA-256: {hashlib.sha256(data).hexdigest()}\n"
                f"MD5    : {hashlib.md5(data).hexdigest()}\n"
                f"bytes  : {len(data)}")

    def base64_code(self, text: str, mode: str = "encode") -> str:
        mode = str(mode or "encode").lower()
        if mode == "encode":
            return base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
        if mode == "decode":
            try:
                return base64.b64decode(str(text or ""), validate=True).decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                raise ToolError(f"Base64 inválido: {exc}")
        raise ToolError("mode debe ser 'encode' o 'decode'.")

    def uuid_gen(self, count: int = 1) -> str:
        try:
            n = max(1, min(int(count or 1), 20))
        except (TypeError, ValueError):
            n = 1
        return "\n".join(str(uuid.uuid4()) for _ in range(n))

    # --------------------------- ollama: paridad -----------------------------

    def model_list(self) -> str:
        try:
            resp = requests.get(f"{ollama_base()}/api/tags", timeout=(5, 15))
            resp.raise_for_status()
            models = resp.json().get("models", [])
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"No se pudo listar modelos: {exc}")
        if not models:
            return "(no hay modelos instalados)"
        lines = []
        for m in models:
            size_gb = (m.get("size") or 0) / 2**30
            lines.append(f"• {m.get('name', '?')}  ({size_gb:.1f} GB)")
        return f"Modelos instalados ({len(models)}):\n" + "\n".join(lines)

    def ollama_consult(self, model: str, prompt: str) -> str:
        """Segunda opinión: pregunta a otro modelo local (/api/chat, sin stream)."""
        m = str(model or "").strip()
        p = str(prompt or "").strip()
        if not m or not p:
            raise ToolError("Se requieren 'model' y 'prompt'.")
        if len(p) > 8000:
            p = p[:8000]
        try:
            resp = requests.post(
                f"{ollama_base()}/api/chat",
                json={"model": m, "stream": False,
                      "messages": [{"role": "user", "content": p}]},
                timeout=(10, CONSULT_TIMEOUT),
            )
            resp.raise_for_status()
            try:
                reply = (resp.json().get("message") or {}).get("content", "")
            except ValueError:
                # algunos servidores ignoran stream:false y responden NDJSON
                parts = []
                for line in resp.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    parts.append((obj.get("message") or {}).get("content", ""))
                reply = "".join(parts)
        except (requests.RequestException, ValueError) as exc:
            raise ToolError(f"Consulta a '{m}' falló: {exc}")
        reply = reply.strip()
        if not reply:
            raise ToolError(f"'{m}' respondió vacío.")
        if len(reply) > WEB_TEXT_CHARS:
            reply = reply[:WEB_TEXT_CHARS] + "\n… [truncado]"
        return f"[{m} opina]:\n{reply}"

    # ------------------------- obsidian: lectura/escritura --------------------

    def _vault_root(self) -> Path:
        vault = os.environ.get("OTTERCODE_VAULT", "").strip()
        if not vault or not Path(vault).is_dir():
            raise ToolError(
                "Vault de Obsidian no configurado. Configúralo desde la UI "
                "(tab Obsidian → Conectar vault) o exporta OTTERCODE_VAULT."
            )
        root = Path(vault).resolve()
        if os.access(root, os.R_OK) is False:
            raise ToolError(f"Sin permisos de lectura sobre el vault: {root}")
        return root

    @staticmethod
    def _vault_target(root: Path, path: str) -> Path:
        rel = str(path or "").strip().lstrip("/\\")
        if not rel or ".." in Path(rel).parts:
            raise ToolError("Ruta de nota inválida (usa rutas relativas al vault).")
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)):
            raise ToolError("Ruta fuera del vault bloqueada.")
        return target

    def vault_read(self, path: str) -> str:
        target = self._vault_target(self._vault_root(), path)
        if not target.is_file():
            raise ToolError(f"La nota no existe: {path}")
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"No se pudo leer la nota: {exc}")
        if len(text) > WEB_TEXT_CHARS:
            text = text[:WEB_TEXT_CHARS] + "\n… [nota truncada]"
        return text

    def vault_write(self, path: str, content: str) -> str:
        self._check_writable("vault_write")
        root = self._vault_root()
        target = self._vault_target(root, path)
        if target.suffix.lower() != ".md":
            target = target.with_suffix(".md")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            target.write_text(str(content or ""), encoding="utf-8")
        except OSError as exc:
            raise ToolError(f"No se pudo escribir la nota: {exc}")
        verb = "actualizada" if existed else "creada"
        return f"OK: nota {verb} en el vault → {target.relative_to(root)} ({len(content or '')} caracteres)"

    # ----------------------------- despacho --------------------------------

    def dispatch(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Ejecuta `tool` con `args`. Nunca lanza excepciones: devuelve ok/output."""
        started = time.time()
        canonical = resolve_name(tool)
        try:
            if canonical == "read_file":
                try:
                    _off = int(args.get("offset") or 1)
                except (TypeError, ValueError):
                    _off = 1
                output = self.read_file(
                    args.get("filepath", ""),
                    offset=_off,
                    limit=args.get("limit") or args.get("max_lines"),
                )
            elif canonical == "write_file":
                output = self.write_file(args.get("filepath", ""), args.get("content", ""))
            elif canonical == "append_file":
                output = self.append_file(args.get("filepath", ""), args.get("content", ""))
            elif canonical == "mkdir":
                output = self.mkdir(args.get("path", ""))
            elif canonical == "list_dir":
                output = self.list_dir(args.get("path", "."))
            elif canonical == "tree":
                output = self.tree(args.get("path", "."), args.get("max_depth", 3))
            elif canonical == "execute_bash":
                output = self.execute_bash(args.get("cmd", ""))
            elif canonical == "python_exec":
                output = self.python_exec(args.get("code", ""))
            elif canonical == "git_status":
                output = self.git_status()
            elif canonical == "git_diff":
                output = self.git_diff(bool(args.get("staged", False)))
            elif canonical == "git_log":
                output = self.git_log(args.get("max", 20))
            elif canonical == "web_fetch":
                output = self.web_fetch(args.get("url", ""))
            elif canonical == "web_search":
                output = self.web_search(args.get("query", ""))
            elif canonical == "wikipedia_search":
                output = self.wikipedia_search(args.get("query", ""), args.get("lang", "es"))
            elif canonical == "arxiv_search":
                output = self.arxiv_search(args.get("query", ""), args.get("max", 5))
            elif canonical == "youtube_transcript":
                output = self.youtube_transcript(args.get("url", ""))
            elif canonical == "sqlite_query":
                output = self.sqlite_query(args.get("db", ""), args.get("sql", ""))
            elif canonical == "csv_peek":
                output = self.csv_peek(args.get("filepath", ""), args.get("rows", 10))
            elif canonical == "json_query":
                output = self.json_query(args.get("filepath", ""), args.get("path", ""))
            elif canonical == "memory_save":
                output = self.memory_save(args.get("key", ""), args.get("value", ""))
            elif canonical == "memory_recall":
                output = self.memory_recall(args.get("key", ""))
            elif canonical == "image_describe":
                output = self.image_describe(
                    args.get("filepath", ""), args.get("model", "llava"), args.get("prompt", "")
                )
            elif canonical == "embed_text":
                output = self.embed_text(args.get("text", ""), args.get("model", "nomic-embed-text"))
            elif canonical == "model_info":
                output = self.model_info(args.get("model", ""))
            elif canonical == "model_pull":
                output = self.model_pull(args.get("model", ""))
            elif canonical == "vault_search":
                output = self.vault_search(args.get("query", ""), args.get("max", 10))
            elif canonical == "vault_read":
                output = self.vault_read(args.get("path", ""))
            elif canonical == "vault_write":
                output = self.vault_write(args.get("path", ""), args.get("content", ""))
            elif canonical == "apply_patch":
                output = self.apply_patch(args.get("filepath", ""), args.get("patch", ""))
            elif canonical == "git_commit":
                output = self.git_commit(args.get("message", ""), args.get("paths"))
            elif canonical == "edit_file":
                output = self.edit_file(
                    filepath=args.get("filepath", ""),
                    old_string=args.get("old_string"),
                    new_string=args.get("new_string"),
                    start_line=args.get("start_line"),
                    end_line=args.get("end_line"),
                    new_content=args.get("new_content") or args.get("content"),
                    patch=args.get("patch"),
                )
            elif canonical == "semantic_search":
                output = self.semantic_search(args.get("query", ""), args.get("top_k", 4))
            elif canonical == "index_workspace":
                output = self.index_workspace()
            elif canonical.startswith("mcp__") and mcp_client.mcp_loop.is_ready():
                output = mcp_client.mcp_loop.call_tool_sync(canonical, args)
            elif canonical == "grep_search":
                output = self.grep_search(args.get("pattern", ""), args.get("path", "."),
                                          args.get("max", GREP_MAX_HITS))
            elif canonical == "glob_files":
                output = self.glob_files(args.get("pattern", "**/*"), args.get("max", GLOB_MAX_FILES))
            elif canonical == "todo_write":
                output = self.todo_write(args.get("todos"))
            elif canonical == "todo_read":
                output = self.todo_read()
            elif canonical == "http_request":
                output = self.http_request(args.get("url", ""), args.get("method", "GET"),
                                           args.get("headers") or {}, args.get("body"))
            elif canonical == "weather":
                output = self.weather(args.get("city", ""), args.get("days", 3))
            elif canonical == "github_search":
                output = self.github_search(args.get("query", ""), args.get("max", 5))
            elif canonical == "stack_search":
                output = self.stack_search(args.get("query", ""), args.get("max", 5))
            elif canonical == "npm_search":
                output = self.npm_search(args.get("query", ""), args.get("max", 5))
            elif canonical == "pypi_info":
                output = self.pypi_info(args.get("name", ""))
            elif canonical == "sys_info":
                output = self.sys_info()
            elif canonical == "hash_text":
                output = self.hash_text(args.get("text", ""))
            elif canonical == "base64_code":
                output = self.base64_code(args.get("text", ""), args.get("mode", "encode"))
            elif canonical == "uuid_gen":
                output = self.uuid_gen(args.get("count", 1))
            elif canonical == "model_list":
                output = self.model_list()
            elif canonical == "ollama_consult":
                output = self.ollama_consult(args.get("model", ""), args.get("prompt", ""))
            elif canonical == "memory":
                from backend.memory_md import memory_tool
                output = json.dumps(memory_tool(
                    str(args.get("action") or "add"),
                    str(args.get("target") or "memory"),
                    str(args.get("text") or ""),
                    str(args.get("old_text") or ""),
                ), ensure_ascii=False)
            elif canonical == "use_skill":
                from backend.skill_creator import list_home_skills
                name = str(args.get("name") or "")
                hit = next((s for s in list_home_skills() if s["name"].lower() == name.lower()), None)
                output = (hit.get("body") if hit and hit.get("enabled") else f"skill '{name}' no disponible")
            elif canonical == "cronjob":
                from backend.cron import jobs as CJ
                from backend.cron.scheduler import run_now
                act = str(args.get("action") or "list").lower()
                ident = str(args.get("id") or args.get("name") or "")
                if act == "list":
                    output = json.dumps(CJ.list_jobs(), ensure_ascii=False)[:8000]
                elif act == "create":
                    output = json.dumps(CJ.create_job(args, from_agent=True), ensure_ascii=False)
                elif act == "pause":
                    output = json.dumps(CJ.pause_job(ident))
                elif act == "resume":
                    output = json.dumps(CJ.resume_job(ident))
                elif act == "remove":
                    output = json.dumps(CJ.remove_job(ident))
                elif act == "run":
                    output = json.dumps(run_now(ident))
                elif act == "update":
                    output = json.dumps(CJ.update_job(ident, args))
                else:
                    output = "acción cron desconocida"
            elif canonical == "delegate_task":
                from backend.subagents import delegate_task
                output = json.dumps(delegate_task(
                    str(args.get("task") or ""),
                    str(args.get("context") or ""),
                    str(args.get("model") or ""),
                    list(args.get("skills") or []),
                    list(args.get("tools") or []),
                    int(args.get("timeout_seconds") or 300),
                ), ensure_ascii=False)
            elif canonical == "session_search":
                from backend.db import session_search
                output = json.dumps(session_search(
                    str(args.get("query") or ""),
                    int(args.get("limit") or 20),
                    args.get("session_id"),
                ), ensure_ascii=False)[:8000]
            else:
                return self._result(
                    False,
                    f"Herramienta desconocida: {tool!r}. Válidas: {', '.join(TOOL_NAMES)} o finalizar.",
                    started,
                )
            return self._result(True, output, started)
        except ToolError as exc:
            return self._result(False, str(exc), started)
        except Exception as exc:  # noqa: BLE001 — el modelo debe ver el fallo
            return self._result(False, f"Excepción no controlada: {exc}", started)

    @staticmethod
    def _result(ok: bool, output: str, started: float) -> Dict[str, Any]:
        return {"ok": ok, "output": output, "ms": int((time.time() - started) * 1000)}

    # --------------------------- inspección --------------------------------

    def list_workspace(self) -> List[Dict[str, Any]]:
        """Lista los archivos del workspace (relativos) con su tamaño."""
        files: List[Dict[str, Any]] = []
        skip_parts = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}
        for path in sorted(self.workdir.rglob("*")):
            if not path.is_file() or path.name == "ottercode_transcript.json":
                continue
            if any(part in skip_parts for part in path.parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = str(path.relative_to(self.workdir))
            base = path.name
            if base.startswith(".otter") or base == "ottercode_transcript.json":
                continue
            files.append({"path": rel, "size": size})
        return files

    def read_workspace_for_review(
        self, per_file_limit: int = 30_000, total_limit: int = 90_000
    ) -> str:
        """Concatena el contenido del workspace para el prompt del Revisor."""
        chunks: List[str] = []
        used = 0
        for path in sorted(self.workdir.rglob("*")):
            if not path.is_file() or path.name == "ottercode_transcript.json":
                continue
            rel = path.relative_to(self.workdir)
            try:
                data = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(data) > per_file_limit:
                data = data[:per_file_limit] + "\n… [archivo truncado]"
            block = f"=== ARCHIVO: {rel} ===\n{data}\n"
            if used + len(block) > total_limit:
                chunks.append("… [workspace truncado por límite de contexto]")
                break
            chunks.append(block)
            used += len(block)
        return "\n".join(chunks)


# ---------------------------------------------------------------------------
# FASE 6 · Carga dinámica de skills externas (~/.ottercode/skills/)
# ---------------------------------------------------------------------------

EXTERNAL_SKILLS_DIRS: List[Path] = [
    Path.home() / ".ottercode" / "skills",
    Path.home() / ".claude" / "skills",
]

_EXTERNAL_SKILLS: Dict[str, Dict[str, Any]] = {}


def load_external_skills() -> Dict[str, Dict[str, Any]]:
    """Lee todas las skills externas de ~/.ottercode/skills/<name>/SKILL.md."""
    global _EXTERNAL_SKILLS
    discovered: Dict[str, Dict[str, Any]] = {}
    for base in EXTERNAL_SKILLS_DIRS:
        if not base.is_dir():
            continue
        for folder in base.iterdir():
            if not folder.is_dir():
                continue
            skill_md = folder / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8", errors="replace")
                meta: Dict[str, Any] = {"name": folder.name, "folder": str(folder), "path": str(skill_md)}
                desc = ""
                # Parseo liviano de metadatos frontmatter o cabeceras
                for line in content.splitlines():
                    l = line.strip()
                    if l.lower().startswith("name:"):
                        meta["name"] = l.split(":", 1)[1].strip()
                    elif l.lower().startswith("description:"):
                        desc = l.split(":", 1)[1].strip()
                    elif l.lower().startswith("category:"):
                        meta["cat"] = l.split(":", 1)[1].strip()
                if not desc:
                    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
                    desc = lines[0] if lines else f"Skill externa {folder.name}"
                meta["desc"] = desc
                meta["cat"] = meta.get("cat", "External")
                meta["writes_fs"] = False
                meta["example"] = f'{{"tool": "{meta["name"]}", "arguments": {{}}}}'
                
                # Buscar ejecutable si existe (main.py, run.sh, etc)
                entrypoint = None
                for fname in ("run.sh", "main.py", "execute.sh", "script.py"):
                    ep = folder / fname
                    if ep.is_file():
                        entrypoint = ep
                        break
                meta["entrypoint"] = str(entrypoint) if entrypoint else None
                
                name = meta["name"]
                discovered[name] = meta
                TOOLS[name] = {
                    "cat": meta["cat"],
                    "writes_fs": meta["writes_fs"],
                    "desc": meta["desc"],
                    "example": meta["example"],
                    "external": True,
                    "entrypoint": meta["entrypoint"],
                }
            except Exception:
                continue
    _EXTERNAL_SKILLS = discovered
    return _EXTERNAL_SKILLS

# Cargar skills al importar el módulo
load_external_skills()


if __name__ == "__main__":  # sanity check manual
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ex = ToolExecutor(tmp)
        print(ex.dispatch("mkdir", {"path": "src/utils"}))
        print(ex.dispatch("write_file", {"filepath": "src/app.py", "content": "print('otter')"}))
        print(ex.dispatch("tree", {"path": "."}))
        print(ex.dispatch("list_dir", {"path": "src"}))
        print(ex.dispatch("execute_bash", {"cmd": "python3 -m py_compile src/app.py && echo COMPILE-OK"}))
        print(ex.dispatch("execute_bash", {"cmd": "rm -rf /"}))
        print(ex.dispatch("python_exec", {"code": "print(sum(range(10)))"}))
        print(ex.dispatch("memory_save", {"key": "decision", "value": "usar sqlite"}))
        print(ex.dispatch("memory_recall", {"key": "decision"}))
        print(ex.dispatch("write_file", {"filepath": "datos.csv", "content": "a,b\n1,2\n3,4"}))
        print(ex.dispatch("csv_peek", {"filepath": "datos.csv"}))
        print(ex.dispatch("write_file", {"filepath": "conf.json", "content": '{"servidor": {"puerto": 8000}}'}))
        print(ex.dispatch("json_query", {"filepath": "conf.json", "path": "servidor.puerto"}))
        print(ex.dispatch("web_fetch", {"url": "http://localhost:11434"}))   # debe BLOQUEAR (SSRF)
        ro = ToolExecutor(tmp, readonly=True)
        print(ro.dispatch("write_file", {"filepath": "x.txt", "content": "x"}))
        print(ro.dispatch("python_exec", {"code": "print(1)"}))

def _prop(typ: str, desc: str = "", **extra: Any) -> Dict[str, Any]:
    d: Dict[str, Any] = {"type": typ}
    if desc:
        d["description"] = desc
    d.update(extra)
    return d


_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "read_file": {
        "properties": {
            "filepath": _prop("string", "Ruta relativa"),
            "offset": _prop("integer", "Primera línea (1-based)"),
            "limit": _prop("integer", "Máximo de líneas"),
        },
        "required": ["filepath"],
    },
    "write_file": {
        "properties": {
            "filepath": _prop("string"),
            "content": _prop("string", "Contenido completo (solo archivos NUEVOS cortos)"),
        },
        "required": ["filepath", "content"],
    },
    "append_file": {
        "properties": {"filepath": _prop("string"), "content": _prop("string")},
        "required": ["filepath", "content"],
    },
    "edit_file": {
        "properties": {
            "filepath": _prop("string"),
            "old_string": _prop("string", "Texto exacto a reemplazar (único)"),
            "new_string": _prop("string"),
        },
        "required": ["filepath", "old_string", "new_string"],
    },
    "apply_patch": {
        "properties": {
            "filepath": _prop("string"),
            "patch": _prop("string", "Unified diff o bloques SEARCH/REPLACE"),
        },
        "required": ["filepath", "patch"],
    },
    "mkdir": {"properties": {"path": _prop("string")}, "required": ["path"]},
    "list_dir": {"properties": {"path": _prop("string")}, "required": []},
    "tree": {
        "properties": {"path": _prop("string"), "max_depth": _prop("integer")},
        "required": [],
    },
    "grep_search": {
        "properties": {"pattern": _prop("string"), "path": _prop("string")},
        "required": ["pattern"],
    },
    "glob_files": {
        "properties": {"pattern": _prop("string")},
        "required": ["pattern"],
    },
    "execute_bash": {
        "properties": {"cmd": _prop("string", "Comando en el workdir")},
        "required": ["cmd"],
    },
    "python_exec": {
        "properties": {"code": _prop("string")},
        "required": ["code"],
    },
    "git_status": {"properties": {}, "required": []},
    "git_diff": {
        "properties": {"staged": _prop("boolean")},
        "required": [],
    },
    "git_log": {
        "properties": {"max": _prop("integer")},
        "required": [],
    },
    "git_commit": {
        "properties": {
            "message": _prop("string"),
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["message"],
    },
    "todo_write": {
        "properties": {
            "todos": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Lista {content, status, agent, evidence}",
            },
        },
        "required": ["todos"],
    },
    "todo_read": {"properties": {}, "required": []},
    "semantic_search": {
        "properties": {"query": _prop("string"), "top_k": _prop("integer")},
        "required": ["query"],
    },
    "index_workspace": {"properties": {}, "required": []},
    "web_search": {
        "properties": {"query": _prop("string")},
        "required": ["query"],
    },
    "web_fetch": {
        "properties": {"url": _prop("string")},
        "required": ["url"],
    },
    "finalizar": {
        "properties": {"resumen": _prop("string")},
        "required": [],
    },
}


def get_ollama_tools(allowed: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Schemas OpenAI/Ollama. Si `allowed` se pasa, SOLO esas tools + finalizar."""
    ollama_tools: List[Dict[str, Any]] = []
    if allowed is None:
        names = list(TOOLS.keys())
    else:
        names = []
        seen = set()
        for n in allowed:
            n = resolve_name(str(n or "").strip())
            if n and n not in seen:
                seen.add(n)
                names.append(n)
        if "finalizar" not in seen:
            names.append("finalizar")
    for name in names:
        info = TOOLS.get(name) or {}
        schema = _TOOL_SCHEMAS.get(name) or {}
        props = dict(schema.get("properties") or {})
        required = list(schema.get("required") or [])
        desc = info.get("desc") or name
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    try:
        if mcp_client.mcp_loop.is_ready():
            mgr = mcp_client.get_manager()
            extra = getattr(mgr, "openai_tools", None) or getattr(mgr, "list_openai_tools", None)
            if callable(extra):
                extra = extra()
            if isinstance(extra, list):
                ollama_tools.extend(extra)
    except Exception:
        pass
    return ollama_tools
