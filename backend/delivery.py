"""Entrega multi-plataforma con retry/backoff. Credenciales de vault/env."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from backend.home import HOME, ensure_home


def _secrets() -> Dict[str, Any]:
    p = Path(".otter_vault.json")
    if not p.is_file():
        p = HOME / "vault.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def deliver(target: str, text: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t = (target or "local").strip()
    body = text or ""
    last_err = ""
    for attempt in range(3):
        try:
            if t in ("origin", "local", ""):
                return _local(body, meta)
            if t.startswith("telegram:"):
                return _http_json(
                    f"https://api.telegram.org/bot{_cred('TELEGRAM_BOT_TOKEN')}/sendMessage",
                    {"chat_id": t.split(":", 1)[1], "text": body[:3900]},
                )
            if t.startswith("discord:"):
                return _http_json(
                    f"https://discord.com/api/v10/channels/{t.split(':',1)[1]}/messages",
                    {"content": body[:1900]},
                    headers={"Authorization": f"Bot {_cred('DISCORD_BOT_TOKEN')}"},
                )
            if t.startswith("slack:"):
                return _http_json(
                    "https://slack.com/api/chat.postMessage",
                    {"channel": t.split(":", 1)[1], "text": body[:3900]},
                    headers={"Authorization": f"Bearer {_cred('SLACK_BOT_TOKEN')}"},
                )
            if t.startswith("webhook:"):
                return _http_json(t.split(":", 1)[1], {"text": body, "meta": meta or {}})
            if t.startswith("email:"):
                return _local(f"email→{t}\n{body}", meta)
            if t.startswith(("whatsapp:", "signal:")):
                return _local(f"{t}\n{body}", meta)
            return _local(body, meta)
        except Exception as exc:
            last_err = str(exc)
            time.sleep(0.4 * (2 ** attempt))
    return {"ok": False, "error": last_err}


def _cred(name: str) -> str:
    sec = _secrets()
    return str(sec.get(name) or os.environ.get(name) or "")


def _local(text: str, meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ensure_home()
    d = HOME / "cron" / "delivered"
    d.mkdir(parents=True, exist_ok=True)
    name = (meta or {}).get("job") or "msg"
    p = d / f"{name}.md"
    p.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(p)}


def _http_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "OtterCode/3.0", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return {"ok": True, "status": resp.status, "body": resp.read()[:500].decode("utf-8", "replace")}
