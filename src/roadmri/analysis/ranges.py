"""RangeSpec（§9.11）。範囲のジオメトリ化と、範囲に含まれる施設・ランプ・区間の解決。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from shapely.geometry import Point, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from roadmri.geometry.projection import geodesic_buffer

RANGE_TYPES = (
    "country",
    "route",
    "admin",
    "preset_area",
    "bbox",
    "circle",
    "polygon",
    "shape",
    "query",
    "facilities",
    "union",
)


class RangeError(ValueError):
    pass


def validate_polygon(g: dict[str, Any]) -> BaseGeometry:
    geom = shape(g)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise RangeError("GeoJSON は Polygon または MultiPolygon を指定してください")
    if not geom.is_valid:
        raise RangeError("ポリゴンが自己交差しています")
    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise RangeError("WGS84（経度・緯度）の座標を指定してください")
    return geom


def range_geometry(spec: dict[str, Any], admin_loader: Any = None) -> BaseGeometry | None:
    """幾何で表せる範囲を Polygon にする（路線・形状・クエリ等は None）。"""
    t = spec.get("type")
    g: BaseGeometry | None = None
    if t == "bbox":
        b = spec["bbox"]
        g = box(b[0], b[1], b[2], b[3])
    elif t == "circle":
        c = spec["circle"]
        g = Polygon(geodesic_buffer(c["lon"], c["lat"], float(c["radius_km"]) * 1000))
    elif t == "polygon":
        g = validate_polygon(spec["polygon"])
    elif t == "admin":
        if admin_loader is not None:
            g = admin_loader(spec.get("admin_codes", []))
        if g is None and spec.get("bbox"):
            b = spec["bbox"]
            g = box(b[0], b[1], b[2], b[3])
    elif t == "union":
        parts = [range_geometry(m, admin_loader) for m in spec.get("members", [])]
        parts2 = [p for p in parts if p is not None]
        g = unary_union(parts2) if parts2 else None
    buf = float(spec.get("buffer_km", 0) or 0)
    if g is not None and buf > 0:
        g = g.buffer(buf / 111.0)
    return g


def resolve_facilities(spec: dict[str, Any], fac: pd.DataFrame, query_runner: Any = None) -> set[str]:
    """範囲に含まれる施設 ID（代表点が範囲内。路線指定ならその路線に属する施設）。"""
    t = spec.get("type")
    if t == "route":
        refs = set(spec.get("route_refs", []))
        return {
            r.facility_id
            for r in fac.itertuples()
            if refs & set(list(r.route_refs) if r.route_refs is not None else [])
        }
    if t == "facilities":
        return set(spec.get("facility_ids", [])) & set(fac["facility_id"])
    if t == "shape":
        cls = set(spec.get("shape_classes", []))
        return set(fac.loc[fac["shape_class"].isin(cls), "facility_id"])
    if t == "country":
        cs = {str(c).upper() for c in spec.get("countries", [])}
        col = fac["country"] if "country" in fac.columns else fac["facility_id"].str[:2]
        return set(fac.loc[col.isin(cs), "facility_id"])
    if t == "admin" and spec.get("admin_codes"):
        codes = {str(c) for c in spec["admin_codes"]}
        hit = (
            set(fac.loc[fac["admin_code"].isin(codes), "facility_id"])
            if "admin_code" in fac.columns
            else set()
        )
        if hit or not spec.get("bbox"):
            return hit
    if t == "query":
        if query_runner is None:
            raise RangeError("検索クエリの範囲を解決できません")
        q = spec["query"]
        res = query_runner(q)
        if q.get("target") in ("ramp", "ramps"):
            return set(res["facility_id"])
        if q.get("target") in ("section", "sections"):
            return set(res["from_facility_id"]) | set(res["to_facility_id"])
        return set(res["facility_id"])
    if t == "union":
        out: set[str] = set()
        for m in spec.get("members", []):
            out |= resolve_facilities(m, fac, query_runner)
        return out
    g = range_geometry(spec)
    if g is None:
        raise RangeError(f"範囲の種類が不正です: {t}")
    return {r.facility_id for r in fac.itertuples() if g.contains(Point(r.lon, r.lat))}


def range_label(spec: dict[str, Any]) -> str:
    if spec.get("label"):
        return str(spec["label"])
    t = spec.get("type")
    if t == "route":
        return "路線 " + "・".join(spec.get("route_refs", []))
    if t == "facilities":
        return f"個別選択（{len(spec.get('facility_ids', []))} 施設）"
    if t == "shape":
        return "形状 " + "・".join(spec.get("shape_classes", []))
    if t == "query":
        return "検索結果"
    if t == "country":
        return "国 " + "・".join(spec.get("countries", []))
    return str(t)
