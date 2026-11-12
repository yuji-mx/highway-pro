from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from roadmri.api.deps import AppState, state
from roadmri.api.util import record

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    j = s.jobs.get(job_id)
    if j is None:
        raise HTTPException(404, f"ジョブが見つかりません: {job_id}")
    out = record(j)
    if j.get("status") == "succeeded" and j.get("result_path") and Path(j["result_path"]).exists():
        out["download_url"] = f"/api/jobs/{job_id}/download"
    return out


@router.get("/jobs/{job_id}/download")
def download(job_id: str, s: AppState = Depends(state)) -> FileResponse:
    j = s.jobs.get(job_id)
    if j is None or not j.get("result_path"):
        raise HTTPException(404, "ダウンロードできる結果がありません")
    p = Path(j["result_path"])
    exports = s.cfg.path("exports_dir").resolve()
    if not p.resolve().is_relative_to(exports) or not p.exists():
        raise HTTPException(404, "ファイルが見つかりません")
    return FileResponse(p, filename=p.name)
