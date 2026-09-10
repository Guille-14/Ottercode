"""Parser de schedules estilo Hermes + croniter si está instalado."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

_IN = re.compile(r"^in\s+(\d+)\s*(s|sec|m|min|h|hr|d|day)s?$", re.I)
_EVERY = re.compile(r"^every\s+(\d+)\s*(s|m|h|d)s?$", re.I)
_EVERY_WD = re.compile(
    r"^every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekday|weekdays)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.I,
)
_AT = re.compile(r"^(weekdays)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)
_WD = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _unit(u: str) -> int:
    u = u.lower()
    if u.startswith("s"):
        return 1
    if u.startswith("m"):
        return 60
    if u.startswith("h"):
        return 3600
    return 86400


def parse_schedule(expr: str, now: Optional[datetime] = None) -> Tuple[str, datetime, bool]:
    """Devuelve (kind, next_run, recurring)."""
    now = now or datetime.now(timezone.utc)
    s = (expr or "").strip()
    if not s:
        raise ValueError("schedule vacío")
    m = _IN.match(s) or re.match(r"^(\d+)\s*(s|m|h|d)s?$", s, re.I)
    if m:
        n = int(m.group(1))
        sec = n * _unit(m.group(2))
        return ("once", now + timedelta(seconds=sec), False)
    m = _EVERY.match(s)
    if m:
        sec = int(m.group(1)) * _unit(m.group(2))
        return ("every", now + timedelta(seconds=sec), True)
    m = _EVERY_WD.match(s) or _AT.match(s)
    if m:
        day = m.group(1).lower()
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        ap = (m.group(4) or "").lower()
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        nxt = _next_weekday(now, day, hour, minute)
        return ("weekly", nxt, True)
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return ("once", dt, False)
    # cron 5 fields
    if len(s.split()) == 5:
        nxt = _croniter_next(s, now)
        return ("cron", nxt, True)
    raise ValueError(f"schedule no reconocido: {s!r}")


def next_run_at(expr: str, now: Optional[datetime] = None) -> datetime:
    return parse_schedule(expr, now)[1]


def _next_weekday(now: datetime, day: str, hour: int, minute: int) -> datetime:
    cur = now.astimezone(timezone.utc)
    if day in ("weekday", "weekdays"):
        for add in range(0, 8):
            d = cur + timedelta(days=add)
            if d.weekday() < 5:
                cand = d.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if cand > cur:
                    return cand
        return cur + timedelta(days=1)
    want = _WD.get(day, 0)
    add = (want - cur.weekday()) % 7
    cand = (cur + timedelta(days=add)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand <= cur:
        cand += timedelta(days=7)
    return cand


def _croniter_next(expr: str, now: datetime) -> datetime:
    try:
        from croniter import croniter  # type: ignore
        it = croniter(expr, now)
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt
    except Exception:
        return now + timedelta(hours=1)


def is_recurring(expr: str) -> bool:
    try:
        return parse_schedule(expr)[2]
    except ValueError:
        return False
