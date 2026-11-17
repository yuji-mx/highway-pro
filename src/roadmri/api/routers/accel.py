"""加速車線の確認・修正（ADR-0017）。自動の測定値は上書きせず、手動の値を別に保存して比較できるようにする。"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import AccelPoints, AccelReview
from roadmri.api.util import py, record
from roadmri.network.accel_lane import REASONS, Span, lane_path, measure_between
from roadmri.store.manual_edits import ACCEL_FIELD, accel_manual_values, add_edit, history

router = APIRouter(tags=["ramps"])


def _row(s: AppState, rid: str) -> dict[str, Any]:
    r = s.store.one("SELECT * FROM ramp WHERE ramp_id=?", [rid])
    if r is None:
        raise HTTPException(404, f"ランプが見つかりません: {rid}")
    if r.get("accel_lane_status") is None:
        raise HTTPException(422, "このランプは加速車線の対象外です（本線への合流ではありません）")
    return r


def _path(s: AppState, row: dict[str, Any]) -> tuple[Any, np.ndarray] | None:
    """ランプ全体と下流の本線をつないだ道筋（投影座標）と投影。中間成果物がなければ None。"""
    try:
        ctx = s.network(row["facility_id"])
    except (LookupError, FileNotFoundError):
        return None
    ramp = next((r for r in ctx.ramps if r.ramp_id == row["ramp_id"]), None)
    if ramp is None or ctx.rg is None:
        return None
    search = float(s.cfg.analysis["accel_lane"]["max_search_m"])
    return ctx.rg.proj, lane_path(ctx.rg.g, ctx.rg.edges_xy(ramp.edges), ramp.nodes[-1], search)


def _lonlat(proj: Any, xy: Any) -> list[float]:
    lon, lat = proj.to_lonlat(float(xy[0]), float(xy[1]))
    return [float(lon), float(lat)]


def _line(proj: Any, xy: np.ndarray) -> dict[str, Any]:
    lon, lat = proj.to_lonlat(xy[:, 0], xy[:, 1])
    return {
        "type": "LineString",
        "coordinates": [[float(a), float(b)] for a, b in zip(lon, lat, strict=True)],
    }


def _span(s: AppState, row: dict[str, Any], start: Any, end: Any) -> tuple[Any, Span]:
    pp = _path(s, row)
    if pp is None:
        raise HTTPException(409, "道路ネットワークの中間成果物がありません。roadmri build を実行してください")
    proj, path = pp
    sx, sy = proj.to_xy(float(start[0]), float(start[1]))
    ex, ey = proj.to_xy(float(end[0]), float(end[1]))
    try:
        return proj, measure_between(path, (float(sx), float(sy)), (float(ex), float(ey)))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _pt(lon: Any, lat: Any) -> list[float] | None:
    return [float(lon), float(lat)] if lon is not None and lat is not None else None


@router.get("/ramps/{ramp_id}/accel_lane")
def get_accel_lane(ramp_id: str, s: AppState = Depends(state)) -> dict[str, Any]:
    """自動の測定値と手動の確認・修正を並べて返す（地図表示用の道筋と区間を含む）。"""
    r = _row(s, ramp_id)
    auto = {
        "status": r["accel_lane_status"],
        "reason": r["accel_lane_reason"],
        "reason_text": REASONS.get(str(r["accel_lane_reason"])) if r["accel_lane_reason"] else None,
        "method": r["accel_lane_method"],
        "confidence": r["accel_lane_confidence"],
        "notes": r["accel_lane_notes"],
        "length_m": r["accel_lane_len_m"],
        "parallel_m": r["accel_parallel_m"],
        "added_lane_m": r["accel_added_lane_m"],
        "start": _pt(r["accel_start_lon"], r["accel_start_lat"]),
        "end": _pt(r["accel_end_lon"], r["accel_end_lat"]),
    }
    manual = None
    if r.get("accel_manual_status"):
        manual = {
            "status": r["accel_manual_status"],
            "length_m": r["accel_manual_len_m"],
            "start": _pt(r["accel_manual_start_lon"], r["accel_manual_start_lat"]),
            "end": _pt(r["accel_manual_end_lon"], r["accel_manual_end_lat"]),
            "note": r["accel_manual_note"],
            "edit_id": r["accel_manual_edit_id"],
            "user": r["accel_manual_user"],
            "at": py(r["accel_manual_at"]),
        }
    diff = (
        manual["length_m"] - auto["length_m"]
        if manual and manual["length_m"] is not None and auto["length_m"] is not None
        else None
    )
    out: dict[str, Any] = {"ramp_id": ramp_id, "auto": auto, "manual": manual, "difference_m": diff}
    pp = _path(s, r)
    out["path"] = _line(pp[0], pp[1]) if pp else None
    for key, part in (("auto_span", auto), ("manual_span", manual)):
        out[key] = None
        if part and part["start"] and part["end"] and pp:
            try:
                _, sp = _span(s, r, part["start"], part["end"])
                out[key] = _line(pp[0], sp.path_xy)
            except HTTPException:
                pass
    out["history"] = [record(e) for e in history(s.store, "ramp", ramp_id) if e["field"] in (ACCEL_FIELD,)]
    return out


@router.post("/ramps/{ramp_id}/accel_lane/measure")
def measure_accel_lane(ramp_id: str, body: AccelPoints, s: AppState = Depends(state)) -> dict[str, Any]:
    """指定した 2 点の間の長さ（保存しない。地図でクリックした点の確認用）。"""
    r = _row(s, ramp_id)
    proj, sp = _span(s, r, body.start, body.end)
    limit = float(s.cfg.analysis["accel_lane"]["review_max_snap_m"])
    warnings = [
        f"{name}が道筋から {off:.0f} m 離れています（{limit:g} m 以内で指定してください）"
        for name, off in (("始点", sp.start_offset_m), ("終点", sp.end_offset_m))
        if off > limit
    ]
    return {
        "length_m": sp.length_m,
        "start": _lonlat(proj, sp.start_xy),
        "end": _lonlat(proj, sp.end_xy),
        "start_offset_m": sp.start_offset_m,
        "end_offset_m": sp.end_offset_m,
        "span": _line(proj, sp.path_xy),
        "warnings": warnings,
    }


@router.post("/ramps/{ramp_id}/accel_lane", status_code=201)
def review_accel_lane(ramp_id: str, body: AccelReview, s: AppState = Depends(state)) -> dict[str, Any]:
    """確認（自動値どおり）・修正（始点・終点を指定）・加速車線ではない、を手動の値として保存する。
    修正は manual_edit に追記され（履歴・取り消し可）、自動の値は変わらない。"""
    r = _row(s, ramp_id)
    value: dict[str, Any] = {"action": body.action, "auto_length_m": r["accel_lane_len_m"]}
    if body.action == "confirmed":
        if r["accel_lane_status"] != "measured":
            raise HTTPException(
                422, "自動で測定できていないため確認できません。始点・終点を指定して修正してください"
            )
        value |= {
            "length_m": r["accel_lane_len_m"],
            "start": _pt(r["accel_start_lon"], r["accel_start_lat"]),
            "end": _pt(r["accel_end_lon"], r["accel_end_lat"]),
        }
    elif body.action == "corrected":
        if body.start is None or body.end is None:
            raise HTTPException(422, "修正には始点と終点が必要です")
        proj, sp = _span(s, r, body.start, body.end)
        limit = float(s.cfg.analysis["accel_lane"]["review_max_snap_m"])
        if max(sp.start_offset_m, sp.end_offset_m) > limit:
            raise HTTPException(422, f"始点・終点はランプまたは本線から {limit:g} m 以内で指定してください")
        value |= {
            "length_m": sp.length_m,
            "start": _lonlat(proj, sp.start_xy),
            "end": _lonlat(proj, sp.end_xy),
            "clicked": {"start": list(body.start), "end": list(body.end)},
        }
    edit = add_edit(s.store, "ramp", ramp_id, ACCEL_FIELD, value, s.cfg.user_name, body.comment)
    apply_accel_manual(s, ramp_id, edit)
    return get_accel_lane(ramp_id, s)


def apply_accel_manual(s: AppState, ramp_id: str, edit: dict[str, Any] | None) -> None:
    """手動の値を ramp の accel_manual_* 列に即時反映（再 build を待たない。自動の列には触れない）。"""
    vals = accel_manual_values(edit)
    cols = ", ".join(f"{k}=?" for k in vals)
    s.store.execute(f"UPDATE ramp SET {cols} WHERE ramp_id=?", [*vals.values(), ramp_id])
