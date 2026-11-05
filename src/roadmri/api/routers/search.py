from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from roadmri.analysis.presets import list_presets
from roadmri.analysis.query import fields_catalog, normalize_target, to_text, warnings
from roadmri.analysis.search import count_total, run_query
from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import QueryDSL, SavedQuery, SavedRange
from roadmri.api.util import records
from roadmri.provenance import new_ulid

router = APIRouter(tags=["search"])


@router.post("/search/text")
def search_text(q: QueryDSL) -> dict[str, Any]:
    d = q.model_dump()
    normalize_target(d["target"])
    return {"text": to_text(d), "warnings": warnings(d)}


@router.get("/search/fields")
def search_fields() -> dict[str, Any]:
    return fields_catalog()


@router.post("/search/{target}")
def search(target: str, q: QueryDSL, s: AppState = Depends(state)) -> dict[str, Any]:
    d = q.model_dump()
    d["target"] = normalize_target(target)
    df = run_query(s.store, d)
    return {
        "target": d["target"],
        "count": len(df),
        "total": count_total(s.store, d["target"]),
        "text": to_text(d),
        "warnings": warnings(d),
        "items": records(df),
    }


@router.get("/presets")
def presets(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    return list_presets(s.cfg)


@router.get("/saved-queries")
def saved_queries(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    return records(s.store.df("SELECT * FROM saved_query ORDER BY created_at DESC"))


@router.post("/saved-queries", status_code=201)
def save_query(body: SavedQuery, s: AppState = Depends(state)) -> dict[str, Any]:
    normalize_target(body.target)
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "query_id": new_ulid(),
        "name": body.name,
        "target": body.target,
        "query": body.query,
        "created_at": now,
    }
    s.store.append("saved_query", [row])
    return {**row, "created_at": now.isoformat()}


@router.delete("/saved-queries/{query_id}", status_code=204)
def delete_query(query_id: str, s: AppState = Depends(state)) -> None:
    if not s.store.scalar("SELECT count(*) FROM saved_query WHERE query_id=?", [query_id]):
        raise HTTPException(404, f"保存クエリが見つかりません: {query_id}")
    s.store.execute("DELETE FROM saved_query WHERE query_id=?", [query_id])


@router.get("/saved-ranges")
def saved_ranges(s: AppState = Depends(state)) -> list[dict[str, Any]]:
    return records(s.store.df("SELECT * FROM saved_range ORDER BY created_at DESC"))


@router.post("/saved-ranges", status_code=201)
def save_range(body: SavedRange, s: AppState = Depends(state)) -> dict[str, Any]:
    now = datetime.now(UTC)
    row: dict[str, Any] = {"range_id": new_ulid(), "name": body.name, "spec": body.spec, "created_at": now}
    s.store.append("saved_range", [row])
    return {**row, "created_at": now.isoformat()}


@router.delete("/saved-ranges/{range_id}", status_code=204)
def delete_range(range_id: str, s: AppState = Depends(state)) -> None:
    if not s.store.scalar("SELECT count(*) FROM saved_range WHERE range_id=?", [range_id]):
        raise HTTPException(404, f"保存範囲が見つかりません: {range_id}")
    s.store.execute("DELETE FROM saved_range WHERE range_id=?", [range_id])
