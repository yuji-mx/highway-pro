"""合成線形（直線・クロソイド・円弧）の生成と OSM 風の点列化。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

Seg = tuple[float, Callable[[float], float]]


def integrate(
    segments: Sequence[Seg], ds: float = 0.05, start=(0.0, 0.0), heading: float = 0.0
) -> np.ndarray:
    x, y = start
    th = heading
    pts = [(x, y)]
    for length, k in segments:
        n = max(1, round(length / ds))
        h = length / n
        for i in range(n):
            u = (i + 0.5) * h
            th += k(u) * h
            x += np.cos(th) * h
            y += np.sin(th) * h
            pts.append((x, y))
    return np.array(pts)


def arc_road(
    radius: float,
    angle: float = np.pi / 2,
    clothoid_ratio: float = 0.0,
    straight: float = 200.0,
    sign: float = 1.0,
) -> np.ndarray:
    ls = clothoid_ratio * radius
    arc = max(radius * abs(angle) - ls, 0.0)
    k = sign / radius
    segs: list[Seg] = [(straight, lambda u: 0.0)]
    if ls > 0:
        segs.append((ls, lambda u: k * u / ls))
    segs.append((arc, lambda u: k))
    if ls > 0:
        segs.append((ls, lambda u: k * (1 - u / ls)))
    segs.append((straight, lambda u: 0.0))
    return integrate(segs)


def osm_like(dense: np.ndarray, base_spacing: float, noise: float, rng: np.random.Generator) -> np.ndarray:
    d = np.hypot(*np.diff(dense, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(d)])
    total = s[-1]
    gaps = rng.uniform(0.5 * base_spacing, 1.5 * base_spacing, int(total / base_spacing * 2) + 2)
    ss = np.concatenate([[0.0], np.cumsum(gaps)])
    ss = np.append(ss[ss < total], total)
    pts = np.column_stack([np.interp(ss, s, dense[:, 0]), np.interp(ss, s, dense[:, 1])])
    return pts + rng.uniform(-noise, noise, pts.shape)
