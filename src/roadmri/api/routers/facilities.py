from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from roadmri.analysis.similarity import similar_facilities
from roadmri.api.deps import AppState, state
from roadmri.api.util import geom_geojson, record, records
from roadmri.store.manual_edits import history

router = APIRouter(tags=["facilities"])
SORTABLE = {
    "name",
    "facility_type",
    "shape_class",
    "shape_confidence",
    "ramp_count",
    "quality_level",
    "review_status",
    "exit_number",
    "lat",
    "lon",
    "facility_id",
}


@router.get("/facilities")
def list_facilities(
    type: str | None = None,
    route: str | None = None,
    min_confidence: float | None = None,
    review_status: str | None = None,
    quality: str | None = None,
    needs_review: bool = False,
    in_range: bool | None = None,
    q: str | None = None,
    sort: str = "name",
    order: str = "asc",
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=10000),
    s: AppState = Depends(state),
) -> dict[str, Any]:
    where = ["TRUE"]
    p: list[Any] = []
    if type:
        where.append("facility_type = ?")
        p.append(type)
    if route:
        where.append("list_contains(route_refs, ?)")
        p.append(route)
    if min_confidence is not None:
        where.append("shape_confidence >= ?")
        p.append(min_confidence)
    if review_status:
        where.append("review_status = ?")
        p.append(review_status)
    if quality:
        where.append("quality_level = ?")
        p.append(quality)
    if needs_review:
        where.append("(review_status = 'needs_review' OR quality_level IN ('low','unusable'))")
    if in_range is not None:
        where.append("in_range = ?")
        p.append(in_range)
    if q:
        from roadmri.config import get_config
        from roadmri.network.names import NameNormalizer

        cfg = get_config()
        nn = NameNormalizer(cfg.name_normalization, cfg.name_aliases).normalize(q)
        where.append(
            "(name ILIKE ? OR normalized_name ILIKE ? OR name_en ILIKE ? OR list_contains(name_variants, ?))"
        )
        p += [f"%{q}%", f"%{nn.stem if nn else q}%", f"%{q}%", q]
    if sort not in SORTABLE:
        raise HTTPException(422, f"並べ替えできない項目です: {sort}")
    direction = "DESC" if order == "desc" else "ASC"
    w = " AND ".join(where)
    total = int(s.store.scalar(f"SELECT count(*) FROM facility WHERE {w}", p) or 0)
    df = s.store.df(
        f"SELECT * EXCLUDE (geom, hull) FROM facility WHERE {w} ORDER BY {sort} {direction} NULLS LAST, "
        f"facility_id LIMIT {limit} OFFSET {offset}",
        p,
    )
    return {"total": total, "items": records(df)}


def _fac(s: AppState, fid: str) -> dict[str, Any]:
    r = s.store.one("SELECT * FROM facility WHERE facility_id=?", [fid])
    if r is None:
        raise HTTPException(404, f"施設が見つかりません: {fid}")
    return r


@router.get("/facilities/{facility_id}")
def get_facility(facility_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    r = _fac(s, facility_id)
    out = record(r)
    out["geometry"] = geom_geojson(r["geom"])
    out["hull_geometry"] = geom_geojson(r["hull"])
    ramps = s.store.df("SELECT * EXCLUDE (geom) FROM ramp WHERE facility_id=? ORDER BY seq", [facility_id])
    out["ramps"] = records(ramps)
    secs = s.store.df(
        "SELECT s.* EXCLUDE (geom), f1.name AS from_name, f2.name AS to_name FROM section s "
        "LEFT JOIN facility f1 ON s.from_facility_id=f1.facility_id LEFT JOIN facility f2 ON s.to_facility_id=f2.facility_id "
        "WHERE s.from_facility_id=? OR s.to_facility_id=? ORDER BY s.route_ref, s.direction",
        [facility_id, facility_id],
    )
    out["sections"] = records(secs)
    out["osm_objects"] = records(
        s.store.df("SELECT * FROM facility_osm_map WHERE facility_id=?", [facility_id])
    )
    out["flags"] = records(
        s.store.df(
            "SELECT * FROM quality_flag WHERE target_id=? OR target_id IN "
            "(SELECT ramp_id FROM ramp WHERE facility_id=?)",
            [facility_id, facility_id],
        )
    )
    feat = s.store.one("SELECT * FROM classification_feature WHERE facility_id=?", [facility_id])
    out["classification"] = record(feat) if feat else None
    z = s.store.scalar(
        "SELECT avg(z_corrected) FROM sample_point WHERE element_id IN "
        "(SELECT ramp_id FROM ramp WHERE facility_id=?) AND seq=0",
        [facility_id],
    )
    out["z_dem"] = z
    out["provenance"] = s.run_info()
    ids = list(s.store.df("SELECT facility_id FROM facility ORDER BY name, facility_id")["facility_id"])
    i = ids.index(facility_id)
    out["prev_id"] = ids[i - 1] if i > 0 else None
    out["next_id"] = ids[i + 1] if i + 1 < len(ids) else None
    return out


@router.get("/facilities/{facility_id}/similar")
def facility_similar(
    facility_id: str, limit: int = Query(10, ge=1, le=100), s: AppState = Depends(state)
) -> dict[str, Any]:
    _fac(s, facility_id)
    return similar_facilities(s.store, facility_id, s.cfg.similarity, limit)


@router.get("/facilities/{facility_id}/history")
def facility_history(facility_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    r = _fac(s, facility_id)
    run = s.run_info()
    auto = {
        "kind": "auto",
        "created_at": run.get("created_at"),
        "run_id": run.get("run_id"),
        "text_ja": f"自動分類：{r['shape_class_auto']} {float(r['shape_confidence_auto'] or 0):.2f}"
        f"（{run.get('classifier_version')}）",
    }
    return {"auto": auto, "edits": [record(e) for e in history(s.store, "facility", facility_id)]}
