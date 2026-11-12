"""DEM アダプタ（§4.7.1, §4.7.2）。新しい DEM ソースは DemProvider のサブクラスを ADAPTERS に 1 つ登録し、
config/dem_sources.yaml に 1 エントリ追加するだけで使える。"""

from __future__ import annotations

import io
import math
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import httpx
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from roadmri.ingest.gsi_tiles import TILE_SIZE, decode_rgb, lonlat_to_global_pixel
from roadmri.ingest.http import HttpClient, NotFoundError, OfflineError
from roadmri.logging import get_logger, log_event

FloatArray = NDArray[np.float64]
Points = tuple[FloatArray, FloatArray]
Progress = Callable[[int, int, str], None]
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
        self.failed = 0

    @abstractmethod
    def sample(self, lon: FloatArray, lat: FloatArray) -> FloatArray:
        """双一次補間で標高を返す。欠損は NaN。"""

    def prefetch(
        self, points: Iterable[Points], workers: int, progress: Progress | None = None
    ) -> list[Points]:
        """points が使うデータを事前に（並列で）取得する。この DEM で得られない点を返す（下位 DEM の事前取得用）。

        事前取得は最適化であり、ここで取りこぼした部分も sample() 時に従来どおり取得・補完される。
        """
        return []

    def stats(self) -> dict[str, Any]:
        return {"requested": self.requested, "missing": self.missing, "failed": self.failed}


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
        self.cache_dirs: list[Path] = [
            Path(p) for p in ctx.get("tile_cache_dirs", ctx.get("gsi_cache_dirs", []))
        ]
        self.http: HttpClient | None = ctx.get("http")
        self.offline = bool(ctx.get("offline", False))
        self._tiles: OrderedDict[tuple[int, int], FloatArray | None] = OrderedDict()
        self._lock = threading.Lock()
        self._failed: set[tuple[int, int]] = (
            set()
        )  # 一時的な失敗（5xx・タイムアウト）。キャッシュせず次回の build で再取得
        self.meta.url = f"{self.BASE}/{self.layer}/{{z}}/{{x}}/{{y}}.png"
        self.tiles_fetched = 0

    EXT = ".png"

    def _tile_path(self, base: Path, x: int, y: int) -> Path:
        return base / self.layer / str(self.zoom) / str(x) / f"{y}{self.EXT}"

    def _fetch(self, x: int, y: int) -> bytes:
        assert self.http is not None
        return self.http.get_bytes(f"{self.BASE}/{self.layer}/{self.zoom}/{x}/{y}.png")

    def _to_cache(self, data: bytes) -> bytes:
        return data

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

    def _cached(self, x: int, y: int) -> Path | bool | None:
        """キャッシュにあればそのパス、404 記録があれば None、なければ False。"""
        for base in self.cache_dirs:
            p = self._tile_path(base, x, y)
            if p.exists():
                return p
            if p.with_suffix(".missing").exists():
                return None
        return False

    def _can_fetch(self) -> bool:
        return not self.offline and self.http is not None and bool(self.cache_dirs)

    def _dest(self, x: int, y: int) -> Path:
        return self._tile_path(self.cache_dirs[-1], x, y)

    def _store(self, x: int, y: int, data: bytes) -> None:
        dest = self._dest(x, y)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    def _store_missing(self, x: int, y: int) -> None:
        dest = self._dest(x, y)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.with_suffix(".missing").write_text("404")

    def _mark_failed(self, keys: Iterable[tuple[int, int]], err: Exception) -> None:
        ks = list(keys)
        with self._lock:
            new = [k for k in ks if k not in self._failed]
            self._failed.update(new)
            self.failed += len(new)
        log_event(log, "dem_tile_failed", dem=self.meta.dem_id, tiles=len(ks), error=str(err)[:200])

    def _fetch_store(self, x: int, y: int) -> bytes | None:
        """1 タイルを取得してキャッシュに保存する。404 は記録し、一時的な失敗は記録して None。"""
        try:
            data = self._to_cache(self._fetch(x, y))
        except NotFoundError:
            self._store_missing(x, y)
            return None
        except OfflineError:
            return None
        except httpx.HTTPError as e:
            self._mark_failed([(x, y)], e)
            return None
        self._store(x, y, data)
        with self._lock:
            self.tiles_fetched += 1
        return data

    def _read_tile(self, x: int, y: int) -> FloatArray | None:
        c = self._cached(x, y)
        if isinstance(c, Path):
            return self._decode(c.read_bytes())
        if c is None or not self._can_fetch() or (x, y) in self._failed:
            return None
        data = self._fetch_store(x, y)
        return None if data is None else self._decode(data)

    def _fetch_many(self, keys: list[tuple[int, int]], workers: int, tick: Callable[[int], None]) -> None:
        def one(k: tuple[int, int]) -> None:
            self._fetch_store(*k)
            tick(1)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(one, keys))

    def _point_tiles(self, lon: FloatArray, lat: FloatArray) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        px, py = lonlat_to_global_pixel(np.asarray(lon, float), np.asarray(lat, float), self.zoom)
        return np.floor(px / TILE_SIZE).astype(np.int64), np.floor(py / TILE_SIZE).astype(np.int64)

    def prefetch(
        self, points: Iterable[Points], workers: int, progress: Progress | None = None
    ) -> list[Points]:
        pts = [(np.asarray(a, float), np.asarray(b, float)) for a, b in points if len(a)]
        needed: set[tuple[int, int]] = set()
        for lon, lat in pts:
            needed |= self.tiles_for(lon, lat)
        todo = sorted(k for k in needed if self._cached(*k) is False and k not in self._failed)
        if todo and self._can_fetch():
            assert self.http is not None
            n_workers = min(max(1, workers), self.http.host_max_concurrency(self._probe_url()))
            done = [0]
            every = max(1, int(self.ctx.get("progress_every", 50)))
            lock = threading.Lock()

            def tick(n: int) -> None:
                with lock:
                    done[0] += n
                    d = done[0]
                if progress and (d % every < n or d >= len(todo)):
                    progress(
                        d,
                        len(todo),
                        f"{self.meta.dem_id} 標高タイル取得 {d}/{len(todo)}（失敗 {self.failed}）",
                    )

            self._fetch_many(todo, n_workers, tick)
        gone = {
            k for k in needed if self._cached(*k) is None or k in self._failed or self._cached(*k) is False
        }
        log_event(
            log,
            "dem_prefetch",
            dem=self.meta.dem_id,
            tiles=len(needed),
            fetched=len(todo),
            unavailable=len(gone),
            failed=self.failed,
        )
        if not gone:
            return []
        out: list[Points] = []
        for lon, lat in pts:
            tx, ty = self._point_tiles(lon, lat)
            m = np.fromiter(
                ((a, b) in gone for a, b in zip(tx.tolist(), ty.tolist(), strict=True)), bool, len(tx)
            )
            if m.any():
                out.append((lon[m], lat[m]))
        return out

    def _probe_url(self) -> str:
        return self.BASE

    def _decode(self, data: bytes) -> FloatArray:
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


