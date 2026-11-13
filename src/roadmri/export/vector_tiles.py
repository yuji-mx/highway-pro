"""地図表示用のベクタタイル（Mapbox Vector Tile 2.1）と、広域表示用の簡略化（ADR-0015）。

- 表示用データ（display_feature）から、タイル（z/x/y）の範囲だけを取り出して MVT に符号化する。
  地図ライブラリは表示範囲のタイルだけを要求・キャッシュするので、州全域のデータを一度に読み込まない。
- 本線・ランプは 100 m・約 20 m ごとの細かい要素なので、低ズーム用に連続する要素をまとめた「まとめ数 k」
  の版を公開時に作る（lod 列。1 が元の要素）。色分けの値は、勾配・曲率は絶対値の最大、標高は平均、
  品質の低信頼は「いずれか」、その他は最頻値でまとめる。
- MVT の符号化は仕様どおりの最小実装（protobuf を手で書く）。外部の依存を増やさない。
"""

from __future__ import annotations

import json
import math
import struct
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd
import shapely
from numpy.typing import NDArray
from shapely.geometry import LineString

FloatArray = NDArray[np.float64]

# ---- 低ズーム用の簡略化 ---------------------------------------------------------------

_MAXABS = {"grade", "curvature"}
_MEAN = {"z"}


def _agg(values: list[Any], key: str) -> Any:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None
    if isinstance(vals[0], bool):
        return any(vals)
    if key in _MAXABS and all(isinstance(v, (int, float)) for v in vals):
        return max(vals, key=lambda v: abs(v))
    if key in _MEAN and all(isinstance(v, (int, float)) for v in vals):
        return float(sum(vals) / len(vals))
    return _mode(vals)


def _mode(vals: list[Any]) -> Any:
    c = Counter(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in vals)
    top = c.most_common(1)[0][0]
    return next(v for v in vals if json.dumps(v, sort_keys=True, ensure_ascii=False) == top)


def merge_props(props: Sequence[dict[str, Any]]) -> dict[str, Any]:
    keys: list[str] = []
    for p in props:
        keys += [k for k in p if k not in keys]
    return {k: _agg([p.get(k) for p in props], k) for k in keys}


def _parent_index(fid: str) -> tuple[str, int]:
    parent, _, idx = fid.rpartition(":")
    try:
        return parent, int(idx)
    except ValueError:
        return fid, 0


