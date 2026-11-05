from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import ExportRequest
from roadmri.export.tabular import DATASETS, FORMATS, export_dataset

router = APIRouter(tags=["export"])


@router.post("/export", status_code=202)
def export(body: ExportRequest, s: AppState = Depends(state)) -> dict[str, Any]:
    if body.dataset not in DATASETS:
        raise HTTPException(422, f"データセットが不正です: {body.dataset}")
    if body.format not in FORMATS:
        raise HTTPException(422, f"形式が不正です: {body.format}")
    ids: set[str] | None = set(body.ids) if body.ids else None
    query = body.query
    if body.range and not ids:
        from roadmri.analysis.ranges import resolve_facilities
        from roadmri.analysis.search import run_query

        fac = s.store.df("SELECT facility_id, lon, lat, route_refs, shape_class, admin_code FROM facility")
        fids = resolve_facilities(body.range, fac, lambda q: run_query(s.store, q))
        if body.dataset == "facilities":
            ids = fids
        elif body.dataset == "ramps":
            ids = set(
                s.store.df("SELECT ramp_id FROM ramp")["ramp_id"][
                    s.store.df("SELECT facility_id FROM ramp")["facility_id"].isin(fids)
                ]
            )
        elif body.dataset == "sections":
            sec = s.store.df("SELECT section_id, from_facility_id, to_facility_id FROM section")
            ids = set(
                sec[sec["from_facility_id"].isin(fids) & sec["to_facility_id"].isin(fids)]["section_id"]
            )

    def work(progress: Any) -> str:
        return str(export_dataset(s.store, s.cfg, body.dataset, body.format, query, None, ids))

    return {"job_id": s.jobs.submit("export", body.model_dump(), work)}
