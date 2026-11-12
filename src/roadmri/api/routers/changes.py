from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri.api.deps import AppState, state
from roadmri.api.util import records

router = APIRouter(tags=["changes"])


@router.get("/changes")
def changes(base: str | None = None, new: str | None = None, s: AppState = Depends(state)) -> dict[str, Any]:
    """変更監視（P4）。change_event テーブルの内容を返す。"""
    df = s.store.df(
        "SELECT * FROM change_event WHERE (? IS NULL OR base_run_id=?) AND (? IS NULL OR new_run_id=?)",
        [base, base, new, new],
    )
    return {"phase": "P4", "events": records(df)}