def lod_rows(rows: Iterable[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """同じ親（本線チェーン・ランプ）の連続する要素を k 個ずつまとめた行（lod=k）。"""
    by_parent: dict[str, list[tuple[int, dict[str, Any], Any]]] = {}
    for r in rows:
        parent, idx = _parent_index(str(r["feature_id"]))
        by_parent.setdefault(parent, []).append((idx, r["props"], r["_geom"]))
    out: list[dict[str, Any]] = []
    for parent, items in by_parent.items():
        items.sort(key=lambda t: t[0])
        for g in range(0, len(items), k):
            grp = items[g : g + k]
            coords: list[tuple[float, float]] = []
            for _, _, geom in grp:
                cs = list(geom.coords)
                if coords and cs and coords[-1] == cs[0]:
                    cs = cs[1:]
                coords += cs
            if len(coords) < 2:
                continue
            line = LineString(coords)
            b = line.bounds
            out.append(
                {
                    "feature_id": f"{parent}@{k}:{g // k}",
                    "props": merge_props([p for _, p, _ in grp]),
                    "geom": shapely.to_wkb(line),
                    "minx": b[0],
                    "miny": b[1],
                    "maxx": b[2],
                    "maxy": b[3],
                    "lod": k,
                }
            )
    return out


def add_lods(disp: pd.DataFrame, lods: dict[str, list[int]]) -> pd.DataFrame:
    """display_feature に低ズーム用の行を追加する（lods: レイヤー → まとめ数の一覧）。"""
    disp = disp.copy()
    disp["lod"] = 1
    parts = [disp]
    for layer, ks in lods.items():
        sub = disp[disp["layer"] == layer]
        if sub.empty:
            continue
        geoms = shapely.from_wkb([bytes(g) for g in sub["geom"]])
        rows = [
            {"feature_id": fid, "props": p, "_geom": g}
            for fid, p, g in zip(sub["feature_id"], sub["props"], geoms, strict=True)
        ]
        for k in sorted({int(k) for k in ks if int(k) > 1}):
            extra = pd.DataFrame(lod_rows(rows, k))
            if len(extra):
                extra.insert(0, "layer", layer)
                parts.append(extra)
    return pd.concat(parts, ignore_index=True)


def spatial_sort(disp: pd.DataFrame) -> pd.DataFrame:
    """レイヤー・まとめ数・位置（Z 順序曲線）の順に並べる。DuckDB の行グループの最小・最大値による
    絞り込みが効き、タイルの範囲検索で読む行が減る。"""
    if disp.empty:
        return disp
    cx = ((disp["minx"].to_numpy(float) + disp["maxx"].to_numpy(float)) / 2 + 180.0) / 360.0
    cy = ((disp["miny"].to_numpy(float) + disp["maxy"].to_numpy(float)) / 2 + 90.0) / 180.0
    ix = np.clip((cx * 65535).astype(np.uint64), 0, 65535)
    iy = np.clip((cy * 65535).astype(np.uint64), 0, 65535)
    key = _interleave(ix) | (_interleave(iy) << np.uint64(1))
    order = np.lexsort((disp["feature_id"].to_numpy(), key, disp["lod"].to_numpy(), disp["layer"].to_numpy()))
    return disp.iloc[order].reset_index(drop=True)


def _interleave(v: NDArray[np.uint64]) -> NDArray[np.uint64]:
    v = v & np.uint64(0xFFFF)
    v = (v | (v << np.uint64(8))) & np.uint64(0x00FF00FF)
    v = (v | (v << np.uint64(4))) & np.uint64(0x0F0F0F0F)
    v = (v | (v << np.uint64(2))) & np.uint64(0x33333333)
    v = (v | (v << np.uint64(1))) & np.uint64(0x55555555)
    return v


def lod_for_zoom(levels: Sequence[Sequence[int]], z: int) -> int:
    """[[最大ズーム, まとめ数], ...] から、ズーム z で使うまとめ数（該当なしは 1）。"""
    for max_zoom, k in sorted(levels, key=lambda t: t[0]):
        if z <= max_zoom:
            return int(k)
    return 1


def tile_props(props: dict[str, Any], keep: Sequence[str] | None, digits: dict[str, int]) -> dict[str, Any]:
    """タイルに入れる属性だけを残し、数値を丸める（keep が None なら全属性。小数桁 0 は整数にする）。"""
    out = {k: v for k, v in props.items() if keep is None or k in keep}
    for k, d in digits.items():
        v = out.get(k)
        if isinstance(v, float) and not math.isnan(v):
            out[k] = round(v) if int(d) == 0 else round(v, int(d))
    return out


# ---- タイルの範囲 --------------------------------------------------------------------


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """タイルの経緯度範囲（西, 南, 東, 北）。"""
    n = 2.0**z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y)


def _to_tile(coords: FloatArray, z: int, x: int, y: int, extent: int) -> FloatArray:
    n = 2.0**z
    lon, lat = coords[:, 0], np.clip(coords[:, 1], -85.05112878, 85.05112878)
    px = (lon + 180.0) / 360.0 * n
    lr = np.radians(lat)
    py = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n
    return np.column_stack([(px - x) * extent, (py - y) * extent])


# ---- MVT の符号化 --------------------------------------------------------------------


def _varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _ld(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _zz(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def _value(v: Any) -> bytes:
    if isinstance(v, bool):
        return _key(7, 0) + _varint(int(v))
    if isinstance(v, int):
        return _key(6, 0) + _varint(_zz(v))
    if isinstance(v, float):
        return _key(3, 1) + struct.pack("<d", v)
    return _ld(1, str(v).encode("utf-8"))


def _line_geometry(parts: list[NDArray[np.int64]]) -> list[int]:
    cmds: list[int] = []
    cx = cy = 0
    for pts in parts:
        cmds.append((1 & 0x7) | (1 << 3))
        x0, y0 = int(pts[0, 0]), int(pts[0, 1])
        cmds += [_zz(x0 - cx), _zz(y0 - cy)]
        cx, cy = x0, y0
        cmds.append((2 & 0x7) | ((len(pts) - 1) << 3))
        for px, py in pts[1:].tolist():
            cmds += [_zz(px - cx), _zz(py - cy)]
            cx, cy = px, py
    return cmds


def _point_geometry(pts: NDArray[np.int64]) -> list[int]:
    cmds = [(1 & 0x7) | (len(pts) << 3)]
    cx = cy = 0
    for px, py in pts.tolist():
        cmds += [_zz(px - cx), _zz(py - cy)]
        cx, cy = px, py
    return cmds


def encode_layer(name: str, features: list[tuple[int, list[int], dict[str, Any]]], extent: int) -> bytes:
    """features: (種類 1=点・2=線, 形状コマンド, 属性)。"""
    keys: dict[str, int] = {}
    values: dict[tuple[str, Any], int] = {}
    feats = bytearray()
    for i, (gtype, geom, props) in enumerate(features):
        tags: list[int] = []
        for k, v in props.items():
            if v is None or isinstance(v, (list, dict)):
                continue
            ki = keys.setdefault(k, len(keys))
            vk = (type(v).__name__, v)
            vi = values.setdefault(vk, len(values))
            tags += [ki, vi]
        body = _key(1, 0) + _varint(i + 1)
        if tags:
            body += _ld(2, b"".join(_varint(t) for t in tags))
        body += _key(3, 0) + _varint(gtype)
        body += _ld(4, b"".join(_varint(c) for c in geom))
        feats += _ld(2, body)
    out = _key(15, 0) + _varint(2) + _ld(1, name.encode("utf-8")) + bytes(feats)
    out += b"".join(_ld(3, k.encode("utf-8")) for k in keys)
    out += b"".join(_ld(4, _value(v)) for (_, v) in values)
    out += _key(5, 0) + _varint(extent)
    return _ld(3, out)


def make_tile(
    name: str,
    props: Sequence[dict[str, Any]],
    wkbs: Sequence[bytes],
    z: int,
    x: int,
    y: int,
    extent: int,
    buffer: int,
    simplify: float = 0.0,
) -> bytes:
    """経緯度の形状（WKB）をタイル座標に変換・切り抜き・簡略化・整数化して 1 レイヤーの MVT にする。"""
    if not wkbs:
        return b""
    geoms = shapely.from_wkb(list(wkbs))
    geoms = shapely.transform(geoms, lambda c: _to_tile(c, z, x, y, extent))
    geoms = shapely.clip_by_rect(geoms, -buffer, -buffer, extent + buffer, extent + buffer)
    if simplify > 0:
        geoms = shapely.simplify(geoms, simplify, preserve_topology=False)
    feats: list[tuple[int, list[int], dict[str, Any]]] = []
    for g, p in zip(geoms, props, strict=True):
        if g is None or g.is_empty:
            continue
        gt = g.geom_type
        if gt in ("Point", "MultiPoint"):
            pts = np.rint(shapely.get_coordinates(g)).astype(np.int64)
            feats.append((1, _point_geometry(pts), p))
            continue
        lines = (
            [g] if gt == "LineString" else [h for h in getattr(g, "geoms", []) if h.geom_type == "LineString"]
        )
        parts = []
        for ln in lines:
            pts = np.rint(np.asarray(ln.coords)[:, :2]).astype(np.int64)
            if len(pts) > 1:
                keep = np.ones(len(pts), dtype=bool)
                keep[1:] = np.any(pts[1:] != pts[:-1], axis=1)
                pts = pts[keep]
            if len(pts) >= 2:
                parts.append(pts)
        if parts:
            feats.append((2, _line_geometry(parts), p))
    return encode_layer(name, feats, extent) if feats else b""
