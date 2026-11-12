from __future__ import annotations

from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends

from roadmri.analysis.search import run_query
from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import ReportRequest
from roadmri.export.report import build_report

router = APIRouter(tags=["reports"])


@router.post("/reports", status_code=202)
def reports(body: ReportRequest, s: AppState = Depends(state)) -> dict[str, Any]:
    if body.query:
        res = run_query(s.store, body.query).head(body.limit)
    elif body.ids:
        res = s.store.df("SELECT * EXCLUDE (geom) FROM ramp WHERE ramp_id IN (SELECT unnest(?))", [body.ids])
        if res.empty:
            res = pd.DataFrame({"facility_id": body.ids})
    else:
        res = s.store.df("SELECT * EXCLUDE (geom) FROM ramp ORDER BY difficulty DESC LIMIT ?", [body.limit])

    def work(progress: Any) -> str:
        return str(build_report(s.store, s.cfg, res, body.query, None, body.pdf, progress))

    return {"job_id": s.jobs.submit("report", body.model_dump(), work), "count": len(res)}
