"""地理院 PNG 標高タイルの復元（§4.7.1）。

x = 2^16·R + 2^8·G + B
  x < 2^23  → h = x·u
  x = 2^23  → 無効値（NaN）
  x > 2^23  → h = (x − 2^24)·u     （u = 0.01 m）
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

TILE_SIZE = 256
UNIT_M = 0.01


def decode_rgb(rgb: NDArray[np.uint8]) -> NDArray[np.float64]:
    a = np.asarray(rgb, dtype=np.int64)
    x = (a[..., 0] << 16) + (a[..., 1] << 8) + a[..., 2]
    h = np.where(x < 2**23, x * UNIT_M, (x - 2**24) * UNIT_M).astype(float)
    h[x == 2**23] = np.nan
    return h


def encode_elevation(h: NDArray[np.float64]) -> NDArray[np.uint8]:
    """テスト fixture 作成用の逆変換。NaN は (128,0,0)。"""
    h = np.asarray(h, dtype=float)
    x = np.round(np.nan_to_num(h) / UNIT_M).astype(np.int64)
    x = np.where(x < 0, x + 2**24, x)
    x = np.where(np.isnan(h), 2**23, x)
    out = np.stack([(x >> 16) & 255, (x >> 8) & 255, x & 255], axis=-1)
    return out.astype(np.uint8)


def lonlat_to_global_pixel(
    lon: NDArray[np.float64], lat: NDArray[np.float64], z: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    n = 2**z * TILE_SIZE
    px = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(np.asarray(lat))
    py = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return px, py


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    px, py = lonlat_to_global_pixel(np.array([lon]), np.array([lat]), z)
    return int(px[0] // TILE_SIZE), int(py[0] // TILE_SIZE)


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    n = 2**z

    def lat(yy: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)
