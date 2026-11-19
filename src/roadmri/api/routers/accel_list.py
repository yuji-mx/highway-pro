"""加速車線一覧（ADR-0018）：自動の値・手動の値・差（手動−自動）・信頼度・確認状態を並べて比較する。

自動の測定の精度と偏りを把握するための画面。検索・統計・難易度は引き続き自動の値を使う（ADR-0017）。
一覧・要約・CSV は同じ絞り込み条件を使う。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from roadmri.api.deps import AppState, state
from roadmri.api.util import records
from roadmri.network.accel_display import STATE_SQL, display
from roadmri.network.accel_lane import REASONS

router = APIRouter(tags=["ramps"])

# 一覧の列（SQL の式）。「差」は手動−自動。確認状態は手動の種類、なければ unreviewed
COLUMNS: dict[str, str] = {
    "ramp_id": "r.ramp_id",
    "facility_id": "r.facility_id",
    "facility_name": "f.name",
    "country": "f.country",
    "route_ref": "json_extract_string(r.to_ref, '$.route_ref')",
    "flow": "r.flow",
    "carriageway": "r.carriageway",
    # 以前の DB では判定保留の理由も excluded だったので、理由コードから決め直す（ADR-0020）
    "auto_status": "CASE WHEN r.accel_lane_status = 'measured' THEN 'measured' "
    "WHEN r.accel_lane_reason IN ('lane_addition', 'auxiliary_to_exit') THEN 'excluded' ELSE 'pending' END",
    "display_state": STATE_SQL,
    "auto_reason": "r.accel_lane_reason",
    "auto_method": "r.accel_lane_method",
    "auto_confidence": "r.accel_lane_confidence",
    "auto_notes": "r.accel_lane_notes",
    "auto_len_m": "r.accel_lane_len_m",
    "manual_len_m": "r.accel_manual_len_m",
    "diff_m": "r.accel_manual_len_m - r.accel_lane_len_m",
    "abs_diff_m": "abs(r.accel_manual_len_m - r.accel_lane_len_m)",
    "review_status": "coalesce(r.accel_manual_status, 'unreviewed')",
    "manual_reason": "r.accel_manual_reason",
    "manual_pending": "r.accel_manual_pending",
    "start_method": "r.accel_start_method",
    "start_confidence": "r.accel_start_confidence",
    "start_pending": "r.accel_start_pending",
    "end_method": "r.accel_end_method",
    "end_confidence": "r.accel_end_confidence",
    "end_pending": "r.accel_end_pending",
    "manual_user": "r.accel_manual_user",
    "manual_at": "r.accel_manual_at",
    "manual_note": "r.accel_manual_note",
    "auto_start_lon": "r.accel_start_lon",
    "auto_start_lat": "r.accel_start_lat",
    "auto_end_lon": "r.accel_end_lon",
    "auto_end_lat": "r.accel_end_lat",
    "manual_start_lon": "r.accel_manual_start_lon",
    "manual_start_lat": "r.accel_manual_start_lat",
    "manual_end_lon": "r.accel_manual_end_lon",
    "manual_end_lat": "r.accel_manual_end_lat",
}
SORTABLE = {
    "display_state",
    "ramp_id", "facility_name", "route_ref", "flow", "auto_status", "auto_method", "auto_confidence",
    "auto_len_m", "manual_len_m", "diff_m", "abs_diff_m", "review_status", "manual_at",
}  # fmt: skip
REVIEW = {"unreviewed", "reviewed", "confirmed", "corrected", "not_accel_lane", "pending"}
STATES = {"provisional", "pending", "excluded", "confirmed"}


@dataclass
class AccelFilter:
    """一覧・要約・CSV で共通の絞り込み条件。"""

    q: str | None = None
    country: str | None = None
    flow: str | None = None
    auto_status: str | None = None
    reason: str | None = None
    method: str | None = None
    confidence: str | None = None
    review: str | None = None
    state: str | None = (
        None  # 表示状態：provisional（暫定値）/ pending（判定保留）/ excluded（対象外）/ confirmed（確認済み）
    )
    min_abs_diff_m: float | None = None
    sort: str = "facility_name"
    order: str = "asc"

    def sql(self) -> tuple[str, list[Any], str]:
        w = ["r.accel_lane_status IS NOT NULL"]
        p: list[Any] = []
        for col, v in (
            ("f.country", self.country),
            ("r.flow", self.flow),
            (f"({COLUMNS['auto_status']})", self.auto_status),
            (f"({STATE_SQL})", self.state),
            ("r.accel_lane_reason", self.reason),
            ("r.accel_lane_method", self.method),
            ("r.accel_lane_confidence", self.confidence),
        ):
            if v:
                w.append(f"{col} = ?")
                p.append(v)
        if self.q:
            w.append("(f.name ILIKE ? OR f.name_en ILIKE ? OR r.ramp_id ILIKE ? OR r.facility_id ILIKE ?)")
            p += [f"%{self.q}%"] * 4
        if self.review:
            if self.review not in REVIEW:
                raise HTTPException(422, f"確認状態の指定が不正です: {self.review}")
            if self.review == "reviewed":
                w.append("r.accel_manual_status IS NOT NULL")
            else:
                w.append(f"{COLUMNS['review_status']} = ?")
                p.append(self.review)
        if self.state and self.state not in STATES:
            raise HTTPException(422, f"表示状態の指定が不正です: {self.state}")
        if self.min_abs_diff_m is not None:
            w.append(f"{COLUMNS['abs_diff_m']} >= ?")
            p.append(self.min_abs_diff_m)
        if self.sort not in SORTABLE:
            raise HTTPException(422, f"並べ替えできない項目です: {self.sort}")
        direction = "DESC" if self.order == "desc" else "ASC"
        return " AND ".join(w), p, f"{self.sort} {direction} NULLS LAST, ramp_id"


def _select(f: AccelFilter, extra: str = "") -> tuple[str, list[Any]]:
    w, p, order = f.sql()
    cols = ", ".join(f"{expr} AS {name}" for name, expr in COLUMNS.items())
    sql = (
        f"SELECT * FROM (SELECT {cols} FROM ramp r LEFT JOIN facility f ON r.facility_id = f.facility_id "
        f"WHERE {w}) ORDER BY {order} {extra}"
    )
    return sql, p


def _with_text(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        r["auto_reason_text"] = REASONS.get(str(r.get("auto_reason"))) if r.get("auto_reason") else None
        d = display(_as_ramp_row(r)) or {}
        r["display_label"] = d.get("label")
        r["display_length_m"] = d.get("length_m")
        r["provisional_length_m"] = d.get("provisional_length_m")
        r["endpoints"] = d.get("endpoints")
    return rows


def _as_ramp_row(r: dict[str, Any]) -> dict[str, Any]:
    """一覧の行を display() が読む ramp 表の列名に戻す。"""
    return {
        "accel_lane_status": r.get("auto_status"),
        "accel_lane_reason": r.get("auto_reason"),
        "accel_lane_len_m": r.get("auto_len_m"),
        "accel_manual_status": None if r.get("review_status") == "unreviewed" else r.get("review_status"),
        "accel_manual_len_m": r.get("manual_len_m"),
        "accel_manual_reason": r.get("manual_reason"),
        "accel_manual_pending": r.get("manual_pending"),
        **{
            f"accel_{w}_{k}": r.get(f"{w}_{k}")
            for w in ("start", "end")
            for k in ("method", "confidence", "pending")
        },
        **{f"accel_{w}_{a}": r.get(f"auto_{w}_{a}") for w in ("start", "end") for a in ("lon", "lat")},
    }


@router.get("/accel_lanes")
def list_accel_lanes(
    f: Annotated[AccelFilter, Depends()],
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=10000),
    s: AppState = Depends(state),
) -> dict[str, Any]:
    w, p, _ = f.sql()
    total = int(
        s.store.scalar(
            f"SELECT count(*) FROM ramp r LEFT JOIN facility f ON r.facility_id = f.facility_id WHERE {w}", p
        )
        or 0
    )
    sql, p2 = _select(f, f"LIMIT {limit} OFFSET {offset}")
    return {"total": total, "items": _with_text(records(s.store.df(sql, p2)))}


def _stats(d: pd.Series) -> dict[str, Any]:
    """差（手動−自動）の要約。偏り＝平均、ばらつき＝平均絶対誤差・二乗平均平方根。"""
    d = d.dropna().astype(float)
    if d.empty:
        return {"n": 0}
    return {
        "n": len(d),
        "bias_m": float(d.mean()),
        "median_m": float(d.median()),
        "mae_m": float(d.abs().mean()),
        "rmse_m": float(math.sqrt((d**2).mean())),
        "p90_abs_m": float(d.abs().quantile(0.9)),
        "min_m": float(d.min()),
        "max_m": float(d.max()),
        "share_manual_longer": float((d > 0).mean()),
    }


@router.get("/accel_lanes/summary")
def accel_lanes_summary(f: Annotated[AccelFilter, Depends()], s: AppState = Depends(state)) -> dict[str, Any]:
    """件数（自動の判定・確認状態）、手動と自動の差の要約（全体・信頼度別・判定方法別）、判定の食い違い。"""
    sql, p = _select(f)
    df = s.store.df(sql, p)
    both = df[df["auto_len_m"].notna() & df["manual_len_m"].notna()]
    out: dict[str, Any] = {
        "total": len(df),
        "by_auto_status": {str(k): int(v) for k, v in df["auto_status"].value_counts().items()},
        "by_review_status": {str(k): int(v) for k, v in df["review_status"].value_counts().items()},
        "difference": _stats(both["diff_m"]),
        "by_confidence": {str(k): _stats(g["diff_m"]) for k, g in both.groupby("auto_confidence")},
        "by_method": {str(k): _stats(g["diff_m"]) for k, g in both.groupby("auto_method")},
        # 自動では測ったが、確認で「加速車線ではない」とされたもの／自動では除外したが、手動で測ったもの
        "auto_measured_but_not_accel_lane": int(
            ((df["auto_status"] == "measured") & (df["review_status"] == "not_accel_lane")).sum()
        ),
        "auto_excluded_but_manual_measured": int(
            (df["auto_status"].isin(["excluded", "pending"]) & df["manual_len_m"].notna()).sum()
        ),
        # 散布図用（自動 × 手動。両方あるもの）
        "points": [
            {
                "ramp_id": r.ramp_id,
                "auto_len_m": float(r.auto_len_m),
                "manual_len_m": float(r.manual_len_m),
                "confidence": r.auto_confidence,
                "review_status": r.review_status,
            }
            for r in both.itertuples()
        ],
    }
    return out


CSV_HEADERS = {
    "ramp_id": "ランプID",
    "facility_id": "施設ID",
    "facility_name": "施設名",
    "country": "国",
    "route_ref": "路線",
    "flow": "種類",
    "carriageway": "方向",
    "auto_status": "自動_判定",
    "auto_reason": "自動_除外理由コード",
    "auto_reason_text": "自動_除外理由",
    "auto_method": "自動_判定方法",
    "auto_confidence": "自動_信頼度",
    "auto_notes": "自動_注記",
    "auto_len_m": "自動_長さ_m",
    "manual_len_m": "手動_長さ_m",
    "diff_m": "差_手動-自動_m",
    "abs_diff_m": "差の絶対値_m",
    "review_status": "確認状態",
    "manual_user": "確認者",
    "manual_at": "確認日時",
    "manual_note": "メモ",
    "auto_start_lon": "自動_始点_経度",
    "auto_start_lat": "自動_始点_緯度",
    "auto_end_lon": "自動_終点_経度",
    "auto_end_lat": "自動_終点_緯度",
    "manual_start_lon": "手動_始点_経度",
    "manual_start_lat": "手動_始点_緯度",
    "manual_end_lon": "手動_終点_経度",
    "manual_end_lat": "手動_終点_緯度",
    # 以下は ADR-0020 で追加した列（既存の列の並びは変えない）
    "display_state": "表示状態",
    "start_method": "自動_始点_方法",
    "start_confidence": "自動_始点_信頼度",
    "start_pending": "自動_始点_保留理由",
    "end_method": "自動_終点_方法",
    "end_confidence": "自動_終点_信頼度",
    "end_pending": "自動_終点_保留理由",
    "manual_reason": "手動_対象外の理由",
    "manual_pending": "手動_判定保留",
}


@router.get("/accel_lanes.csv")
def accel_lanes_csv(f: Annotated[AccelFilter, Depends()], s: AppState = Depends(state)) -> Response:
    """一覧と同じ条件の全件（UTF-8 BOM 付き、Excel で開ける）。長さは m（表示単位に関係なく）。"""
    sql, p = _select(f)
    df = s.store.df(sql, p)
    df.insert(
        df.columns.get_loc("auto_reason") + 1,
        "auto_reason_text",
        [REASONS.get(str(v)) if v else None for v in df["auto_reason"]],
    )
    body = df[list(CSV_HEADERS)].rename(columns=CSV_HEADERS).to_csv(index=False)
    return Response(
        "﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="accel_lanes.csv"'},
    )
