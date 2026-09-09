"""STT/TTS configurable (whisper local / edge-tts). Sin romper si faltan deps."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

from backend.home import cfg_get


def transcribe(data: bytes, filename: str = "audio.wav") -> Dict[str, Any]:
    backend = str(cfg_get("voice", "stt_backend", default="whisper_local") or "whisper_local")
    if backend == "openai":
        return {"ok": False, "error": "openai stt no configurado (usar whisper_local)"}
    # whisper.cpp o whisper CLI
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".wav", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        for cmd in (
            ["whisper", path, "--language", "es", "--output_format", "txt"],
            ["whisper-cpp", "-f", path],
        ):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if proc.returncode == 0:
                    txt = (proc.stdout or "").strip()
                    return {"ok": True, "text": txt, "backend": cmd[0]}
            except FileNotFoundError:
                continue
        return {"ok": False, "error": "whisper no instalado"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def synthesize(text: str) -> Dict[str, Any]:
    voice = str(cfg_get("voice", "tts_voice", default="es-ES-ElviraNeural") or "es-ES-ElviraNeural")
    backend = str(cfg_get("voice", "tts_backend", default="edge_tts") or "edge_tts")
    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    out.close()
    if backend == "edge_tts":
        try:
            proc = subprocess.run(
                ["edge-tts", "--voice", voice, "--text", text[:1500], "--write-media", out.name],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0 and Path(out.name).stat().st_size > 0:
                return {"ok": True, "path": out.name, "mime": "audio/mpeg"}
        except FileNotFoundError:
            pass
    # espeak fallback
    try:
        wav = out.name.replace(".mp3", ".wav")
        subprocess.run(["espeak", "-v", "es", "-w", wav, text[:500]], check=False, timeout=30)
        if Path(wav).is_file():
            return {"ok": True, "path": wav, "mime": "audio/wav"}
    except FileNotFoundError:
        pass
    return {"ok": False, "error": "no tts backend"}
