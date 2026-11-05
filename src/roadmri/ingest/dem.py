"""DEM アダプタ（§4.7.1, §4.7.2）。新しい DEM ソースは DemProvider のサブクラスを ADAPTERS に 1 つ登録し、
config/dem_sources.yaml に 1 エントリ追加するだけで使える。"""

from __future__ import annotations

import io
import math
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from roadmri.ingest.gsi_tiles import TILE_SIZE, decode_rgb, lonlat_to_global_pixel
from roadmri.ingest.http import HttpClient, NotFoundError, OfflineError
from roadmri.logging import get_logger, log_event

FloatArray = NDArray[np.float64]
log = get_logger("dem")


@dataclass
class DemMeta:
    dem_id: str
    name: str
    resolution_m: float
    crs: str = "EPSG:4326"
    vertical_datum: str = ""
    nominal_error_m: float | None = None
    attribution: str = ""
    license: str = ""
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DemProvider(ABC):
    adapter: ClassVar[str] = ""

    def __init__(self, dem_id: str, spec: dict[str, Any], ctx: dict[str, Any]):
        self.meta = DemMeta(
            dem_id=dem_id,
            name=str(spec.get("name", dem_id)),
            resolution_m=float(spec.get("resolution_m", 30)),
            vertical_datum=str(spec.get("vertical_datum", "")),
            nominal_error_m=spec.get("nominal_error_m"),
            attribution=str(spec.get("attribution", "")),
            license=str(spec.get("license", "")),
        )
        self.spec = spec
        self.ctx = ctx
        self.requested = 0
        self.missing = 0

    @abstractmethod
    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        """双一次補間で標高を返す。欠損は NaN。"""

    def stats(self) -> dict[str, Any]:
        return {"requested": self.requested, "missing": self.missing}


