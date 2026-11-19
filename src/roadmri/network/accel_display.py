"""加速車線の表示状態（ADR-0020）：暫定値・判定保留・対象外・確認済みと、始点・終点ごとの根拠。

画面（ランプ詳細・道路MRIビュー・加速車線一覧・施設詳細）は、ここで決めた状態と文言をそのまま使う。
自動の値（`accel_lane_len_m` など）は変えない（検索・統計・難易度は自動の値のまま。ADR-0017）。
"""

from __future__ import annotations

import json
from typing import Any

from roadmri.network.accel_lane import ENDPOINT_METHODS, EXCLUDED_REASONS, REASONS, status_of

# 手動で判定保留にするときの理由（端点ごと）
MANUAL_PENDING_REASONS: dict[str, str] = {
    "shadow": "影で目印が見えない",
    "bridge": "橋・高架で目印が見えない",
    "out_of_image": "航空写真の範囲外",
    "adjacent_ramp": "隣接するランプの線と重なる",
    "construction": "工事中・撮影時期による違い",
    "other": "その他（メモ参照）",
}
# 対象外の理由（手動・自動共通）
EXCLUSION_LABELS: dict[str, str] = {
    "lane_addition": "対象外・車線追加",
    "auxiliary_to_exit": "対象外・出口まで続く付加車線",
    "other": "対象外",
}
STATE_LABELS: dict[str, str] = {
    "provisional": "暫定値",
    "pending": "判定保留",
    "excluded": "対象外",
    "confirmed": "確認済み",
}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
ENDPOINT_LABELS = {"start": "始点", "end": "終点"}

# 加速車線一覧の絞り込み・並べ替え用（display() と同じ判定を SQL で）
STATE_SQL = """CASE
  WHEN r.accel_lane_status IS NULL THEN NULL
  WHEN r.accel_manual_status IN ('confirmed', 'corrected') THEN 'confirmed'
  WHEN r.accel_manual_status = 'not_accel_lane' THEN 'excluded'
  WHEN r.accel_manual_status = 'pending' THEN 'pending'
  WHEN r.accel_lane_status = 'measured' THEN 'provisional'
  WHEN r.accel_lane_reason IN ('lane_addition', 'auxiliary_to_exit') THEN 'excluded'
  ELSE 'pending' END"""


def _pending_reason_text(code: str | None) -> str | None:
    if not code:
        return None
    if code in MANUAL_PENDING_REASONS:
        return MANUAL_PENDING_REASONS[code]
    return REASONS.get(code, code).removeprefix("判定保留：")


def endpoint(row: dict[str, Any], which: str) -> dict[str, Any]:
    """自動の始点・終点の根拠。記録のない以前の DB は evaluated=false（画面では「未評価」）。"""
    method = row.get(f"accel_{which}_method")
    pending = row.get(f"accel_{which}_pending")
    if pending is None and row.get("accel_lane_status") is not None:
        # 以前の DB：理由コードから、確定できなかった端点を補う
        from roadmri.network.accel_lane import PENDING_ENDPOINTS

        reason = row.get("accel_lane_reason")
        if status_of(reason) == "pending" and which in PENDING_ENDPOINTS.get(str(reason), ()):
            pending = reason
    lon, lat = row.get(f"accel_{which}_lon"), row.get(f"accel_{which}_lat")
    return {
        "which": which,
        "label": ENDPOINT_LABELS[which],
        "lonlat": [lon, lat] if lon is not None and lat is not None else None,
        "method": method,
        "method_text": ENDPOINT_METHODS.get(str(method)) if method else None,
        "confidence": row.get(f"accel_{which}_confidence"),
        "confidence_text": CONFIDENCE_LABELS.get(str(row.get(f"accel_{which}_confidence"))),
        "pending": pending,
        "pending_text": _pending_reason_text(pending),
        "evaluated": method is not None or pending is not None,
    }


def _manual_pending(row: dict[str, Any]) -> dict[str, str]:
    v = row.get("accel_manual_pending")
    if not v:
        return {}
    try:
        d = json.loads(v) if isinstance(v, str) else dict(v)
    except (ValueError, TypeError):
        return {}
    return {k: str(x) for k, x in d.items() if k in ENDPOINT_LABELS}


def display(row: dict[str, Any]) -> dict[str, Any] | None:
    """ランプ 1 本の加速車線の表示状態。本線への合流でなければ None。"""
    status = row.get("accel_lane_status")
    if status is None:
        return None
    reason = row.get("accel_lane_reason")
    auto_status = status_of(reason) if status != "measured" else "measured"
    auto_len = row.get("accel_lane_len_m") if auto_status == "measured" else None
    ends = {w: endpoint(row, w) for w in ("start", "end")}
    manual = row.get("accel_manual_status")
    out: dict[str, Any] = {
        "auto_status": auto_status,
        "auto_reason": reason,
        "auto_reason_text": REASONS.get(str(reason)) if reason else None,
        "endpoints": ends,
        "source": "manual" if manual else "auto",
        "provisional_length_m": auto_len,  # 地図データ（OSM）による暫定値
    }
    if manual in ("confirmed", "corrected"):
        detail = "自動値どおりと確認" if manual == "confirmed" else "手動で修正"
        return {
            **out,
            "state": "confirmed",
            "label": f"確認済み（{detail}）",
            "length_m": row.get("accel_manual_len_m"),
        }
    if manual == "not_accel_lane":
        r = row.get("accel_manual_reason") or "other"
        return {**out, "state": "excluded", "label": EXCLUSION_LABELS.get(str(r), "対象外"), "length_m": None,
                "exclusion_reason": r}  # fmt: skip
    if manual == "pending":
        mp = _manual_pending(row)
        for w, code in mp.items():
            ends[w] = {
                **ends[w],
                "pending": code,
                "pending_text": _pending_reason_text(code),
                "evaluated": True,
            }
        parts = [f"{ENDPOINT_LABELS[w]}：{_pending_reason_text(c)}" for w, c in mp.items()]
        return {**out, "state": "pending", "label": "判定保留（" + "、".join(parts) + "）" if parts else "判定保留",
                "length_m": None}  # fmt: skip
    if auto_status == "measured":
        return {**out, "state": "provisional", "label": "暫定値（地図データ）", "length_m": auto_len}
    if auto_status == "excluded":
        return {**out, "state": "excluded", "label": EXCLUSION_LABELS.get(str(reason), "対象外"), "length_m": None,
                "exclusion_reason": reason}  # fmt: skip
    parts = [f"{ENDPOINT_LABELS[w]}：{e['pending_text']}" for w, e in ends.items() if e["pending"]]
    return {**out, "state": "pending", "label": "判定保留（" + "、".join(parts) + "）" if parts else "判定保留",
            "length_m": None}  # fmt: skip


__all__ = ["EXCLUDED_REASONS", "display", "endpoint"]
