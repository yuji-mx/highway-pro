"""単位と国別の表記（内部は SI。表示・入力の換算と、米国の路線番号・出口番号の扱い）。"""

from __future__ import annotations

import re
from typing import Any

MPH_TO_KMH = 1.609344
M_TO_FT = 1.0 / 0.3048
KM_TO_MI = 1.0 / 1.609344

_SPEED = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$", re.IGNORECASE)
_ROUTE = re.compile(
    r"^(I|US|SR|M|CA|NY|TX|FL|PA|OH|IL|IN|WI|MN|KY|TN|GA|NC|VA|NJ|MD|CO)[\s\-]?(\d+[A-Z]?)\b(.*)$",
    re.IGNORECASE,
)
_EXIT_BASE = re.compile(r"^\s*(\d+)")


def parse_speed_kmh(v: Any) -> int | None:
    """OSM の maxspeed を km/h の整数にする（"70 mph" → 113、"100" → 100、"none"/"signals" → None）。"""
    if v is None:
        return None
    s = str(v).split(";")[0]
    m = _SPEED.match(s)
    if not m:
        return None
    val = float(m.group(1))
    if (m.group(2) or "").lower() == "mph":
        val *= MPH_TO_KMH
    return round(val)


def parse_int(v: Any) -> int | None:
    try:
        return int(str(v).split(";")[0].split()[0])
    except (ValueError, IndexError, TypeError):
        return None


def normalize_route_ref(ref: str) -> str:
    """米国の路線番号の表記ゆれを統一する（"I-94" / "I94" / "I 94" → "I 94"）。日本の ref はそのまま。"""
    tok = ref.strip()
    m = _ROUTE.match(tok)
    if m:
        return f"{m.group(1).upper()} {m.group(2).upper()}{m.group(3)}".strip()
    return tok


def pretty_route(ref: str | None) -> str:
    """表示用（"I 94" → "I-94"、"US 23" → "US-23"）。"""
    if not ref:
        return ""
    m = _ROUTE.match(ref)
    return f"{m.group(1).upper()}-{m.group(2).upper()}{m.group(3)}" if m else ref


def route_number(ref: str | None) -> int | None:
    m = _ROUTE.match(ref or "")
    if not m:
        return None
    d = re.match(r"\d+", m.group(2))
    return int(d.group(0)) if d else None


def exit_base(ref: str | None) -> str | None:
    """出口番号の基本番号（"180A" / "180A-B" / "180B" → "180"）。数字で始まらない場合はそのまま。"""
    if not ref:
        return None
    m = _EXIT_BASE.match(str(ref))
    return m.group(1) if m else str(ref).strip()
