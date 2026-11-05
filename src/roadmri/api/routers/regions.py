from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import PipelineRun, RegionCreate
from roadmri.api.util import records

router = APIRouter(tags=["regions"])


def _status(s: AppState, r: dict[str, Any]) -> str:
    run = s.store.current_run()
    if run and run.get("region_id") == r.get("id"):
        return "processed"
    pbf = r.get("pbf", {})
    if pbf.get("local"):
        p = Path(pbf["local"])
        return "cached" if (p if p.is_absolute() else s.cfg.root / p).exists() else "missing"
    name = Path(str(pbf.get("url", ""))).name
    if name and list((s.cfg.path("raw_dir") / "osm" / "geofabrik").glob(f"*/{name}")):
        return "fetched"
    return "missing"


@router.get("/regions")
def list_regions(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    return [{**r, "status": _status(s, r)} for r in s.cfg.regions()]


@router.post("/regions", status_code=201)
def create_region(body: RegionCreate, s: AppState = Depends(state)) -> dict[str, Any]:
    from roadmri.analysis.ranges import range_geometry

    range_geometry(body.range)  # 検証（不正なら 422）
    p = s.cfg.root / "config" / "regions" / f"{body.id}.yaml"
    if p.exists():
        raise HTTPException(409, f"地域 ID が既に存在します: {body.id}")
    p.write_text(yaml.safe_dump(body.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return body.model_dump()


@router.get("/regions/{region_id}/status")
def region_status(region_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    regs = {r["id"]: r for r in s.cfg.regions()}
    if region_id not in regs:
        raise HTTPException(404, f"地域が見つかりません: {region_id}")
    jobs = s.store.df("SELECT * FROM job WHERE kind='pipeline' ORDER BY created_at DESC LIMIT 5")
    return {
        "region": regs[region_id],
        "status": _status(s, regs[region_id]),
        "run": s.run_info(),
        "jobs": records(jobs),
    }


@router.post("/pipeline/run", status_code=202)
def pipeline_run(body: PipelineRun, s: AppState = Depends(state)) -> dict[str, Any]:
    from roadmri.pipeline import run_pipeline

    if body.region not in {r["id"] for r in s.cfg.regions()}:
        raise HTTPException(404, f"地域が見つかりません: {body.region}")

    def work(progress: Any) -> str | None:
        res = run_pipeline(s.cfg, body.region, s.store, body.from_step, body.to_step, progress=progress)
        return str(res.get("run_id"))

    jid = s.jobs.submit("pipeline", body.model_dump(), work)
    return {"job_id": jid}
