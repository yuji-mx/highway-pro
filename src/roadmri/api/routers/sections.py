from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from roadmri.api.deps import AppState, state
from roadmri.api.util import geom_geojson, record, records

router = APIRouter(tags=["sections"])
BASE = (
    "SELECT s.* EXCLUDE (geom), f1.name AS from_name, f2.name AS to_name FROM section s "
    "LEFT JOIN facility f1 ON s.from_facility_id=f1.facility_id "
    "LEFT JOIN facility f2 ON s.to_facility_id=f2.facility_id"
)


@router.get("/sections")
def list_sections(
    route: str | None = None, direction: str | None = None, s: AppState = Depends(state)
) -> dict[str, Any]:
    where, p = ["TRUE"], []
    if route:
        where.append("s.route_ref=?")
        p.append(route)
    if direction:
        where.append("s.direction=?")
        p.append(direction)
    df = s.store.df(f"{BASE} WHERE {' AND '.join(where)} ORDER BY s.route_ref, s.direction, s.section_id", p)
    routes = list(s.store.df("SELECT DISTINCT route_ref FROM section ORDER BY 1")["route_ref"])
    return {"items": records(df), "routes": routes}


@router.get("/sections/{section_id}")
def get_section(section_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    r = s.store.one(f"{BASE} WHERE s.section_id=?", [section_id])
    if r is None:
        raise HTTPException(404, f"区間が見つかりません: {section_id}")
    g = s.store.one("SELECT geom FROM section WHERE section_id=?", [section_id])
    out = record(r)
    out["geometry"] = geom_geojson(g["geom"]) if g else None
    out["distances"] = records(
        s.store.df(
            "SELECT section_id, definition, distance_m, note FROM section_distance "
            "WHERE section_id=? ORDER BY definition",
            [section_id],
        )
    )
    out["flags"] = records(s.store.df("SELECT * FROM quality_flag WHERE target_id=?", [section_id]))
    rev = s.store.one(
        "SELECT section_id FROM section WHERE from_facility_id=? AND to_facility_id=? AND route_ref=?",
        [r["to_facility_id"], r["from_facility_id"], r["route_ref"]],
    )
    out["reverse_section_id"] = rev["section_id"] if rev else None
    return out


@router.get("/sections/{section_id}/profile")
def section_profile(section_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    if not s.store.scalar("SELECT count(*) FROM section WHERE section_id=?", [section_id]):
        raise HTTPException(404, f"区間が見つかりません: {section_id}")
    df = s.store.df(
        "SELECT * FROM sample_point WHERE element_type='mainline' AND element_id=? ORDER BY seq", [section_id]
    )
    return {"section_id": section_id, "points": records(df)}
