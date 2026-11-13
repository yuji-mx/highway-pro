"""ベクタタイルの符号化・低ズーム用のまとめ版（別実装の mapbox-vector-tile で復号して確認）。"""

from __future__ import annotations

import mapbox_vector_tile
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString, Point

from roadmri.export.vector_tiles import (
    add_lods,
    encode_layer,
    lod_for_zoom,
    make_tile,
    merge_props,
    spatial_sort,
    tile_bounds,
    tile_props,
)

Z, X, Y = 12, 3637, 1612


def test_make_tile_round_trip_with_independent_decoder() -> None:
    w, s, e, n = tile_bounds(Z, X, Y)
    mid = (s + n) / 2
    geoms = [
        LineString([(w - 0.01, mid), (e + 0.01, mid)]),  # タイルをはみ出す線 → buffer で切り抜き
        LineString([((w + e) / 2, s + 0.001), ((w + e) / 2, n - 0.001)]),
        Point((w + e) / 2, mid),
        LineString([(w - 1, s - 1), (w - 0.9, s - 0.9)]),  # 範囲外 → 含めない
    ]
    props = [
        {"grade": -3.5, "route_ref": "E4", "low_conf": True, "lanes": 2},
        {"name": "テスト", "z": 120.25, "none": None, "lst": [1]},
        {"facility_id": "X"},
        {"name": "outside"},
    ]
    b = make_tile("mainline", props, [shapely.to_wkb(g) for g in geoms], Z, X, Y, 4096, 64)
    lay = mapbox_vector_tile.decode(b, default_options={"y_coord_down": True})["mainline"]
    assert lay["extent"] == 4096 and len(lay["features"]) == 3
    f0, f1, f2 = lay["features"]
    assert f0["geometry"]["coordinates"] == [[-64, 2048], [4160, 2048]]
    assert f0["properties"] == {"grade": -3.5, "route_ref": "E4", "low_conf": True, "lanes": 2}
    assert f1["properties"] == {"name": "テスト", "z": 120.25}  # None・配列は省く
    assert f2["geometry"] == {"type": "Point", "coordinates": [2048, 2048]}


def test_simplify_reduces_vertices_within_tolerance() -> None:
    w, s, e, n = tile_bounds(Z, X, Y)
    xs = np.linspace(w, e, 400)
    wiggle = LineString(
        np.column_stack([xs, (s + n) / 2 + 1e-7 * np.sin(np.arange(400))])
    )  # 1 単位未満の揺れ
    raw = mapbox_vector_tile.decode(make_tile("m", [{}], [shapely.to_wkb(wiggle)], Z, X, Y, 4096, 64))
    simp = mapbox_vector_tile.decode(make_tile("m", [{}], [shapely.to_wkb(wiggle)], Z, X, Y, 4096, 64, 8.0))
    n_raw = len(raw["m"]["features"][0]["geometry"]["coordinates"])
    n_simp = len(simp["m"]["features"][0]["geometry"]["coordinates"])
    assert n_simp == 2 < n_raw


def test_empty_and_degenerate() -> None:
    assert make_tile("x", [], [], Z, X, Y, 4096, 64) == b""
    w, s, _, _ = tile_bounds(Z, X, Y)
    tiny = LineString([(w + 1e-9, s + 1e-9), (w + 2e-9, s + 2e-9)])  # 整数化すると 1 点になる線は含めない
    assert make_tile("x", [{}], [shapely.to_wkb(tiny)], Z, X, Y, 4096, 64) == b""
    lay = mapbox_vector_tile.decode(encode_layer("e", [], 512))["e"]
    assert lay["features"] == [] and lay["extent"] == 512


