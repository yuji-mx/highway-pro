"""地図の道路をベクタタイルで配信する（ADR-0015）。表示範囲のタイルだけを返すので、州全域のデータを一度に送らない。"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from roadmri.api.deps import AppState, state
from roadmri.export.vector_tiles import lod_for_zoom, make_tile, tile_bounds, tile_props

router = APIRouter(tags=["layers"])
MVT = "application/vnd.mapbox-vector-tile"


class _Lru:
    def __init__(self) -> None:
        self.data: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> Any:
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key: tuple[Any, ...], value: Any, size: int) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > max(1, size):
                self.data.popitem(last=False)


_tiles = _Lru()
_meta = _Lru()


def _tiles_cfg(s: AppState) -> dict[str, Any]:
    cfg: dict[str, Any] = (s.cfg.settings.get("map", {}) or {}).get("tiles", {}) or {}
    return cfg


def _run_id(s: AppState) -> str:
    return str(s.run_info().get("run_id") or "")


def _has_lod(s: AppState, run_id: str, layer: str, lod: int) -> bool:
    """公開時に低ズーム用のまとめ版を作っていない DB（このバージョンより前の build）では元の要素を使う。"""
    key = (run_id, layer, lod)
    hit = _meta.get(key)
    if hit is None:
        hit = bool(
            s.store.scalar(
                "SELECT count(*) > 0 FROM (SELECT 1 FROM display_feature WHERE layer=? AND coalesce(lod, 1)=? LIMIT 1)",
                [layer, lod],
            )
        )
        _meta.put(key, hit, 1000)
    return bool(hit)


@router.get("/tiles/{layer}/{z}/{x}/{y}.pbf", response_class=Response)
def tile(layer: str, z: int, x: int, y: int, v: str | None = None, s: AppState = Depends(state)) -> Response:
    """道路のベクタタイル（Mapbox Vector Tile）。v に公開 ID を付けた URL はブラウザに長期キャッシュさせる。"""
    tc = _tiles_cfg(s)
    spec = (tc.get("layers") or {}).get(layer)
    if spec is None:
        raise HTTPException(404, f"タイルのレイヤーが見つかりません: {layer}")
    if not 0 <= z <= int(tc["max_zoom"]) or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise HTTPException(404, f"タイルの範囲外です: {z}/{x}/{y}")
    run_id = _run_id(s)
    key = (run_id, layer, z, x, y)
    data = _tiles.get(key)
    if data is None:
        data = b""
        if z >= int(spec.get("min_zoom", 0)):
            lod = lod_for_zoom(spec.get("lod") or [], z)
            if lod > 1 and not _has_lod(s, run_id, layer, lod):
                lod = 1
            extent, buffer = int(tc["extent"]), int(tc["buffer"])
            w, so, e, n = tile_bounds(z, x, y)
            pad_x = (e - w) * buffer / extent
            pad_y = (n - so) * buffer / extent
            df = s.store.df(
                "SELECT props, geom FROM display_feature WHERE layer=? AND coalesce(lod, 1)=? "
                "AND maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ?",
                [layer, lod, w - pad_x, e + pad_x, so - pad_y, n + pad_y],
            )
            if len(df):
                keep = spec.get("props")
                props = [
                    tile_props(
                        json.loads(p) if isinstance(p, str) else (p or {}), keep, tc.get("round") or {}
                    )
                    for p in df["props"]
                ]
                data = make_tile(
                    layer,
                    props,
                    [bytes(g) for g in df["geom"]],
                    z,
                    x,
                    y,
                    extent,
                    buffer,
                    float(tc.get("simplify", 0)),
                )
        _tiles.put(key, data, int(tc["cache_tiles"]))
    cache = "public, max-age=31536000, immutable" if v and v == run_id else "no-cache"
    return Response(data, media_type=MVT, headers={"Cache-Control": cache})


@router.get("/layers/summary")
def layers_summary(s: AppState = Depends(state)) -> dict[str, Any]:
    """地図の初期表示と色分けに使う要約（全要素を送らずに済むように）。"""
    run_id = _run_id(s)
    hit = _meta.get(("summary", run_id))
    if hit is not None:
        return dict(hit)
    q = s.store.one(
        "SELECT quantile_cont(z, 0.02) AS lo, quantile_cont(z, 0.98) AS hi FROM ("
        "SELECT TRY_CAST(json_extract(props, '$.z') AS DOUBLE) AS z FROM display_feature "
        "WHERE layer IN ('mainline', 'ramps') AND coalesce(lod, 1) = 1) WHERE z IS NOT NULL"
    )
    shapes = s.store.df("SELECT DISTINCT shape_class FROM facility WHERE shape_class IS NOT NULL ORDER BY 1")
    b = s.store.one(
        "SELECT min(minx) AS w, min(miny) AS s, max(maxx) AS e, max(maxy) AS n FROM display_feature "
        "WHERE layer = 'mainline' AND coalesce(lod, 1) = 1"
    )
    cb = s.store.df(
        "SELECT country, min(lon) AS w, min(lat) AS s, max(lon) AS e, max(lat) AS n FROM facility GROUP BY 1 ORDER BY 1"
    )
    out = {
        "run_id": run_id,
        "z_range": [q["lo"], q["hi"]] if q and q["lo"] is not None else None,
        "shapes": [str(x) for x in shapes["shape_class"]],
        "bounds": [b["w"], b["s"], b["e"], b["n"]] if b and b["w"] is not None else None,
        "country_bounds": {r.country: [r.w, r.s, r.e, r.n] for r in cb.itertuples()},
    }
    _meta.put(("summary", run_id), out, 1000)
    return out
