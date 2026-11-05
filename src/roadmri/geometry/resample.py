"""ポリラインの等間隔再サンプリング。"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def arc_length(xy: FloatArray) -> FloatArray:
    if len(xy) == 0:
        return np.zeros(0)
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    return np.concatenate([[0.0], np.cumsum(d)])


def dedupe_points(xy: FloatArray, tol: float = 1e-6) -> FloatArray:
    if len(xy) < 2:
        return xy
    d = np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))
    keep = np.concatenate([[True], d > tol])
    out: FloatArray = xy[keep]
    return out


def interpolate_at(xy: FloatArray, s: FloatArray, s_new: FloatArray) -> FloatArray:
    x = np.interp(s_new, s, xy[:, 0])
    y = np.interp(s_new, s, xy[:, 1])
    return np.column_stack([x, y])


def resample_uniform(xy: FloatArray, step: float) -> tuple[FloatArray, FloatArray]:
    """内部計算用：全長を等分割（間隔は step に最も近い等間隔）。戻り値 (s, xy)。"""
    xy = dedupe_points(np.asarray(xy, dtype=float))
    s = arc_length(xy)
    total = float(s[-1]) if len(s) else 0.0
    if total <= 0:
        return np.zeros(1), xy[:1]
    n = max(1, round(total / step))
    s_new = np.linspace(0.0, total, n + 1)
    return s_new, interpolate_at(xy, s, s_new)


def resample_step(xy: FloatArray, step: float) -> tuple[FloatArray, FloatArray]:
    """出力用：始点から step 間隔、終点を必ず含む。戻り値 (s, xy)。"""
    xy = dedupe_points(np.asarray(xy, dtype=float))
    s = arc_length(xy)
    total = float(s[-1]) if len(s) else 0.0
    if total <= 0:
        return np.zeros(1), xy[:1]
    s_new = np.arange(0.0, total, step)
    if total - s_new[-1] > 1e-6:
        s_new = np.concatenate([s_new, [total]])
    return s_new, interpolate_at(xy, s, s_new)
