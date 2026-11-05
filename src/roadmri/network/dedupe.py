"""上下線の重複ノード統合（§4.3.3）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Candidate:
    osm_type: str  # node / way
    osm_id: int
    kind: str  # motorway_junction / services / rest_area
    name: str | None
    name_en: str | None
    norm_key: str | None
    code: str | None
    exit_ref: str | None
    route_ref: str | None
    direction: str | None
    lon: float
    lat: float
    x: float
    y: float
    tags: dict[str, Any] = field(default_factory=dict)
    timestamp: Any = None


@dataclass
class Group:
    members: list[Candidate]
    reasons: list[dict[str, Any]] = field(default_factory=list)


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[max(ra, rb)] = min(ra, rb)
        return True


def merge_candidates(
    cands: list[Candidate], merge_cfg: dict[str, Any], exit_ref_distance_m: float
) -> list[Group]:
    """正規化名称一致＋距離閾値、または出口番号一致＋同一路線＋1 km 以内で統合する。"""
    n = len(cands)
    uf = _UF(n)
    reasons: dict[tuple[int, int], dict[str, Any]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = cands[i], cands[j]
            d = float(np.hypot(a.x - b.x, a.y - b.y))
            if a.norm_key and b.norm_key and a.norm_key == b.norm_key:
                code = a.code or b.code or "default"
                thr = float(merge_cfg.get(code, merge_cfg.get("default", 2000)))
                if d <= thr:
                    uf.union(i, j)
                    reasons[(i, j)] = {
                        "rule": "same_normalized_name",
                        "name": a.norm_key,
                        "distance_m": round(d, 1),
                        "threshold_m": thr,
                        "osm_ids": [a.osm_id, b.osm_id],
                    }
            elif (
                (not a.norm_key or not b.norm_key)
                and a.exit_ref
                and a.exit_ref == b.exit_ref
                and a.route_ref
                and a.route_ref == b.route_ref
                and d <= exit_ref_distance_m
            ):
                uf.union(i, j)
                reasons[(i, j)] = {
                    "rule": "same_exit_number_same_route",
                    "exit_ref": a.exit_ref,
                    "distance_m": round(d, 1),
                    "osm_ids": [a.osm_id, b.osm_id],
                }
    groups: dict[int, Group] = {}
    for i in range(n):
        r = uf.find(i)
        groups.setdefault(r, Group(members=[]))
        groups[r].members.append(cands[i])
    for (i, _j), why in reasons.items():
        groups[uf.find(i)].reasons.append(why)
    out = list(groups.values())
    for g in out:
        if len(g.members) > 1:
            dirs = {m.direction for m in g.members if m.direction}
            if {"up", "down"} <= dirs:
                g.reasons.append({"rule": "carriageway_pair", "directions": sorted(dirs)})
    return sorted(out, key=lambda g: min(m.osm_id for m in g.members))
