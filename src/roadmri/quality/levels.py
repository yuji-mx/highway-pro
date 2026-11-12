"""品質レベル（§4.10.1）。high / medium / low / unusable。"""

from __future__ import annotations

from typing import Any

LEVELS = ("unusable", "low", "medium", "high")
LEVEL_RANK = {k: i for i, k in enumerate(LEVELS)}


def dem_quality(low_conf_ratio: float | None, cfg: dict[str, Any]) -> str:
    q = cfg["quality"]
    if low_conf_ratio is None:
        return "low"
    if low_conf_ratio <= q["dem_low_ratio_high"]:
        return "high"
    if low_conf_ratio <= q["dem_low_ratio_medium"]:
        return "medium"
    return "low"


def osm_quality_ramp(lanes: Any, maxspeed: Any, carriageway: str) -> str:
    missing = sum(v is None for v in (lanes, maxspeed))
    if carriageway == "unknown" and missing == 2:
        return "low"
    return "high" if missing == 0 else "medium"


def level_from_flags(
    flags: list[dict[str, Any]], unusable: bool = False, low: bool = False, partial: bool = False
) -> str:
    if unusable:
        return "unusable"
    sev = {f["severity"] for f in flags}
    if low or "error" in sev:
        return "low"
    if partial or "warn" in sev:
        return "medium"
    return "high"


def min_level(*levels: str) -> str:
    return min(levels, key=lambda x: LEVEL_RANK.get(x, 0))
