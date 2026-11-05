from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri.analysis.compare import compare_ranges
from roadmri.analysis.similarity import compare_facilities
from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import CompareFacilities, StatsCompare
from roadmri.api.util import py

router = APIRouter(tags=["compare"])


@router.post("/compare/facilities")
def compare_two(body: CompareFacilities, s: AppState = Depends(state)) -> dict[str, Any]:
    return py(compare_facilities(s.store, body.a, body.b, s.cfg.similarity))  # type: ignore[no-any-return]


@router.post("/stats/compare")
def stats_compare(body: StatsCompare, s: AppState = Depends(state)) -> dict[str, Any]:
    return py(compare_ranges(s.store, s.cfg, body.model_dump()))  # type: ignore[no-any-return]


@router.get("/stats/presets")
def stats_presets(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    """範囲比較のプリセット（データ中の路線・地域から生成）。"""
    routes = list(s.store.df("SELECT DISTINCT unnest(route_refs) AS r FROM facility ORDER BY 1")["r"])
    fac = s.store.df(
        "SELECT min(lon) w, max(lon) e, min(lat) so, max(lat) n, median(lat) ml, median(lon) mo "
        "FROM facility WHERE in_range"
    )
    out: list[dict[str, Any]] = []
    for i, a in enumerate(routes):
        for b in routes[i + 1 :]:
            out.append(
                {
                    "id": f"route_{a}_{b}",
                    "label": f"{a} vs {b}",
                    "range_a": {"type": "route", "route_refs": [a], "label": f"{a} 沿い"},
                    "range_b": {"type": "route", "route_refs": [b], "label": f"{b} 沿い"},
                }
            )
    if len(fac) and fac.iloc[0]["w"] is not None:
        f = fac.iloc[0]
        w, e, so, n, ml, mo = (float(f[k]) for k in ("w", "e", "so", "n", "ml", "mo"))
        pad = 0.05
        out.append(
            {
                "id": "south_north",
                "label": "南部 vs 北部",
                "range_a": {"type": "bbox", "bbox": [w - pad, so - pad, e + pad, ml], "label": "南部"},
                "range_b": {"type": "bbox", "bbox": [w - pad, ml, e + pad, n + pad], "label": "北部"},
            }
        )
        out.append(
            {
                "id": "west_east",
                "label": "西部 vs 東部",
                "range_a": {"type": "bbox", "bbox": [w - pad, so - pad, mo, n + pad], "label": "西部"},
                "range_b": {"type": "bbox", "bbox": [mo, so - pad, e + pad, n + pad], "label": "東部"},
            }
        )
        if routes:
            out.append(
                {
                    "id": "search_vs_route",
                    "label": f"検索結果 vs {routes[0]} 全体",
                    "range_a": {
                        "type": "query",
                        "label": "検索結果",
                        "query": {
                            "target": "ramp",
                            "where": {"all": [{"field": "grade_up_max_w100", "op": "gte", "value": 3}]},
                        },
                    },
                    "range_b": {"type": "route", "route_refs": [routes[0]], "label": f"{routes[0]} 全体"},
                }
            )
    return out
