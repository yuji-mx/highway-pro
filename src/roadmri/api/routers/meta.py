from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri import __version__
from roadmri.api.deps import AppState, state
from roadmri.api.util import records
from roadmri.provenance import attribution_text

router = APIRouter(tags=["meta"])


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
    }
