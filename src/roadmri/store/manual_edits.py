"""手動修正（§4.19）。manual_edit は追記専用。取り消しも新しい修正として記録する。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from roadmri.provenance import new_ulid
from roadmri.store.duckdb_store import Store

EDITABLE: dict[str, set[str]] = {
    "facility": {"shape_class", "facility_type", "review_status", "comment", "name", "excluded"},
    "ramp": {"facility_id", "flow", "from_ref", "to_ref", "excluded"},
    "section": {"excluded", "adopted_definition"},
}
REVERT_FIELD = "__revert__"


class EditError(ValueError):
    pass


def _loads(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def anchor_for(store: Store, target_type: str, target_id: str) -> dict[str, Any]:
    if target_type == "facility":
        r = store.one(
            "SELECT normalized_name, route_refs, lon, lat FROM facility WHERE facility_id=?", [target_id]
        )
        if r:
            return {
                "normalized_name": r["normalized_name"],
                "route": (r["route_refs"] or [None])[0],
                "lon": r["lon"],
                "lat": r["lat"],
            }
    if target_type == "ramp":
        ws = store.df('SELECT way_id FROM ramp_way_map WHERE ramp_id=? ORDER BY "order"', [target_id])
        return {"way_ids": [int(w) for w in ws["way_id"]]}
    if target_type == "section":
        r = store.one(
            "SELECT route_ref, from_facility_id, to_facility_id, direction FROM section WHERE section_id=?",
            [target_id],
        )
        return dict(r) if r else {}
    return {}


def current_value(store: Store, target_type: str, target_id: str, field: str) -> Any:
    table = {"facility": "facility", "ramp": "ramp", "section": "section"}[target_type]
    key = f"{target_type}_id"
    col = "comment" if field == "comment" else field
    try:
        r = store.one(f'SELECT "{col}" AS v FROM {table} WHERE {key}=?', [target_id])
    except Exception:
        return None
    return _loads(r["v"]) if r else None


def add_edit(
    store: Store,
    target_type: str,
    target_id: str,
    field: str,
    new_value: Any,
    user: str,
    comment: str | None = None,
) -> dict[str, Any]:
    if target_type not in EDITABLE:
        raise EditError(f"修正対象の種類が不正です: {target_type}")
    if field not in EDITABLE[target_type]:
        raise EditError(f"修正できない項目です: {target_type}.{field}")
    table_key = f"{target_type}_id"
    exists = store.scalar(f"SELECT count(*) FROM {target_type} WHERE {table_key}=?", [target_id])
    if not exists:
        raise LookupError(f"対象が見つかりません: {target_id}")
    row = {
        "edit_id": new_ulid(),
        "target_type": target_type,
        "target_id": target_id,
        "field": field,
        "old_value": current_value(store, target_type, target_id, field),
        "new_value": new_value,
        "comment": comment,
        "user_name": user,
        "created_at": datetime.now(UTC),
        "osm_anchor": anchor_for(store, target_type, target_id),
        "superseded_by": None,
        "status": "active",
    }
    store.append("manual_edit", [row])
    return row


def revert_edit(store: Store, edit_id: str, user: str, comment: str | None = None) -> dict[str, Any]:
    e = store.one("SELECT * FROM manual_edit WHERE edit_id=?", [edit_id])
    if not e:
        raise LookupError(f"修正が見つかりません: {edit_id}")
    if e["field"] == REVERT_FIELD:
        raise EditError("取り消しの取り消しはできません。新しい修正を登録してください")
    row = {
        "edit_id": new_ulid(),
        "target_type": e["target_type"],
        "target_id": e["target_id"],
        "field": REVERT_FIELD,
        "old_value": None,
        "new_value": {"edit_id": edit_id, "field": e["field"]},
        "comment": comment,
        "user_name": user,
        "created_at": datetime.now(UTC),
        "osm_anchor": _loads(e["osm_anchor"]),
        "superseded_by": None,
        "status": "active",
    }
    store.append("manual_edit", [row])
    return row


def all_edits(store: Store) -> pd.DataFrame:
    return store.df("SELECT * FROM manual_edit ORDER BY created_at, edit_id")


def effective_edits(store: Store) -> list[dict[str, Any]]:
    """取り消されていない修正のうち、対象・項目ごとに最新のもの。"""
    df = all_edits(store)
    if df.empty:
        return []
    reverted = set()
    for v in df.loc[df["field"] == REVERT_FIELD, "new_value"]:
        reverted.add(_loads(v)["edit_id"])
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in df.to_dict("records"):
        if r["field"] == REVERT_FIELD or r["edit_id"] in reverted:
            continue
        r["new_value"] = _loads(r["new_value"])
        r["old_value"] = _loads(r["old_value"])
        r["osm_anchor"] = _loads(r["osm_anchor"])
        latest[(r["target_type"], r["target_id"], r["field"])] = r
    # 同じ対象・項目に後から取り消しがあれば、それ以前の有効な修正に戻る
    return list(latest.values())


def history(store: Store, target_type: str, target_id: str) -> list[dict[str, Any]]:
    rows = store.rows(
        "SELECT * FROM manual_edit WHERE target_type=? AND target_id=? ORDER BY created_at, edit_id",
        [target_type, target_id],
    )
    res = {r["edit_id"]: r for r in store.rows("SELECT * FROM manual_edit_resolution")}
    for r in rows:
        for k in ("old_value", "new_value", "osm_anchor"):
            r[k] = _loads(r[k])
        rr = res.get(r["edit_id"])
        if rr:
            r["status"] = rr["status"]
    return rows


def _jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


def resolve_target(
    edit: dict[str, Any], tables: dict[str, pd.DataFrame], ramp_ways: dict[str, set[int]]
) -> str | None:
    """ID が変わっていても、OSM アンカーで現在の対象を探す（ID 継承）。"""
    tt, tid = edit["target_type"], edit["target_id"]
    df = tables[tt]
    key = f"{tt}_id"
    if tid in set(df[key]):
        return str(tid)
    anchor = edit.get("osm_anchor") or {}
    if tt == "facility" and anchor.get("normalized_name"):
        from roadmri.geometry.projection import geodesic_distance_m

        cand = df[df["normalized_name"] == anchor["normalized_name"]]
        for r in cand.itertuples():
            if (
                anchor.get("lon") is None
                or geodesic_distance_m(r.lon, r.lat, anchor["lon"], anchor["lat"]) <= 500
            ):
                return str(r.facility_id)
    if tt == "ramp" and anchor.get("way_ids"):
        ws = set(anchor["way_ids"])
        best, bj = None, 0.0
        for rid, rws in ramp_ways.items():
            j = _jaccard(ws, rws)
            if j > bj:
                best, bj = rid, j
        if best and bj >= 0.5:
            return best
    if tt == "section" and anchor:
        m = df[
            (df["route_ref"] == anchor.get("route_ref"))
            & (df["from_facility_id"] == anchor.get("from_facility_id"))
            & (df["to_facility_id"] == anchor.get("to_facility_id"))
            & (df["direction"] == anchor.get("direction"))
        ]
        if len(m):
            return str(m.iloc[0]["section_id"])
    return None


def apply_edits(
    edits: list[dict[str, Any]], tables: dict[str, pd.DataFrame], ramp_ways: dict[str, set[int]], run_id: str
) -> list[dict[str, Any]]:
    """自動結果に最新の有効な修正を重ねる。自動値は *_auto 列に残す。戻り値は解決結果。"""
    resolutions = []
    for e in sorted(edits, key=lambda r: (str(r["created_at"]), r["edit_id"])):
        tt = e["target_type"]
        tid = resolve_target(e, tables, ramp_ways)
        if tid is None:
            resolutions.append(
                {"edit_id": e["edit_id"], "run_id": run_id, "status": "orphaned", "resolved_target_id": None}
            )
            continue
        df = tables[tt]
        idx = df.index[df[f"{tt}_id"] == tid]
        field, val = e["field"], e["new_value"]
        if tt == "facility":
            if field == "shape_class":
                df.loc[idx, "shape_class"] = val
                df.loc[idx, "shape_confidence"] = 1.0
                df.loc[idx, "review_status"] = "reviewed"
            elif field in ("facility_type", "review_status", "comment", "name"):
                df.loc[idx, field] = val
            elif field == "excluded":
                df.loc[idx, "excluded"] = bool(val)
        elif tt == "ramp":
            if field in ("flow", "facility_id"):
                df.loc[idx, field] = val
            elif field in ("from_ref", "to_ref"):
                for i in idx:
                    df.at[i, field] = val
            elif field == "excluded":
                df.loc[idx, "excluded"] = bool(val)
        elif tt == "section":
            if field == "excluded":
                df.loc[idx, "excluded"] = bool(val)
            elif field == "adopted_definition":
                df.loc[idx, "adopted_definition"] = val
        resolutions.append(
            {"edit_id": e["edit_id"], "run_id": run_id, "status": "active", "resolved_target_id": tid}
        )
    return resolutions