def _bilinear(
    grid_get: Callable[[NDArray[np.int64], NDArray[np.int64]], FloatArray], px: FloatArray, py: FloatArray
) -> FloatArray:
    """ピクセル中心基準の双一次補間。grid_get(ix, iy) はグローバルピクセル値（NaN 可）を返す。"""
    fx = px - 0.5
    fy = py - 0.5
    x0 = np.floor(fx).astype(np.int64)
    y0 = np.floor(fy).astype(np.int64)
    tx = fx - x0
    ty = fy - y0
    v00 = grid_get(x0, y0)
    v10 = grid_get(x0 + 1, y0)
    v01 = grid_get(x0, y0 + 1)
    v11 = grid_get(x0 + 1, y0 + 1)
    w = np.stack([(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty])
    v = np.stack([v00, v10, v01, v11])
    good = ~np.isnan(v)
    ws = np.where(good, w, 0.0).sum(axis=0)
    vs = np.where(good, v * w, 0.0).sum(axis=0)
    out = np.where(ws > 0.5, vs / np.where(ws > 0, ws, 1.0), np.nan)  # 半分以上の重みが有効な場合だけ採用
    return out


class GsiTileProvider(DemProvider):
    """地理院 PNG 標高タイル（dem5a_png / dem5b_png / dem_png 等）。"""

    adapter = "gsi_tile"
    BASE = "https://cyberjapandata.gsi.go.jp/xyz"

    def __init__(self, dem_id: str, spec: dict[str, Any], ctx: dict[str, Any]):
        super().__init__(dem_id, spec, ctx)
        self.layer = str(spec["layer"])
        self.zoom = int(spec["zoom"])
        self.cache_dirs: list[Path] = [Path(p) for p in ctx.get("gsi_cache_dirs", [])]
        self.http: HttpClient | None = ctx.get("http")
        self.offline = bool(ctx.get("offline", False))
        self._tiles: OrderedDict[tuple[int, int], FloatArray | None] = OrderedDict()
        self._lock = threading.Lock()
        self.meta.url = f"{self.BASE}/{self.layer}/{{z}}/{{x}}/{{y}}.png"
        self.tiles_fetched = 0

    def _tile_path(self, base: Path, x: int, y: int) -> Path:
        return base / self.layer / str(self.zoom) / str(x) / f"{y}.png"

    def load_tile(self, x: int, y: int) -> FloatArray | None:
        key = (x, y)
        with self._lock:
            if key in self._tiles:
                self._tiles.move_to_end(key)
                return self._tiles[key]
        arr = self._read_tile(x, y)
        with self._lock:
            self._tiles[key] = arr
            if len(self._tiles) > 512:
                self._tiles.popitem(last=False)
        return arr

    def _read_tile(self, x: int, y: int) -> FloatArray | None:
        for base in self.cache_dirs:
            p = self._tile_path(base, x, y)
            if p.exists():
                return self._decode(p.read_bytes())
            if p.with_suffix(".missing").exists():
                return None
        if self.offline or self.http is None or not self.cache_dirs:
            return None
        url = f"{self.BASE}/{self.layer}/{self.zoom}/{x}/{y}.png"
        dest = self._tile_path(self.cache_dirs[-1], x, y)
        try:
            data = self.http.get_bytes(url)
        except NotFoundError:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.with_suffix(".missing").write_text("404")
            return None
        except OfflineError:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.tiles_fetched += 1
        return self._decode(data)

    @staticmethod
    def _decode(data: bytes) -> FloatArray:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return decode_rgb(np.asarray(img))

    def tiles_for(self, lon: FloatArray, lat: FloatArray) -> set[tuple[int, int]]:
        px, py = lonlat_to_global_pixel(lon, lat, self.zoom)
        out: set[tuple[int, int]] = set()
        for dx in (-0.5, 0.5):
            for dy in (-0.5, 0.5):
                tx = np.floor((px + dx) / TILE_SIZE).astype(int)
                ty = np.floor((py + dy) / TILE_SIZE).astype(int)
                out.update(zip(tx.tolist(), ty.tolist(), strict=True))
        return out

    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        self.requested += len(lon)
        px, py = lonlat_to_global_pixel(lon, lat, self.zoom)
        tiles = {t: self.load_tile(*t) for t in sorted(self.tiles_for(lon, lat))}

        def get(ix: NDArray[np.int64], iy: NDArray[np.int64]) -> FloatArray:
            out = np.full(ix.shape, np.nan)
            tx = ix // TILE_SIZE
            ty = iy // TILE_SIZE
            for (a, b), arr in tiles.items():
                if arr is None:
                    continue
                m = (tx == a) & (ty == b)
                if m.any():
                    out[m] = arr[iy[m] - b * TILE_SIZE, ix[m] - a * TILE_SIZE]
            return out

        z = _bilinear(get, px, py)
        self.missing += int(np.isnan(z).sum())
        return z


class GeoTiffProvider(DemProvider):
    """ローカル GeoTIFF（利用者が用意した DEM、Copernicus のキャッシュ、テスト fixture）。"""

    adapter = "geotiff"

    def __init__(self, dem_id: str, spec: dict[str, Any], ctx: dict[str, Any]):
        super().__init__(dem_id, spec, ctx)
        self.paths: list[Path] = [
            Path(p) for p in ([spec["path"]] if "path" in spec else spec.get("paths", []))
        ]
        self._data: list[tuple[Any, FloatArray, Any]] = []

    def _load(self) -> None:
        if self._data:
            return
        import rasterio
        from pyproj import Transformer

        for p in self.paths:
            if not p.exists():
                continue
            with rasterio.open(p) as ds:
                arr = ds.read(1).astype(float)
                if ds.nodata is not None:
                    arr[arr == ds.nodata] = np.nan
                crs = ds.crs.to_string() if ds.crs else "EPSG:4326"
                tr = (
                    None
                    if crs in ("EPSG:4326", "OGC:CRS84")
                    else Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                )
                self._data.append((ds.transform, arr, tr))
                self.meta.crs = crs

    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        self._load()
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        self.requested += len(lon)
        out = np.full(len(lon), np.nan)
        for transform, arr, tr in self._data:
            x, y = tr.transform(lon, lat) if tr is not None else (lon, lat)
            inv = ~transform
            col, row = inv * (np.asarray(x), np.asarray(y))
            h, w = arr.shape

            def get(
                ix: NDArray[np.int64], iy: NDArray[np.int64], arr: FloatArray = arr, h: int = h, w: int = w
            ) -> FloatArray:
                ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
                res = np.full(ix.shape, np.nan)
                res[ok] = arr[iy[ok], ix[ok]]
                return res

            z = _bilinear(get, np.asarray(col, dtype=float), np.asarray(row, dtype=float))
            fill = np.isnan(out) & ~np.isnan(z)
            out[fill] = z[fill]
        self.missing += int(np.isnan(out).sum())
        return out


class CopernicusProvider(GeoTiffProvider):
    """Copernicus DEM GLO-30（AWS Open Data の COG。1°タイルをダウンロードしてキャッシュ）。"""

    adapter = "copernicus"

    def __init__(self, dem_id: str, spec: dict[str, Any], ctx: dict[str, Any]):
        super().__init__(dem_id, {**spec, "paths": []}, ctx)
        self.base_url = str(spec.get("base_url", "https://copernicus-dem-30m.s3.amazonaws.com"))
        self.cache = Path(ctx.get("cache_dir", "data/raw/dem")) / "copernicus"
        self.http: HttpClient | None = ctx.get("http")
        self.offline = bool(ctx.get("offline", False))
        self._loaded_tiles: set[str] = set()

    @staticmethod
    def tile_name(lon: float, lat: float) -> str:
        la, lo = math.floor(lat), math.floor(lon)
        ns = f"{'N' if la >= 0 else 'S'}{abs(la):02d}_00"
        ew = f"{'E' if lo >= 0 else 'W'}{abs(lo):03d}_00"
        return f"Copernicus_DSM_COG_10_{ns}_{ew}_DEM"

    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        names = {self.tile_name(float(a), float(b)) for a, b in zip(lon, lat, strict=True)}
        new = False
        for name in sorted(names - self._loaded_tiles):
            dest = self.cache / f"{name}.tif"
            if not dest.exists() and not self.offline and self.http is not None:
                try:
                    self.http.download(f"{self.base_url}/{name}/{name}.tif", dest)
                except (NotFoundError, OfflineError) as e:
                    log_event(log, "copernicus_missing", tile=name, error=str(e))
            if dest.exists():
                self.paths.append(dest)
                new = True
            self._loaded_tiles.add(name)
        if new:
            self._data = []
        return super().sample(lon, lat)


class UsgsProvider(GeoTiffProvider):
    """USGS 3DEP（P5）。MVP ではローカルに置いた GeoTIFF のみ読み込む。"""

    adapter = "usgs"


class SyntheticProvider(DemProvider):
    """合成 DEM（テスト・デモ用）。ctx['synthetic_dem'] に f(lon, lat) -> z を渡す。"""

    adapter = "synthetic"

    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        fn = self.ctx.get("synthetic_dem")
        self.requested += len(lon)
        if fn is None:
            from roadmri.ingest.synthetic import synthetic_dem

            fn = synthetic_dem
        return np.asarray(fn(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)), dtype=float)


