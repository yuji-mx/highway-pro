from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from roadmri.api.deps import AppState, state
from roadmri.export.tiles import LAYERS, to_gdf

router = APIRouter(tags=["layers"])


def _bbox(b: str | None) -> list[float] | None:
    if not b:
        return None
    try:
        v = [float(x) for x in b.split(",")]
    except ValueError as e:
        raise HTTPException(422, "bbox は 西,南,東,北 の数値で指定してください") from e
    if len(v) != 4:
        raise HTTPException(422, "bbox は 4 つの数値で指定してください")
    return v


def _query(s: AppState, name: str, bbox: list[float] | None) -> Any:
    sql = "SELECT feature_id, props, geom FROM display_feature WHERE layer=?"
    params: list[Any] = [name]
    if bbox:
        sql += " AND maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ?"
        params += [bbox[0], bbox[2], bbox[1], bbox[3]]
    return s.store.df(sql + " ORDER BY feature_id", params)


@router.get("/layers/{name}.fgb")
def layer_fgb(
    name: str, bbox: str | None = None, zoom: float | None = Query(None), s: AppState = Depends(state)
) -> Response:
    if name not in LAYERS:
        raise HTTPException(404, f"レイヤーが見つかりません: {name}")
    bb = _bbox(bbox)
    if bb is None:
        p = s.cfg.path("tiles_dir") / f"{name}.fgb"
        if p.exists():
            return FileResponse(p, media_type="application/octet-stream")
    gdf = to_gdf(_query(s, name, bb))
    if gdf.empty:
        return Response(b"", media_type="application/octet-stream")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"{name}.fgb"
        gdf.to_file(p, driver="FlatGeobuf", engine="pyogrio")
        data = p.read_bytes()
    return Response(data, media_type="application/octet-stream")


@router.get("/layers/{name}.geojson")
def layer_geojson(name: str, bbox: str | None = None, s: AppState = Depends(state)) -> Response:
    if name not in LAYERS:
        raise HTTPException(404, f"レイヤーが見つかりません: {name}")
    gdf = to_gdf(_query(s, name, _bbox(bbox)))
    body = (
        gdf.to_json(drop_id=True) if len(gdf) else json.dumps({"type": "FeatureCollection", "features": []})
    )
    return Response(body, media_type="application/geo+json")
