"""平面座標の平滑化（Savitzky–Golay）。"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.signal import savgol_filter

FloatArray = NDArray[np.float64]


def window_points(window_m: float, step: float, order: int, n: int) -> int:
    w = round(window_m / step)
    if w % 2 == 0:
        w += 1
    w = min(w, n if n % 2 == 1 else n - 1)
    return w if w > order else 0


def savgol_xy(xy: FloatArray, step: float, window_m: float, order: int) -> FloatArray:
    n = len(xy)
    w = window_points(window_m, step, order, n)
    if w == 0:
        return xy.copy()
    x = savgol_filter(xy[:, 0], w, order, mode="interp")
    y = savgol_filter(xy[:, 1], w, order, mode="interp")
    return np.column_stack([x, y])