ADAPTERS: dict[str, type[DemProvider]] = {
    cls.adapter: cls
    for cls in (GsiTileProvider, GeoTiffProvider, CopernicusProvider, UsgsProvider, SyntheticProvider)
}


def build_providers(dem_cfg: dict[str, Any], profile: str, ctx: dict[str, Any]) -> list[DemProvider]:
    out: list[DemProvider] = []
    for src in dem_cfg.get("local_sources", []) or []:
        out.append(GeoTiffProvider(str(src.get("name", src["path"])), src, ctx))
    for dem_id in dem_cfg["profiles"][profile]:
        spec = dem_cfg["sources"][dem_id]
        out.append(ADAPTERS[spec["adapter"]](dem_id, spec, ctx))
    return out


class DemSampler:
    """優先順に DEM を試し、欠損を下位 DEM で補完する（§4.7.2）。"""

    def __init__(self, providers: list[DemProvider]):
        self.providers = providers

    def sample(self, lon: FloatArray, lat: FloatArray) -> tuple[FloatArray, NDArray[np.object_], FloatArray]:
        n = len(lon)
        z = np.full(n, np.nan)
        src = np.full(n, None, dtype=object)
        res = np.full(n, np.nan)
        todo = np.ones(n, dtype=bool)
        for p in self.providers:
            if not todo.any():
                break
            idx = np.where(todo)[0]
            zz = p.sample(np.asarray(lon)[idx], np.asarray(lat)[idx])
            ok = ~np.isnan(zz)
            z[idx[ok]] = zz[ok]
            src[idx[ok]] = p.meta.dem_id
            res[idx[ok]] = p.meta.resolution_m
            todo[idx[ok]] = False
        return z, src, res

    def used_datasets(self, src: NDArray[np.object_]) -> list[DemMeta]:
        used = {s for s in src.tolist() if s}
        return [p.meta for p in self.providers if p.meta.dem_id in used]