def test_tile_props_keeps_listed_and_rounds() -> None:
    p = {
        "grade": 3.14159,
        "z": 120.6,
        "curvature": 0.00123456789,
        "name": "x",
        "flow": "entry",
        "nan": float("nan"),
    }
    out = tile_props(
        p, ["grade", "z", "curvature", "flow", "nan"], {"grade": 1, "z": 0, "curvature": 6, "nan": 1}
    )
    assert out == {"grade": 3.1, "z": 121, "curvature": 0.001235, "flow": "entry", "nan": out["nan"]}
    assert isinstance(out["z"], int) and out["nan"] != out["nan"]
    assert tile_props({"a": 1}, None, {}) == {"a": 1} and tile_props({"a": 1}, [], {}) == {}


def test_lod_for_zoom() -> None:
    levels = [[10, 4], [6, 64], [8, 16]]
    assert [lod_for_zoom(levels, z) for z in (3, 6, 7, 8, 10, 11)] == [64, 64, 16, 16, 4, 1]
    assert lod_for_zoom([], 5) == 1


def test_merge_props() -> None:
    m = merge_props(
        [
            {"grade": 2.0, "z": 10.0, "curvature": -0.01, "low_conf": False, "lanes": 2, "route_ref": "E4"},
            {"grade": -5.0, "z": 20.0, "curvature": 0.002, "low_conf": True, "lanes": 3, "route_ref": "E4"},
            {"grade": None, "z": float("nan"), "lanes": 2, "route_ref": "E4"},
        ]
    )
    assert m == {
        "grade": -5.0,
        "z": 15.0,
        "curvature": -0.01,
        "low_conf": True,
        "lanes": 2,
        "route_ref": "E4",
    }


def _disp() -> pd.DataFrame:
    rows = []
    for c, y0 in (("C1", 0.0), ("C2", 1.0)):
        for i in range(10):
            g = LineString([(i * 0.001, y0), ((i + 1) * 0.001, y0)])
            b = g.bounds
            rows.append(
                {
                    "layer": "mainline",
                    "feature_id": f"{c}:{i}",
                    "props": {"grade": float(i), "route_ref": c},
                    "geom": shapely.to_wkb(g),
                    "minx": b[0],
                    "miny": b[1],
                    "maxx": b[2],
                    "maxy": b[3],
                }
            )
    rows.append(
        {"layer": "facilities", "feature_id": "F", "props": {}, "geom": shapely.to_wkb(Point(0, 0)),
         "minx": 0.0, "miny": 0.0, "maxx": 0.0, "maxy": 0.0}
    )  # fmt: skip
    return pd.DataFrame(rows)


def test_add_lods_merges_consecutive_pieces_per_parent() -> None:
    out = add_lods(_disp(), {"mainline": [4, 1], "facilities": [4]})
    base = out[out["lod"] == 1]
    lod4 = out[out["lod"] == 4].sort_values("feature_id")
    assert len(base) == 21 and len(lod4) == 6  # 2 チェーン × ceil(10 / 4)
    first = lod4.iloc[0]
    assert first["feature_id"] == "C1@4:0" and first["props"]["grade"] == 3.0
    g = shapely.from_wkb(first["geom"])
    assert g.length == pytest_approx(0.004) and len(g.coords) == 5  # 接続点は重複させない
    assert not set(lod4["props"].map(lambda p: p["route_ref"])) - {"C1", "C2"}


def pytest_approx(v: float) -> object:
    import pytest

    return pytest.approx(v)


def test_spatial_sort_groups_layer_and_lod_and_is_deterministic() -> None:
    out = add_lods(_disp(), {"mainline": [4]})
    a = spatial_sort(out.sample(frac=1.0, random_state=1))
    b = spatial_sort(out.sample(frac=1.0, random_state=2))
    assert list(a["feature_id"]) == list(b["feature_id"])
    keys = list(zip(a["layer"], a["lod"], strict=True))
    assert keys == sorted(keys)
    # 同じ層・同じまとめ数の中では近い要素が隣り合う（2 本のチェーンが交互にならない）
    m = a[(a["layer"] == "mainline") & (a["lod"] == 1)]
    switches = np.sum(np.diff([0 if r.startswith("C1") else 1 for r in m["feature_id"]]) != 0)
    assert switches == 1
