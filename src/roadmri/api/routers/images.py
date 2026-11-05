from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import ImageRequest
from roadmri.export.images import batch_images, bbox_around, dem_attributions, render_mri

router = APIRouter(tags=["images"])
MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "svg": "image/svg+xml"}


def _bbox(body: ImageRequest, s: AppState) -> tuple[float, float, float, float]:
    if body.bbox:
        if len(body.bbox) != 4:
            raise HTTPException(422, "bbox は 4 つの数値で指定してください")
        return (body.bbox[0], body.bbox[1], body.bbox[2], body.bbox[3])
    if body.center:
        return bbox_around(body.center[0], body.center[1], body.radius_m)
    r = s.store.one("SELECT min(lon) w, min(lat) s, max(lon) e, max(lat) n FROM facility")
    if not r or r["w"] is None:
        raise HTTPException(404, "データがありません")
    pad = 0.02
    return (r["w"] - pad, r["s"] - pad, r["e"] + pad, r["n"] + pad)


@router.post("/images/render")
def render(body: ImageRequest, s: AppState = Depends(state)) -> Response:
    """単発の画像出力（同期）。"""
    data = render_mri(
        s.store,
        _bbox(body, s),
        (body.width, body.height),
        body.format,
        body.background,
        body.line_scale,
        body.metric,
        body.labels,
        layers=body.layers,
        dem_names=dem_attributions(s.store),
        scale_m_per_px=body.scale_m_per_px,
    )
    return Response(
        data,
        media_type=MEDIA[body.format],
        headers={"Content-Disposition": f'attachment; filename="road_mri.{body.format}"'},
    )


@router.post("/images", status_code=202)
def images(body: ImageRequest, s: AppState = Depends(state)) -> dict[str, Any]:
    """一括画像生成（非同期ジョブ、ZIP）。query または facility_ids を入力にする。"""
    import pandas as pd

    from roadmri.analysis.search import run_query

    if body.query:
        res = run_query(s.store, body.query)
    elif body.facility_ids:
        res = pd.DataFrame({"facility_id": body.facility_ids})
    else:
        res = s.store.df("SELECT facility_id FROM facility WHERE in_range ORDER BY facility_id")

    def work(progress: Any) -> str:
        return str(
            batch_images(
                s.store,
                s.cfg,
                res,
                "jpeg" if body.format == "jpg" else body.format,
                (body.width, body.height),
                body.metric,
                None,
                body.radius_m,
                progress,
            )
        )

    return {"job_id": s.jobs.submit("images", body.model_dump(), work), "count": len(res)}
