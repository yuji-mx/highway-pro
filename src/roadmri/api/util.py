"""API 共通ユーティリティ（JSON 化）。"""

from __future__ import annotations

import contextlib
import json
import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

JSON_COLS = {
    "destinations",
    "classification_reasons",
    "merge_reason",
    "osm_ids",
    "from_ref",
    "to_ref",
    "composite",
    "difficulty_components",
    "curves",
    "features",
    "detail",
    "props",
    "old_value",
    "new_value",
    "osm_anchor",
    "params",
    "range_spec",
    "provenance",
    "spec",
    "query",
}


def py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.ndarray):
        return [py(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [py(x) for x in v]
    if isinstance(v, dict):
        return {str(k): py(x) for k, x in v.items()}
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return v.isoformat()
    if v is pd.NaT:
        return None
    return v


def record(r: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in r.items():
        if k in ("geom", "hull", "from_point", "to_point", "coverage"):
            continue
        if k in JSON_COLS and isinstance(v, str):
            with contextlib.suppress(json.JSONDecodeError):
                v = json.loads(v)
        out[k] = py(v)
    return out


def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [record(r) for r in df.to_dict("records")]


def geom_geojson(blob: Any) -> dict[str, Any] | None:
    if blob is None:
        return None
    from shapely import wkb
    from shapely.geometry import mapping

    return mapping(wkb.loads(bytes(blob)))  # type: ignore[no-any-return]
