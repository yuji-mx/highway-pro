from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from roadmri.analysis.similarity import similar_ramps
from roadmri.api.deps import AppState, state
from roadmri.api.util import geom_geojson, record, records
from roadmri.store.manual_edits import history

router = APIRouter(tags=["ramps"])


def _ramp(s: AppState, rid: str) -> dict[str, Any]:
    r = s.store.one("SELECT * FROM ramp WHERE ramp_id=?", [rid])
    if r is None:
        raise HTTPException(404, f"ランプが見つかりません: {rid}")
    return r


@router.get("/ramps/{ramp_id}")
def get_ramp(ramp_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    r = _ramp(s, ramp_id)
    out = record(r)
    out["geometry"] = geom_geojson(r["geom"])
    f = s.store.one("SELECT * EXCLUDE (geom, hull) FROM facility WHERE facility_id=?", [r["facility_id"]])
    out["facility"] = record(f) if f else None
    out["siblings"] = list(
        s.store.df("SELECT ramp_id FROM ramp WHERE facility_id=? ORDER BY seq", [r["facility_id"]])["ramp_id"]
    )
    out["flags"] = records(s.store.df("SELECT * FROM quality_flag WHERE target_id=?", [ramp_id]))
    out["way_ids"] = records(
        s.store.df('SELECT * FROM ramp_way_map WHERE ramp_id=? ORDER BY "order"', [ramp_id])
    )
    out["params"] = (s.run_info().get("provenance") or {}).get("params")
    out["history"] = [record(e) for e in history(s.store, "ramp", ramp_id)]
    return out


@router.get("/ramps/{ramp_id}/profile")
def ramp_profile(
    ramp_id: str, step: float | None = Query(None, gt=0), s: AppState = Depends(state)
) -> dict[str, Any]:
    _ramp(s, ramp_id)
    df = s.store.df(
        "SELECT * FROM sample_point WHERE element_type='ramp' AND element_id=? ORDER BY seq", [ramp_id]
    )
    if step and len(df) > 1:
        cur = float(df["s_m"].iloc[1] - df["s_m"].iloc[0])
        k = max(1, round(step / cur))
        df = df.iloc[::k]
    return {"ramp_id": ramp_id, "points": records(df)}


@router.get("/ramps/{ramp_id}/similar")
def ramp_similar(
    ramp_id: str, limit: int = Query(10, ge=1, le=100), s: AppState = Depends(state)
) -> dict[str, Any]:
    _ramp(s, ramp_id)
    return similar_ramps(s.store, ramp_id, s.cfg.similarity, limit)
