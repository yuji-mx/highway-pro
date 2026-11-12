"""QueryDSL（§4.11.5, §9.12）。フィールドは許可リスト（レジストリ）で管理し、値はすべてパラメータバインドする。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ORDINAL = {"quality": ["unusable", "low", "medium", "high"]}
QUALITY_JA = {"high": "高", "medium": "中", "low": "低", "unusable": "使用不可"}
FLOW_JA = {
    "entry": "流入",
    "exit": "流出",
    "jct_link": "JCT連絡",
    "collector_distributor": "集散路",
    "facility_link": "SA・PA連絡",
    "toll_link": "料金所連絡",
    "two_way": "双方向",
}
OP_JA = {
    "eq": "＝",
    "ne": "≠",
    "gte": "≥",
    "lte": "≤",
    "gt": "＞",
    "lt": "＜",
    "in": "∈",
    "between": "が",
    "exists": "あり",
    "contains": "を含む",
}
TARGET_JA = {"ramp": "ランプ", "facility": "IC/JCT", "section": "区間"}
TARGET_ALIASES = {
    "ramps": "ramp",
    "facilities": "facility",
    "sections": "section",
    "ramp": "ramp",
    "facility": "facility",
    "section": "section",
}


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    sql: str
    kind: str  # num / str / bool / ordinal / list / text
    label: str
    unit: str = ""
    ordinal: str | None = None


def _num(sql: str, label: str, unit: str = "") -> Field:
    return Field(sql, "num", label, unit)


RAMP_FIELDS: dict[str, Field] = {
    "ramp_id": Field("r.ramp_id", "str", "ランプID"),
    "facility_id": Field("r.facility_id", "str", "施設ID"),
    "name": Field("f.name", "text", "施設名"),
    "route_ref": Field("f.route_refs", "list", "路線"),
    "admin_code": Field("f.admin_code", "str", "都道府県"),
    "country": Field("f.country", "str", "国"),
    "flow": Field("r.flow", "str", "区分"),
    "carriageway": Field("r.carriageway", "str", "方向"),
    "shape_class": Field("f.shape_class", "str", "IC/JCT形状"),
    "facility_type": Field("f.facility_type", "str", "施設種別"),
    "length_m": _num("r.length_m", "長さ", "m"),
    "min_radius_m": _num("r.min_radius_m", "最小R", "m"),
    "max_curvature": _num("r.max_curvature", "最大曲率", "1/m"),
    "curve_length_m": _num("r.curve_length_m", "カーブ長", "m"),
    "curvature_rate_max": _num("r.curvature_rate_max", "曲率変化率", "1/m²"),
    "grade_mean": _num("r.grade_mean", "平均勾配", "%"),
    "grade_inst_max": _num("r.grade_inst_max", "最大瞬間勾配", "%"),
    "cont_up_len_m": _num("r.cont_up_len_m", "連続上り距離", "m"),
    "elev_diff_m": _num("r.elev_diff_m", "標高差", "m"),
    "abs_elev_diff_m": _num("r.abs_elev_diff_m", "|標高差|", "m"),
    "z_max": _num("r.z_max", "最高標高", "m"),
    "lanes": _num("r.lanes", "車線数"),
    "maxspeed_kmh": _num("r.maxspeed_kmh", "制限速度", "km/h"),
    "accel_lane_len_m": _num("r.accel_lane_len_m", "加速車線長", "m"),
    "pre_merge_grade": _num("r.pre_merge_grade", "合流直前勾配", "%"),
    "end_grade": _num("r.end_grade", "終端勾配", "%"),
    "difficulty": _num("r.difficulty", "難易度候補値"),
    "is_loop": Field("r.is_loop", "bool", "ループ"),
    "dem_quality": Field("r.dem_quality", "ordinal", "DEM品質", ordinal="quality"),
    "osm_quality": Field("r.osm_quality", "ordinal", "OSM品質", ordinal="quality"),
    "quality_level": Field("r.quality_level", "ordinal", "品質", ordinal="quality"),
    "lon": _num("f.lon", "経度"),
    "lat": _num("f.lat", "緯度"),
    "small_r_uphill": _num(
        "CAST(json_extract(r.composite, '$.small_r_uphill') AS DOUBLE)", "小R区間内の最大上り", "%"
    ),
    "small_r_long": Field(
        "CAST(json_extract(r.composite, '$.small_r_long') AS BOOLEAN)", "bool", "小Rかつ長いランプ"
    ),
    "short_accel": Field(
        "CAST(json_extract(r.composite, '$.short_accel') AS BOOLEAN)", "bool", "短い加速車線"
    ),
    "end_uphill": Field("CAST(json_extract(r.composite, '$.end_uphill') AS BOOLEAN)", "bool", "終端上り"),
    "high_alt_steep": Field(
        "CAST(json_extract(r.composite, '$.high_alt_steep') AS BOOLEAN)", "bool", "高標高急勾配"
    ),
    "continuous_curve_relief": _num(
        "CAST(json_extract(r.composite, '$.continuous_curve_relief') AS DOUBLE)", "連続カーブ×高低差", "m²"
    ),
}
for _w in (10, 50, 100, 500):
    RAMP_FIELDS[f"grade_up_max_w{_w}"] = _num(f"r.grade_up_max_w{_w}", f"{_w}m上り", "%")
    RAMP_FIELDS[f"grade_down_max_w{_w}"] = _num(f"r.grade_down_max_w{_w}", f"{_w}m下り", "%")

FACILITY_FIELDS: dict[str, Field] = {
    "facility_id": Field("f.facility_id", "str", "施設ID"),
    "name": Field("f.name", "text", "名称"),
    "route_ref": Field("f.route_refs", "list", "路線"),
    "exit_number": Field("f.exit_number", "str", "出口番号"),
    "admin_code": Field("f.admin_code", "str", "都道府県"),
    "country": Field("f.country", "str", "国"),
    "facility_type": Field("f.facility_type", "str", "種別"),
    "shape_class": Field("f.shape_class", "str", "形状"),
    "shape_confidence": _num("f.shape_confidence", "信頼度"),
    "ramp_count": _num("f.ramp_count", "ランプ数"),
    "loop_ramp_count": _num("f.loop_ramp_count", "ループ数"),
    "has_loop": Field("(f.loop_ramp_count > 0)", "bool", "ループあり"),
    "is_half_ic": Field("f.is_half_ic", "bool", "ハーフIC"),
    "mean_ramp_length_m": _num("f.mean_ramp_length_m", "平均ランプ長", "m"),
    "min_radius_m": _num("f.min_radius_m", "最小R（施設内）", "m"),
    "grade_up_max_w100": _num("f.grade_up_max_w100", "最大上り（施設内）", "%"),
    "max_elev_diff_m": _num("f.max_elev_diff_m", "最大高低差", "m"),
    "connection_directions": _num("f.connection_directions", "接続方向数"),
    "quality_level": Field("f.quality_level", "ordinal", "品質", ordinal="quality"),
    "review_status": Field("f.review_status", "str", "確認状態"),
    "in_range": Field("f.in_range", "bool", "範囲内"),
    "lon": _num("f.lon", "経度"),
    "lat": _num("f.lat", "緯度"),
}
SECTION_FIELDS: dict[str, Field] = {
    "country": Field("substr(s.from_facility_id, 1, 2)", "str", "国"),
    "section_id": Field("s.section_id", "str", "区間ID"),
    "route_ref": Field("s.route_ref", "str", "路線"),
    "direction": Field("s.direction", "str", "方向"),
    "from_facility_id": Field("s.from_facility_id", "str", "起点"),
    "to_facility_id": Field("s.to_facility_id", "str", "終点"),
    "dist_standard_m": _num("s.dist_standard_m", "距離（標準）", "m"),
    "grade_mean": _num("s.grade_mean", "平均勾配", "%"),
    "grade_up_max_w100": _num("s.grade_up_max_w100", "最大上り(100m)", "%"),
    "grade_down_max_w100": _num("s.grade_down_max_w100", "最大下り(100m)", "%"),
    "grade_up_max_w1000": _num("s.grade_up_max_w1000", "最大上り(1000m)", "%"),
    "elev_gain_m": _num("s.elev_gain_m", "累積上昇", "m"),
    "gain_per_km": _num("s.gain_per_km", "累積上昇/km", "m/km"),
    "cont_up_len_m": _num("s.cont_up_len_m", "連続上り距離", "m"),
    "min_radius_m": _num("s.min_radius_m", "最小R", "m"),
    "tunnel_len_m": _num("s.tunnel_len_m", "トンネル延長", "m"),
    "bridge_len_m": _num("s.bridge_len_m", "橋梁延長", "m"),
    "z_max": _num("s.z_max", "最高標高", "m"),
    "quality_level": Field("s.quality_level", "ordinal", "品質", ordinal="quality"),
}
REGISTRY = {"ramp": RAMP_FIELDS, "facility": FACILITY_FIELDS, "section": SECTION_FIELDS}
FROM = {
    "ramp": "ramp r LEFT JOIN facility f ON r.facility_id = f.facility_id",
    "facility": "facility f",
    "section": "section s",
}
SELECT = {
    "ramp": "r.* EXCLUDE (geom), f.name AS facility_name, f.facility_type, f.route_refs, f.lon AS facility_lon, "
    "f.lat AS facility_lat",
    "facility": "f.* EXCLUDE (geom, hull)",
    "section": "s.* EXCLUDE (geom)",
}
NOT_EXCLUDED = {
    "ramp": "coalesce(r.excluded, false) = false",
    "facility": "coalesce(f.excluded, false) = false",
    "section": "coalesce(s.excluded, false) = false",
}
OPS = {"eq", "ne", "in", "gte", "lte", "gt", "lt", "between", "exists", "contains"}


def normalize_target(t: str) -> str:
    if t not in TARGET_ALIASES:
        raise QueryError(f"検索対象が不正です: {t}")
    return TARGET_ALIASES[t]


def _ord(v: Any, scale: str) -> int:
    levels = ORDINAL[scale]
    if v not in levels:
        raise QueryError(f"順序付きカテゴリの値が不正です: {v}（{', '.join(levels)}）")
    return levels.index(v)


def _cond_sql(target: str, c: dict[str, Any], params: list[Any]) -> str:
    if "all" in c or "any" in c:
        return _group_sql(target, c, params)
    name = c.get("field")
    reg = REGISTRY[target]
    if name not in reg:
        raise QueryError(f"検索できないフィールドです: {name}")
    f = reg[name]
    op = c.get("op", "eq")
    if op not in OPS:
        raise QueryError(f"演算子が不正です: {op}")
    v: Any = c.get("value")
    if op == "exists":
        return f"({f.sql} IS {'NOT ' if v in (None, True) else ''}NULL)"
    if f.kind == "ordinal":
        levels = ORDINAL[f.ordinal or "quality"]
        expr = "CASE " + " ".join(f"WHEN {f.sql} = '{lv}' THEN {i}" for i, lv in enumerate(levels)) + " END"
        if op in ("in",):
            params.extend(v)
            return f"({f.sql} IN ({', '.join('?' for _ in v)}))"
        if op == "between":
            params.extend([_ord(v[0], f.ordinal or "quality"), _ord(v[1], f.ordinal or "quality")])
            return f"({expr} BETWEEN ? AND ?)"
        params.append(_ord(v, f.ordinal or "quality"))
        return f"({expr} {_sqlop(op)} ?)"
    if f.kind == "list":
        if op in ("eq", "contains"):
            params.append(v)
            return f"list_contains({f.sql}, ?)"
        if op == "in":
            params.extend(v)
            return "(" + " OR ".join(f"list_contains({f.sql}, ?)" for _ in v) + ")"
        raise QueryError(f"{name} には eq / in / contains のみ使えます")
    if f.kind == "text":
        if op in ("eq", "contains"):
            from roadmri.config import get_config
            from roadmri.network.names import NameNormalizer

            cfg = get_config()
            nn = NameNormalizer(cfg.name_normalization, cfg.name_aliases)
            stem = nn.normalize(str(v))
            params.extend([f"%{v}%", f"%{stem.stem if stem else v}%"])
            norm = "f.normalized_name"
            return f"({f.sql} ILIKE ? OR {norm} ILIKE ?)"
        raise QueryError(f"{name} には contains のみ使えます")
    if op == "in":
        if not isinstance(v, list) or not v:
            raise QueryError("in には配列を指定してください")
        params.extend(v)
        return f"({f.sql} IN ({', '.join('?' for _ in v)}))"
    if op == "between":
        if not isinstance(v, list) or len(v) != 2:
            raise QueryError("between には [下限, 上限] を指定してください")
        params.extend(v)
        return f"({f.sql} BETWEEN ? AND ?)"
    if op == "contains":
        params.append(f"%{v}%")
        return f"(CAST({f.sql} AS VARCHAR) ILIKE ?)"
    if f.kind == "num" and not isinstance(v, (int, float)):
        raise QueryError(f"{name} には数値を指定してください")
    params.append(v)
    return f"({f.sql} {_sqlop(op)} ?)"


def _sqlop(op: str) -> str:
    return {"eq": "=", "ne": "<>", "gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[op]


def _group_sql(target: str, w: dict[str, Any], params: list[Any]) -> str:
    parts = []
    alls = [_cond_sql(target, c, params) for c in w.get("all", []) or []]
    anys = [_cond_sql(target, c, params) for c in w.get("any", []) or []]
    if alls:
        parts.append("(" + " AND ".join(alls) + ")")
    if anys:
        parts.append("(" + " OR ".join(anys) + ")")
    return "(" + " AND ".join(parts) + ")" if parts else "TRUE"


def compile_query(q: dict[str, Any], facility_ids: set[str] | None = None) -> tuple[str, list[Any]]:
    target = normalize_target(str(q.get("target", "ramp")))
    params: list[Any] = []
    where = [_group_sql(target, q.get("where", {}) or {}, params), NOT_EXCLUDED[target]]
    if facility_ids is not None:
        ids = sorted(facility_ids)
        if not ids:
            where.append("FALSE")
        else:
            ph = ", ".join("?" for _ in ids)
            if target == "section":
                where.append(f"(s.from_facility_id IN ({ph}) OR s.to_facility_id IN ({ph}))")
                params.extend(ids + ids)
            else:
                col = "r.facility_id" if target == "ramp" else "f.facility_id"
                where.append(f"({col} IN ({ph}))")
                params.extend(ids)
    order = []
    for s in q.get("sort", []) or []:
        fld = REGISTRY[target].get(s.get("field"))
        if fld is None:
            raise QueryError(f"並べ替えできないフィールドです: {s.get('field')}")
        order.append(f"{fld.sql} {'DESC' if s.get('dir') == 'desc' else 'ASC'} NULLS LAST")
    key = {"ramp": "r.ramp_id", "facility": "f.facility_id", "section": "s.section_id"}[target]
    order.append(key)
    limit = int(q.get("limit", 500) or 500)
    if limit < 1 or limit > 100_000:
        raise QueryError("limit は 1〜100000 で指定してください")
    sql = f"SELECT {SELECT[target]} FROM {FROM[target]} WHERE {' AND '.join(where)} ORDER BY {', '.join(order)} LIMIT {limit}"
    return sql, params


def _val_text(f: Field | None, v: Any, field: str) -> str:
    if isinstance(v, list):
        return "・".join(_val_text(f, x, field) for x in v)
    if field == "flow":
        return FLOW_JA.get(str(v), str(v))
    if f is not None and f.kind == "ordinal":
        return QUALITY_JA.get(str(v), str(v))
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if isinstance(v, float):
        v = round(v, 4)
    return f"{v}{(' ' + f.unit) if f and f.unit and f.unit not in ('%',) else (f.unit if f else '')}"


def cond_text(target: str, c: dict[str, Any]) -> str:
    if "all" in c or "any" in c:
        return "（" + group_text(target, c) + "）"
    reg = REGISTRY[target]
    f = reg.get(c.get("field", ""))
    label = f.label if f else str(c.get("field"))
    op = c.get("op", "eq")
    v: Any = c.get("value")
    if op == "between":
        return f"{label} {_val_text(f, v[0], c['field'])}〜{_val_text(f, v[1], c['field'])}"
    if op == "exists":
        return f"{label}{'あり' if v in (None, True) else 'なし'}"
    if op == "contains":
        return f"{label}に「{v}」を含む"
    if f is not None and f.kind == "ordinal" and op == "gte":
        return f"{label} {_val_text(f, v, c['field'])}以上"
    return f"{label} {OP_JA.get(op, op)} {_val_text(f, v, c['field'])}"


def group_text(target: str, w: dict[str, Any]) -> str:
    parts = []
    alls = [cond_text(target, c) for c in w.get("all", []) or []]
    anys = [cond_text(target, c) for c in w.get("any", []) or []]
    if alls:
        parts.append(" かつ ".join(alls))
    if anys:
        parts.append("（" + " または ".join(anys) + "）" if alls else " または ".join(anys))
    return " かつ ".join(parts) if parts else "条件なし"


def to_text(q: dict[str, Any]) -> str:
    """QueryDSL を人が読める日本語文に変換する。"""
    target = normalize_target(str(q.get("target", "ramp")))
    s = f"{TARGET_JA[target]}：{group_text(target, q.get('where', {}) or {})}"
    if q.get("range"):
        from roadmri.analysis.ranges import range_label

        s += f"（範囲：{range_label(q['range'])}）"
    sorts = []
    for so in q.get("sort", []) or []:
        f = REGISTRY[target].get(so.get("field"))
        sorts.append(f"{f.label if f else so.get('field')}の{'降順' if so.get('dir') == 'desc' else '昇順'}")
    if sorts:
        s += "、" + "・".join(sorts)
    if q.get("limit"):
        s += f"、上限 {q['limit']} 件"
    return s


def warnings(q: dict[str, Any]) -> list[str]:
    """最大瞬間勾配で候補を抽出する条件には警告を出す（§4.8.3）。"""
    out = []

    def walk(w: dict[str, Any]) -> bool:
        for c in (w.get("all", []) or []) + (w.get("any", []) or []):
            if "all" in c or "any" in c:
                if walk(c):
                    return True
            elif c.get("field") in ("grade_inst_max",):
                return True
        return False

    if walk(q.get("where", {}) or {}):
        out.append("最大瞬間勾配は DEM のノイズに敏感なため、候補抽出には 100 m 窓平均勾配の使用を推奨します")
    return out


def fields_catalog() -> dict[str, list[dict[str, Any]]]:
    return {
        t: [{"field": k, "label": f.label, "unit": f.unit, "kind": f.kind} for k, f in reg.items()]
        for t, reg in REGISTRY.items()
    }
