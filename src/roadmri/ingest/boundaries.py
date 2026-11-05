"""行政界ポリゴン（オフライン用に data/raw/boundaries/ に保存した GeoJSON を読む）。"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def load_admin(boundaries_dir: Path, codes: list[str]) -> BaseGeometry | None:
    geoms = []
    for code in codes:
        p = boundaries_dir / f"{code}.geojson"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        feats = data.get("features", [data])
        geoms += [shape(f.get("geometry", f)) for f in feats]
    return unary_union(geoms) if geoms else None