class Usgs3depTileProvider(GsiTileProvider):
    """USGS 3DEP（The National Map の 3DEPElevation ImageServer、登録不要）。

    ImageServer の exportImage で、Web メルカトル XYZ タイル（256 px、EPSG:3857）と同じ格子の float32 GeoTIFF を
    取得してキャッシュする。サーバー側で利用可能な最良の 3DEP（1 m ライダー、1/3 秒角など）を動的モザイクする。
    標高基準は NAVD88。格子が地理院タイルと同じなので、双一次補間は共通の処理を使う。
    """

    adapter = "usgs_3dep_tile"
    EXT = ".tif"

    def __init__(self, dem_id: str, spec: dict[str, Any], ctx: dict[str, Any]):
        super().__init__(dem_id, {**spec, "layer": spec.get("layer", "usgs3dep")}, ctx)
        self.base_url = str(spec["base_url"]).rstrip("/")
        self.meta.url = f"{self.base_url}/exportImage"
        self.meta.crs = "EPSG:3857"
        self.block = max(1, int(spec.get("block_tiles", 1)))

    def _probe_url(self) -> str:
        return self.base_url

    def _tile_size_m(self) -> float:
        return float(2 * math.pi * 6378137.0 / 2**self.zoom)

    def _fetch(self, x: int, y: int) -> bytes:
        return self._fetch_rect(x, y, 1, 1)

    def _fetch_rect(self, x: int, y: int, w: int, h: int) -> bytes:
        """タイル (x, y) を左上とする w×h タイルの範囲を 1 要求で取得する（タイル格子に揃えた float32 GeoTIFF）。"""
        assert self.http is not None
        r = 6378137.0
        size = self._tile_size_m()
        x0, x1 = -math.pi * r + x * size, -math.pi * r + (x + w) * size
        y1, y0 = math.pi * r - y * size, math.pi * r - (y + h) * size
        params = {
            "bbox": f"{x0},{y0},{x1},{y1}",
            "bboxSR": "3857",
            "imageSR": "3857",
            "size": f"{TILE_SIZE * w},{TILE_SIZE * h}",
            "format": "tiff",
            "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "image",
        }
        data = self.http.get_bytes(f"{self.base_url}/exportImage", params=params)
        if not data.startswith((b"II*\x00", b"MM\x00*")):
            raise NotFoundError("3DEP の応答が GeoTIFF ではありません（範囲外の可能性）")
        return data

    def _to_cache(self, data: bytes) -> bytes:
        """キャッシュは圧縮（deflate、浮動小数点予測）した GeoTIFF で保存する。"""
        from rasterio.io import MemoryFile

        with MemoryFile(data) as src, src.open() as ds:
            arr = ds.read(1)
            prof = ds.profile
        prof.update(compress="deflate", predictor=3, tiled=False)
        with MemoryFile() as dst:
            with dst.open(**prof) as out:
                out.write(arr, 1)
            return bytes(dst.read())

    def _encode_tile(self, arr: NDArray[np.float32], x: int, y: int, nodata: float | None) -> bytes:
        from rasterio.io import MemoryFile
        from rasterio.transform import from_origin

        size = self._tile_size_m()
        r = 6378137.0
        prof: dict[str, Any] = {
            "driver": "GTiff",
            "width": TILE_SIZE,
            "height": TILE_SIZE,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:3857",
            "transform": from_origin(
                -math.pi * r + x * size, math.pi * r - y * size, size / TILE_SIZE, size / TILE_SIZE
            ),
            "nodata": nodata,
            "compress": "deflate",
            "predictor": 3,
            "tiled": False,
        }
        with MemoryFile() as dst:
            with dst.open(**prof) as out:
                out.write(arr.astype(np.float32), 1)
            return bytes(dst.read())

    def _fetch_block(self, x: int, y: int, w: int, h: int) -> None:
        """w×h タイルをまとめて取得し、タイルごとに分けてキャッシュする。"""
        from rasterio.io import MemoryFile

        keys = [(x + i, y + j) for j in range(h) for i in range(w)]
        try:
            data = self._fetch_rect(x, y, w, h)
        except NotFoundError:
            for k in keys:
                self._store_missing(*k)
            return
        except OfflineError:
            return
        except httpx.HTTPError as e:
            self._mark_failed(keys, e)
            return
        with MemoryFile(data) as mf, mf.open() as ds:
            arr = ds.read(1)
            nodata = ds.nodata
        if arr.shape != (TILE_SIZE * h, TILE_SIZE * w):
            self._mark_failed(keys, ValueError(f"3DEP の応答の大きさが不正です: {arr.shape}"))
            return
        for j in range(h):
            for i in range(w):
                tile = arr[j * TILE_SIZE : (j + 1) * TILE_SIZE, i * TILE_SIZE : (i + 1) * TILE_SIZE]
                self._store(x + i, y + j, self._encode_tile(tile, x + i, y + j, nodata))
        with self._lock:
            self.tiles_fetched += len(keys)

    def _fetch_many(self, keys: list[tuple[int, int]], workers: int, tick: Callable[[int], None]) -> None:
        """block_tiles×block_tiles の区画ごとに、必要なタイルを囲む最小の長方形を 1 要求で取得する。"""
        if self.block <= 1:
            super()._fetch_many(keys, workers, tick)
            return
        groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for k in keys:
            groups.setdefault((k[0] // self.block, k[1] // self.block), []).append(k)

        def one(ks: list[tuple[int, int]]) -> None:
            xs = [k[0] for k in ks]
            ys = [k[1] for k in ks]
            self._fetch_block(min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)
            tick(len(ks))

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(one, [groups[g] for g in sorted(groups)]))

    def _decode(self, data: bytes) -> FloatArray:
        from rasterio.io import MemoryFile

        with MemoryFile(data) as mf, mf.open() as ds:
            arr = ds.read(1).astype(float)
            nodata = ds.nodata
        if nodata is not None:
            arr[arr == nodata] = np.nan
        arr[(arr < -1000) | (arr > 10000)] = np.nan  # 無効値（-3.4e38 等）
        if arr.shape != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"3DEP タイルの大きさが不正です: {arr.shape}")
        out: FloatArray = arr
        return out


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
        self._failed: set[str] = set()

    def _download(self, name: str) -> None:
        assert self.http is not None
        dest = self.cache / f"{name}.tif"
        try:
            self.http.download(f"{self.base_url}/{name}/{name}.tif", dest)
        except (NotFoundError, OfflineError) as e:
            log_event(log, "copernicus_missing", tile=name, error=str(e))
        except httpx.HTTPError as e:
            self._failed.add(name)
            self.failed += 1
            log_event(log, "dem_tile_failed", dem=self.meta.dem_id, tiles=1, error=str(e)[:200])

    def prefetch(
        self, points: Iterable[Points], workers: int, progress: Progress | None = None
    ) -> list[Points]:
        names: set[str] = set()
        for lon, lat in points:
            if len(lon):
                la = np.floor(np.asarray(lat, float)).astype(int)
                lo = np.floor(np.asarray(lon, float)).astype(int)
                for a, b in set(zip(lo.tolist(), la.tolist(), strict=True)):
                    names.add(self.tile_name(float(a), float(b)))
        todo = sorted(n for n in names if not (self.cache / f"{n}.tif").exists() and n not in self._failed)
        if todo and not self.offline and self.http is not None:
            n_workers = min(max(1, workers), self.http.host_max_concurrency(self.base_url))
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                for i, _ in enumerate(ex.map(self._download, todo), 1):
                    if progress:
                        progress(i, len(todo), f"{self.meta.dem_id} 標高タイル取得 {i}/{len(todo)}")
        log_event(
            log, "dem_prefetch", dem=self.meta.dem_id, tiles=len(names), fetched=len(todo), failed=self.failed
        )
        return []

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
                if name in self._failed:
                    self._loaded_tiles.add(name)
                    continue
                self._download(name)
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
    for cls in (
        GsiTileProvider,
        Usgs3depTileProvider,
        GeoTiffProvider,
        CopernicusProvider,
        UsgsProvider,
        SyntheticProvider,
    )
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

    def prefetch(self, points: Iterable[Points], workers: int, progress: Progress | None = None) -> None:
        """優先順に、各 DEM で必要なデータを事前取得する。上位 DEM で得られない点だけを下位 DEM に回す。"""
        todo: Iterable[Points] = points
        for p in self.providers:
            todo = p.prefetch(todo, workers, progress)
            if not todo:
                break

    def used_datasets(self, src: NDArray[np.object_]) -> list[DemMeta]:
        used = {s for s in src.tolist() if s}
        return [p.meta for p in self.providers if p.meta.dem_id in used]
