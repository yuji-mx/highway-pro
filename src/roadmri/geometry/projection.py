"""座標投影。保存は WGS84、計算は UTM（対象範囲の重心経度から決める）。投影はこのモジュールに集約する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray
from pyproj import Geod, Transformer

FloatArray = NDArray[np.float64]
WGS84 = "EPSG:4326"
_GEOD = Geod(ellps="WGS84")


def utm_zone(lon: float) -> int:
    return int(np.floor((lon + 180.0) / 6.0)) % 60 + 1


def utm_epsg(lon: float, lat: float) -> int:
    zone = utm_zone(lon)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=16)
def _transformers(crs: int | str) -> tuple[Transformer, Transformer]:
    target = f"EPSG:{crs}" if isinstance(crs, int) else crs
    fwd = Transformer.from_crs(WGS84, target, always_xy=True)
    inv = Transformer.from_crs(target, WGS84, always_xy=True)
    return fwd, inv


def transverse_mercator(lon0: float) -> str:
    """UTM と同じ縮尺係数・偽東距で、中央子午線を範囲の重心に置いた横メルカトル。"""
    return (
        f"+proj=tmerc +lat_0=0 +lon_0={lon0:.6f} +k=0.9996 +x_0=500000 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


@dataclass(frozen=True)
class Projector:
    epsg: int | str
    _fwd: Transformer = field(init=False, repr=False, compare=False)
    _inv: Transformer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        fwd, inv = _transformers(self.epsg)
        object.__setattr__(self, "_fwd", fwd)
        object.__setattr__(self, "_inv", inv)

    @classmethod
    def for_lonlat(cls, lon: float, lat: float) -> Projector:
        return cls(utm_epsg(lon, lat))

    @classmethod
    def for_bounds(cls, minlon: float, minlat: float, maxlon: float, maxlat: float) -> Projector:
        """範囲が 1 つの UTM ゾーンに収まればその UTM、2 ゾーン以上にまたがる場合（米国の州など）は
        重心経度を中央子午線とする横メルカトル（ADR-0013）。"""
        lon0, lat0 = (minlon + maxlon) / 2, (minlat + maxlat) / 2
        if utm_zone(minlon) == utm_zone(maxlon):
            return cls(utm_epsg(lon0, lat0))
        return cls(transverse_mercator(lon0))

    def to_xy(self, lon: FloatArray | float, lat: FloatArray | float) -> tuple[FloatArray, FloatArray]:
        x, y = self._fwd.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
        return np.asarray(x, dtype=float), np.asarray(y, dtype=float)

    def to_lonlat(self, x: FloatArray | float, y: FloatArray | float) -> tuple[FloatArray, FloatArray]:
        lon, lat = self._inv.transform(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
        return np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)


def geodesic_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    _, _, d = _GEOD.inv(lon1, lat1, lon2, lat2)
    return float(d)


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """真北 0°・時計回りの方位角（0〜360）。"""
    az, _, _ = _GEOD.inv(lon1, lat1, lon2, lat2)
    return float(az) % 360.0


def geodesic_buffer(lon: float, lat: float, radius_m: float, n: int = 72) -> list[tuple[float, float]]:
    az = np.linspace(0, 360, n, endpoint=False)
    lons, lats, _ = _GEOD.fwd(np.full(n, lon), np.full(n, lat), az, np.full(n, radius_m))
    pts = [(float(a), float(b)) for a, b in zip(lons, lats, strict=True)]
    pts.append(pts[0])
    return pts
