"""品質フラグ（§4.10.2）。"""

from __future__ import annotations

from typing import Any

from roadmri.network.anomalies import ANOMALY_FLAGS

FLAG_DEFS: dict[str, tuple[str, str]] = {
    "NAME_MISSING": ("warn", "施設名称がありません"),
    "EXIT_NUMBER_MISSING": ("info", "出口番号がありません"),
    "RAMP_MISSING": ("error", "ランプが見つかりません（欠落の疑い）"),
    "NETWORK_GAP": ("error", "本線ネットワークに分断があります"),
    "UNNATURAL_DISTANCE": ("warn", "区間距離が不自然です"),
    "EXTREME_GRADE": ("warn", "100 m 窓平均勾配の絶対値が閾値を超えています"),
    "EXTREME_CURVATURE": ("warn", "最小曲率半径が閾値未満です"),
    "DEM_MISSING": ("warn", "DEM 標高が欠損している点があります"),
    "TUNNEL_DEM": ("info", "トンネル区間の DEM 値は地表高のため勾配計算から除外しました"),
    "ELEVATED_GROUND_MISMATCH": ("info", "高架・橋梁区間で地表標高と補正後標高の差が大きい"),
    "CARRIAGEWAY_MISCONNECT": ("warn", "ランプの接続 carriageway が判定できません"),
    "CARRIAGEWAY_ELEV_DIFF": ("info", "上下線の標高差が大きい区間があります"),
    "DUPLICATE_FACILITY": ("warn", "同名の施設が近接して複数あります（統合漏れの疑い）"),
    "LOW_CLASSIFICATION_CONFIDENCE": ("warn", "形状分類の信頼度が低い"),
    "TYPE_NAME_MISMATCH": ("warn", "名称上の種別とネットワーク判定が一致しません"),
    **ANOMALY_FLAGS,
}


def make_flag(
    target_type: str,
    target_id: str,
    code: str,
    run_id: str,
    detail: dict[str, Any] | None = None,
    message: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    sev, msg = FLAG_DEFS.get(code, ("info", code))
    return {
        "flag_id": f"{target_type}:{target_id}:{code}",
        "target_type": target_type,
        "target_id": target_id,
        "code": code,
        "severity": severity or sev,
        "message_ja": message or msg,
        "detail": detail or {},
        "run_id": run_id,
    }
