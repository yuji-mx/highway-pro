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


def severity(code: str) -> str:
    return ANOMALY_FLAGS.get(code, ("info", ""))[0]


def message_ja(code: str) -> str:
    return ANOMALY_FLAGS.get(code, ("info", code))[1]
