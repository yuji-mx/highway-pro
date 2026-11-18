"""経路異常フラグの定義（§4.5.5）。判定ロジックは sections.py。"""

from __future__ import annotations

ANOMALY_FLAGS: dict[str, tuple[str, str]] = {
    "NO_PATH": ("error", "本線グラフで経路が見つかりません"),
    "ONEWAY_VIOLATION": ("error", "無向なら到達できますが、一方通行を守ると到達できません"),
    "VIA_NON_MOTORWAY": ("warn", "本線外の道路を経由しないと到達できません"),
    "EXCESSIVE_DETOUR": ("warn", "経路長が代表点間の直線距離に比べて長すぎます"),
    "NAME_COLLISION": ("warn", "同名施設が複数あり、接続の候補が曖昧です"),
    "MAINLINE_GAP": ("error", "本線チェーンが途中で切れています（未接続の本線端点が近接）"),
    "NETWORK_DISCONTINUITY": ("warn", "ネットワークの連結成分が分断しています"),
    "OUT_OF_RANGE": ("info", "終点または起点が範囲外参照の施設です"),
}


# 画面の一覧に出す短い名称（SPEC §4.5.5 の条件の言い換え。詳しい説明は ANOMALY_FLAGS の文、元のコードも併記する）
ANOMALY_LABELS: dict[str, str] = {
    "NO_PATH": "経路なし",
    "ONEWAY_VIOLATION": "一方通行で到達不可",
    "VIA_NON_MOTORWAY": "本線外を経由",
    "EXCESSIVE_DETOUR": "遠回り（直線距離比）",
    "NAME_COLLISION": "同名施設あり",
    "MAINLINE_GAP": "本線の途切れ",
    "NETWORK_DISCONTINUITY": "ネットワークの分断",
    "OUT_OF_RANGE": "範囲外の施設",
}


def describe() -> dict[str, dict[str, str]]:
    """画面用：コード → 重大度・短い名称・説明。"""
    return {
        code: {"severity": sev, "label": ANOMALY_LABELS.get(code, code), "message": msg}
        for code, (sev, msg) in ANOMALY_FLAGS.items()
    }


def severity(code: str) -> str:
    return ANOMALY_FLAGS.get(code, ("info", ""))[0]


def message_ja(code: str) -> str:
    return ANOMALY_FLAGS.get(code, ("info", code))[1]
