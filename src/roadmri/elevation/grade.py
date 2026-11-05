"""勾配解析（§4.8）。符号は要素の進行方向基準、% 表示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def instantaneous_grade(s: FloatArray, z: FloatArray) -> FloatArray:
    """g_inst(s) = (z(s+Δ) − z(s−Δ)) / 2Δ × 100（端は片側差分）。表示用。"""
    if len(s) < 2:
        return np.zeros(len(s))
    return np.gradient(z, s) * 100.0


def window_grade(s: FloatArray, z: FloatArray, w: float) -> tuple[FloatArray, bool]:
    """窓平均勾配 g_w(s)。窓がはみ出る点は NaN。要素長 < w の場合は端点間平均 1 値（truncated=True）。"""
    n = len(s)
    out = np.full(n, np.nan)
    if n < 2:
        return out, True
    length = float(s[-1] - s[0])
    if length <= 0:
        return out, True
    if length < w:
        g = (z[-1] - z[0]) / length * 100.0
        out[:] = g
        return out, True
    half = w / 2.0
    ok = (s - half >= s[0] - 1e-9) & (s + half <= s[-1] + 1e-9)
    zp = np.interp(s[ok] + half, s, z)
    zm = np.interp(s[ok] - half, s, z)
    out[ok] = (zp - zm) / w * 100.0
    return out, False


def hysteresis_gain(z: FloatArray, threshold: float) -> tuple[float, float]:
    """累積上昇・下降量。基準値から threshold 以上動いたときだけ計上する（threshold 未満の振動を除外）。

    最後の計上方向と同じ向きの端数は終端で加える。
    """
    zz = z[~np.isnan(z)]
    if len(zz) < 2:
        return 0.0, 0.0
    gain = loss = 0.0
    ref = float(zz[0])
    last = 0
    for v in zz[1:]:
        d = float(v) - ref
        if d >= threshold or (last == 1 and d > 0):
            gain += d
            ref = float(v)
            last = 1
        elif -d >= threshold or (last == -1 and d < 0):
            loss += -d
            ref = float(v)
            last = -1
    return float(gain), float(loss)


def longest_run(s: FloatArray, mask: NDArray[np.bool_]) -> float:
    best = 0.0
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            best = max(best, float(s[j] - s[i]))
            i = j + 1
        else:
            i += 1
    return best


@dataclass
class GradeResult:
    g_inst: FloatArray
    g_windows: dict[int, FloatArray]
    truncated: dict[int, bool]
    metrics: dict[str, Any] = field(default_factory=dict)


def _nanmax(a: FloatArray) -> float | None:
    return float(np.nanmax(a)) if np.any(~np.isnan(a)) else None


def _nanmin(a: FloatArray) -> float | None:
    return float(np.nanmin(a)) if np.any(~np.isnan(a)) else None


def analyze_grade(s: FloatArray, z: FloatArray, cfg: dict[str, Any]) -> GradeResult:
    gc = cfg["grade"]
    windows = [int(w) for w in gc["windows_m"]]
    g_inst = instantaneous_grade(s, z)
    g_w: dict[int, FloatArray] = {}
    trunc: dict[int, bool] = {}
    m: dict[str, Any] = {}
    for w in windows:
        g, t = window_grade(s, z, float(w))
        g_w[w], trunc[w] = g, t
        mx = _nanmax(g)
        mn = _nanmin(g)
        m[f"grade_up_max_w{w}"] = max(mx, 0.0) if mx is not None else None
        m[f"grade_down_max_w{w}"] = max(-mn, 0.0) if mn is not None else None
    length = float(s[-1] - s[0]) if len(s) > 1 else 0.0
    valid = ~np.isnan(z)
    m["grade_inst_max"] = _nanmax(g_inst)
    m["grade_inst_min"] = _nanmin(g_inst)
    if valid.sum() >= 2 and length > 0:
        zv = z[valid]
        sv = s[valid]
        m["grade_mean"] = float((zv[-1] - zv[0]) / (sv[-1] - sv[0]) * 100.0) if sv[-1] > sv[0] else 0.0
        m["elev_diff_m"] = float(zv[-1] - zv[0])
        m["z_max"] = float(np.max(zv))
        m["z_min"] = float(np.min(zv))
        gain, loss = hysteresis_gain(zv, float(gc["gain_hysteresis_m"]))
        m["elev_gain_m"], m["elev_loss_m"] = gain, loss
    else:
        for k in ("grade_mean", "elev_diff_m", "z_max", "z_min", "elev_gain_m", "elev_loss_m"):
            m[k] = None
    dw = int(gc["default_window_m"])
    thr = float(gc["continuous_threshold_pct"])
    gd = g_w.get(dw, g_inst)
    m["cont_up_len_m"] = longest_run(s, np.nan_to_num(gd, nan=0.0) > thr)
    m["cont_down_len_m"] = longest_run(s, np.nan_to_num(gd, nan=0.0) < -thr)
    m["window_truncated"] = bool(trunc.get(dw, False))
    return GradeResult(g_inst=g_inst, g_windows=g_w, truncated=trunc, metrics=m)


def tail_mean_grade(s: FloatArray, z: FloatArray, tail_m: float) -> float | None:
    if len(s) < 2:
        return None
    s0 = max(s[0], s[-1] - tail_m)
    z0 = float(np.interp(s0, s, z))
    if s[-1] - s0 <= 0:
        return None
    return float((z[-1] - z0) / (s[-1] - s0) * 100.0)


def max_window_grade_in(s: FloatArray, g: FloatArray, s_from: float, s_to: float) -> float | None:
    m = (s >= s_from) & (s <= s_to) & ~np.isnan(g)
    return float(np.max(g[m])) if m.any() else None
