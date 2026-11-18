from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri import __version__
from roadmri.api.deps import AppState, state
from roadmri.api.util import records
from roadmri.network.anomalies import describe
from roadmri.provenance import attribution_text

router = APIRouter(tags=["meta"])


def default_units(s: AppState) -> str:
    """表示単位の初期値：公開中の地域がすべて units: imperial（米国の州など）なら imperial、それ以外は metric。
    利用者が画面で単位を選んだ場合は、画面側でその選択を優先する（ここでは上書きしない）。"""
    from roadmri.pipeline import split_regions

    run = s.store.current_run()
    if not run or not run.get("region_id"):
        return "metric"
    units = []
    for rid in split_regions(str(run["region_id"])):
        try:
            units.append(str(s.cfg.region(rid).get("units", "metric")))
        except (FileNotFoundError, OSError):
            units.append("metric")
    return "imperial" if units and all(u == "imperial" for u in units) else "metric"


@router.get("/meta")
def meta(s: AppState = Depends(state)) -> dict[str, Any]:
    dems = s.store.df("SELECT * EXCLUDE (coverage) FROM dem_dataset")
    srcs = s.store.df("SELECT * FROM dataset_source")
    counts = {
        t: int(s.store.scalar(f"SELECT count(*) FROM {t}") or 0) for t in ("facility", "ramp", "section")
    }
    countries = list(s.store.df("SELECT DISTINCT country FROM facility ORDER BY 1")["country"])
    review = int(s.store.scalar("SELECT count(*) FROM facility WHERE review_status='needs_review'") or 0)
    return {
        "app": "ROAD MRI",
        "version": __version__,
        "run": s.run_info(),
        "sources": records(srcs),
        "dem_datasets": records(dems),
        "counts": {**counts, "needs_review": review},
        "attribution": attribution_text([{"attribution": a} for a in dems.get("attribution", [])]),
        "offline": True,
        "countries": countries,
        "has_data": counts["facility"] > 0,
        "map": s.cfg.settings.get("map", {}),
        "list": s.cfg.settings.get("list", {}),
        "areas": s.cfg.settings.get("areas", {}),
        "anomaly_flags": describe(),
        "default_units": default_units(s),
        # 航空写真の出典など（取得元の URL は返さない。タイルは /api/imagery 経由）
        "imagery": [
            {k: v for k, v in {"id": sid, **spec}.items() if k != "url"}
            for sid, spec in (s.cfg.settings.get("imagery", {}).get("sources") or {}).items()
        ],
    }
