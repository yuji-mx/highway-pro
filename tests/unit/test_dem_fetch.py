"""標高タイルの事前取得・まとめ取得・一時的な失敗（504 等）への耐性（オフライン、偽の HTTP で検証）。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from roadmri.ingest.dem import DemSampler, SyntheticProvider, Usgs3depTileProvider
from roadmri.ingest.gsi_tiles import TILE_SIZE
from roadmri.ingest.http import HttpClient

R = 6378137.0
ZOOM = 15
SPEC = {"base_url": "https://dem.example/ImageServer", "zoom": ZOOM, "resolution_m": 3.5, "name": "3DEP"}


def _value(gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """全球ピクセル座標の関数としての標高（位置ごとに異なる値）。"""
    return (100.0 + 0.001 * (gx % 10000) + 0.002 * (gy % 10000)).astype(np.float32)


class FakeHttp:
    """exportImage の偽物。bbox と size から格子に揃った float32 GeoTIFF を返す。fail が真の間は 504。"""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[int, int]] = []

    def host_max_concurrency(self, url: str) -> int:
        return 2

    def get_bytes(self, url: str, params: dict[str, str] | None = None, **kw: Any) -> bytes:
        assert params is not None
        w, h = (int(v) for v in params["size"].split(","))
        self.calls.append((w // TILE_SIZE, h // TILE_SIZE))
        if self.fail:
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError("HTTP 504", request=req, response=httpx.Response(504, request=req))
        x0, _y0, _x1, y1 = (float(v) for v in params["bbox"].split(","))
        world = 2 * math.pi * R
        px0 = (x0 + math.pi * R) / world * TILE_SIZE * 2**ZOOM
        py0 = (math.pi * R - y1) / world * TILE_SIZE * 2**ZOOM
        gy, gx = np.mgrid[0:h, 0:w]
        arr = _value(np.round(px0) + gx, np.round(py0) + gy)
        res = world / 2**ZOOM / TILE_SIZE
        prof = {
            "driver": "GTiff",
            "width": w,
            "height": h,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:3857",
            "transform": from_origin(x0, y1, res, res),
            "nodata": -3.4e38,
        }
        with MemoryFile() as mf:
            with mf.open(**prof) as ds:
                ds.write(arr, 1)
            return bytes(mf.read())


def _provider(tmp_path: Path, http: Any, block: int = 4) -> Usgs3depTileProvider:
    ctx = {"tile_cache_dirs": [tmp_path / "tiles"], "http": http, "offline": False}
    return Usgs3depTileProvider("usgs_3dep_z15", {**SPEC, "block_tiles": block}, ctx)


def _line(n: int = 400) -> tuple[np.ndarray, np.ndarray]:
    """Ann Arbor 付近を斜めに約 6 km 横切る点列（タイル数個にまたがる）。"""
    return np.linspace(-83.76, -83.70, n), np.linspace(42.22, 42.24, n)


def test_block_fetch_matches_single_tiles(tmp_path: Path) -> None:
    lon, lat = _line()
    blk_http, one_http = FakeHttp(), FakeHttp()
    blk = _provider(tmp_path / "b", blk_http, block=4)
    one = _provider(tmp_path / "s", one_http, block=1)
    blk.prefetch([(lon, lat)], workers=4)
    one.prefetch([(lon, lat)], workers=4)
    # まとめ取得は要求数が少なく、複数タイルの長方形を要求している
    assert len(blk_http.calls) < len(one_http.calls)
    assert any(w * h > 1 for w, h in blk_http.calls)
    assert all(w <= 4 and h <= 4 for w, h in blk_http.calls)
    # タイルごとのキャッシュ内容と標本値が一致する
    for p in sorted((tmp_path / "s" / "tiles").rglob("*.tif")):
        q = tmp_path / "b" / "tiles" / p.relative_to(tmp_path / "s" / "tiles")
        assert np.array_equal(blk._decode(q.read_bytes()), one._decode(p.read_bytes()))
    assert np.array_equal(blk.sample(lon, lat), one.sample(lon, lat))
    # 事前取得後の標本化では追加の取得をしない
    n = len(blk_http.calls)
    blk.sample(lon, lat)
    assert len(blk_http.calls) == n


def test_prefetch_then_sample_equals_lazy(tmp_path: Path) -> None:
    lon, lat = _line()
    lazy = _provider(tmp_path / "lazy", FakeHttp(), block=1)
    pre = _provider(tmp_path / "pre", FakeHttp(), block=4)
    pre.prefetch([(lon, lat)], workers=2)
    z = pre.sample(lon, lat)
    assert not np.isnan(z).any()
    assert np.array_equal(z, lazy.sample(lon, lat))


def test_504_falls_back_without_aborting_and_is_retried_next_run(tmp_path: Path) -> None:
    lon, lat = _line()
    http = FakeHttp(fail=True)
    prim = _provider(tmp_path, http)
    fallback = SyntheticProvider(
        "synthetic", {"resolution_m": 30}, {"synthetic_dem": lambda a, b: a * 0 + 7.0}
    )
    sampler = DemSampler([prim, fallback])
    sampler.prefetch([(lon, lat)], workers=2)  # 例外にならない
    assert prim.failed > 0
    calls = len(http.calls)
    z, src, _ = sampler.sample(lon, lat)  # 失敗したタイルは同じ実行中に再要求しない
    assert len(http.calls) == calls
    assert np.all(z == 7.0) and set(src.tolist()) == {"synthetic"}
    # 失敗は 404 として記録しない（次回の build で取得し直す）
    assert not list((tmp_path / "tiles").rglob("*.missing"))
    again = _provider(tmp_path, FakeHttp())
    z2, src2, _ = DemSampler([again, fallback]).sample(lon, lat)
    assert set(src2.tolist()) == {"usgs_3dep_z15"} and not np.isnan(z2).any()


def test_prefetch_returns_only_points_in_unavailable_tiles(tmp_path: Path) -> None:
    lon, lat = _line()
    prov = _provider(tmp_path, FakeHttp(fail=True))
    gaps = prov.prefetch([(lon, lat), (lon[:0], lat[:0])], workers=1)
    assert sum(len(a) for a, _ in gaps) == len(lon)
    ok = _provider(tmp_path / "ok", FakeHttp())
    assert ok.prefetch([(lon, lat)], workers=1) == []


def test_offline_prefetch_does_nothing(tmp_path: Path) -> None:
    lon, lat = _line()
    http = FakeHttp()
    ctx = {"tile_cache_dirs": [tmp_path], "http": http, "offline": True}
    Usgs3depTileProvider("usgs_3dep_z15", {**SPEC, "block_tiles": 4}, ctx).prefetch([(lon, lat)], workers=4)
    assert http.calls == []


def _client(handler: Any, settings: dict[str, Any]) -> HttpClient:
    hc = HttpClient({"http": settings})
    hc._client = httpx.Client(transport=httpx.MockTransport(handler))
    return hc


def test_http_host_retries_timeout_and_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("roadmri.ingest.http.time.sleep", lambda s: sleeps.append(s))
    seen: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.extensions.get("timeout"))
        if len(seen) == 1:
            return httpx.Response(503, headers={"Retry-After": "30"})
        return httpx.Response(504)

    hc = _client(
        handler,
        {
            "timeout_s": 180,
            "max_retries": 4,
            "backoff_base_s": 1.0,
            "retry_after_cap_s": 20,
            "rate_limits": {"dem.example": {"timeout_s": 5, "max_retries": 2}},
        },
    )
    with pytest.raises(httpx.HTTPStatusError):
        hc.get_bytes("https://dem.example/x")
    assert len(seen) == 3  # ホスト別の max_retries=2（既定の 4 ではない）
    assert seen[0]["read"] == 5  # ホスト別の timeout_s
    assert sleeps == [20.0, 2.0]  # Retry-After（上限 20 秒）、その後は指数バックオフ
    assert hc.host_max_concurrency("https://other.example/") == 4
