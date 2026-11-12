from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri.api.deps import AppState, state
from roadmri.api.util import records

router = APIRouter(tags=["quality"])


@router.get("/quality/flags")
def flags(
    target: str | None = None, severity: str | None = None, s: AppState = Depends(state)
) -> list[dict[str, Any]]:
    where, p = ["TRUE"], []
    if target:
        where.append("(target_type=? OR target_id=?)")
        p += [target, target]
    if severity:
        where.append("severity=?")
        p.append(severity)
    return records(
        s.store.df(f"SELECT * FROM quality_flag WHERE {' AND '.join(where)} ORDER BY severity, code", p)
    )


@router.get("/review-queue")
def review_queue(s: AppState = Depends(state)) -> dict[str, Any]:
    fac = s.store.df(
        "SELECT * EXCLUDE (geom, hull) FROM facility WHERE review_status='needs_review' "
        "OR quality_level IN ('low','unusable') ORDER BY shape_confidence, name"
    )
    orphans = s.store.df(
        "SELECT e.* FROM manual_edit e JOIN manual_edit_resolution r USING (edit_id) "
        "WHERE r.status='orphaned'"
    )
    return {"facilities": records(fac), "orphaned_edits": records(orphans)}
