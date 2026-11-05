from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import EditCreate, EditRevert
from roadmri.api.util import record, records
from roadmri.store.manual_edits import EDITABLE, add_edit, revert_edit

router = APIRouter(tags=["edits"])


def _apply_now(s: AppState, row: dict[str, Any]) -> None:
    """再 build を待たずに、現在の結果テーブルへ修正を即時反映する（自動値は *_auto に保持済み）。"""
    tt, tid, field, v = row["target_type"], row["target_id"], row["field"], row["new_value"]
    key = f"{tt}_id"
    if tt == "facility" and field == "shape_class":
        s.store.execute(
            "UPDATE facility SET shape_class=?, shape_confidence=1.0, review_status='reviewed' "
            "WHERE facility_id=?",
            [v, tid],
        )
        s.store.execute("UPDATE ramp SET shape_class=? WHERE facility_id=?", [v, tid])
    elif field in ("from_ref", "to_ref"):
        import json

        s.store.execute(f"UPDATE {tt} SET {field}=? WHERE {key}=?", [json.dumps(v, ensure_ascii=False), tid])
    elif field in EDITABLE.get(tt, set()):
        col = field
        s.store.execute(f'UPDATE {tt} SET "{col}"=? WHERE {key}=?', [v, tid])


@router.get("/edits")
def list_edits(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    return records(s.store.df("SELECT * FROM manual_edit ORDER BY created_at DESC"))


@router.post("/edits", status_code=201)
def create_edit(body: EditCreate, s: AppState = Depends(state)) -> dict[str, Any]:
    row = add_edit(
        s.store, body.target_type, body.target_id, body.field, body.new_value, s.cfg.user_name, body.comment
    )
    _apply_now(s, row)
    return record(row)


@router.post("/edits/{edit_id}/revert", status_code=201)
def revert(edit_id: str, body: EditRevert | None = None, s: AppState = Depends(state)) -> dict[str, Any]:
    row = revert_edit(s.store, edit_id, s.cfg.user_name, body.comment if body else None)
    orig = s.store.one("SELECT * FROM manual_edit WHERE edit_id=?", [edit_id])
    if orig is not None:
        import json

        old = json.loads(orig["old_value"]) if isinstance(orig["old_value"], str) else orig["old_value"]
        _apply_now(
            s,
            {
                "target_type": orig["target_type"],
                "target_id": orig["target_id"],
                "field": orig["field"],
                "new_value": old,
            },
        )
    return record(row)
