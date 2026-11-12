"""分類ルールの評価（条件式）。"""

from __future__ import annotations

from typing import Any

OP_TEXT = {"eq": "＝", "ne": "≠", "gte": "≥", "lte": "≤", "gt": "＞", "lt": "＜", "between": "〜", "in": "∈"}

FEATURE_JA = {
    "mainline_count": "接続本線数",
    "loop_ramp_count": "ループランプ数",
    "general_road_connections": "一般道接続点数",
    "ramp_count": "本線接続ランプ数",
    "toll_on_general_side": "料金所が一般道側",
    "crossing_road_count": "交差道路数",
    "toll_present": "料金所あり",
    "has_general_road": "一般道接続あり",
    "is_half_ic": "ハーフIC",
    "is_smart": "ETC専用",
    "is_jct_combined": "IC＋JCT複合",
    "terminating_routes": "終端路線数",
    "mainline_angle_deg": "本線交差角",
    "jct_link_count": "JCT連絡ランプ数",
    "connection_directions": "接続方向数",
    "sweeping_jct_ramps": "旋回ランプ数",
    "layer_count": "立体レベル数",
    "facility_type": "施設種別",
}


def evaluate(cond: dict[str, Any], features: dict[str, Any]) -> bool:
    v = features.get(cond["feature"])
    t = cond["value"]
    op = cond["op"]
    if v is None:
        return False
    if op == "eq":
        return bool(v == t)
    if op == "ne":
        return bool(v != t)
    if op == "in":
        return v in t
    if op == "gte":
        return bool(v >= t)
    if op == "lte":
        return bool(v <= t)
    if op == "gt":
        return bool(v > t)
    if op == "lt":
        return bool(v < t)
    if op == "between":
        return bool(t[0] <= v <= t[1])
    raise ValueError(f"未知の演算子: {op}")


def describe(cond: dict[str, Any], features: dict[str, Any]) -> str:
    name = FEATURE_JA.get(cond["feature"], cond["feature"])
    t = cond["value"]
    tv = f"{t[0]}〜{t[1]}" if cond["op"] == "between" else str(t)
    return f"{name} {OP_TEXT.get(cond['op'], cond['op'])} {tv}（実測 {features.get(cond['feature'])}）"
