"""Redacta secretos y rutas sensibles en el streaming hacia el frontend."""
from __future__ import annotations

import os
import re
from typing import Pattern

_PATTERNS: list[Pattern[str]] = [
    re.compile(r"(?i)(sk-[A-Za-z0-9]{20,})"),
    re.compile(r"(?i)(ghp_[A-Za-z0-9]{20,})"),
    re.compile(r"(?i)(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?i)(AKIA[0-9A-Z]{16})"),
    re.compile(r"(?i)(xox[baprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"(?i)(-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)"),
]


def _token_pat() -> Pattern[str] | None:
    tok = (os.environ.get("OTTERCODE_TOKEN") or "").strip()
    if len(tok) >= 8:
        return re.compile(re.escape(tok))
    return None


def redact_text(text: str) -> str:
    if not text:
        return text
    out = text
    for rx in _PATTERNS:
        out = rx.sub("[REDACTED]", out)
    tp = _token_pat()
    if tp:
        out = tp.sub("[REDACTED]", out)
    out = re.sub(r"(?i)(/home/[^/\s]+/\.ssh/[^\s]+)", "[REDACTED_PATH]", out)
    return out
