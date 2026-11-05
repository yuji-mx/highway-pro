"""曲率・線形解析（§4.6）。三点外接円法、左カーブを正とする。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import make_smoothing_spline
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter

from roadmri.geometry.resample import arc_length, dedupe_points, resample_uniform
from roadmri.geometry.smoothing import savgol_xy

FloatArray = NDArray[np.float64]
ElementKind = Literal["ramp", "mainline"]


@dataclass
class CurveSegment:
    start_m: float
    end_m: float
    side: str
    min_radius_m: float

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


@dataclass
class CurvatureResult:
    s: FloatArray
    xy: FloatArray
    kappa: FloatArray
    metrics: dict[str, Any]
    curves: list[CurveSegment] = field(default_factory=list)
    heading: FloatArray = field(default_factory=lambda: np.zeros(0))


def three_point_curvature(xy: FloatArray, k: int) -> FloatArray:
    """P(i-k), P(i), P(i+k) の外接円から符号付き曲率を求める。端の k 点は NaN。"""
    n = len(xy)
    out = np.full(n, np.nan)
    if k < 1 or n < 2 * k + 1:
        return out
    a = xy[: n - 2 * k]
    b = xy[k : n - k]
    c = xy[2 * k :]
    ab = b - a
    bc = c - b
    ca = a - c
    la = np.hypot(bc[:, 0], bc[:, 1])
    lb = np.hypot(ca[:, 0], ca[:, 1])
    lc = np.hypot(ab[:, 0], ab[:, 1])
    cross = ab[:, 0] * bc[:, 1] - ab[:, 1] * bc[:, 0]  # = 2S（符号付き）
    denom = la * lb * lc
    with np.errstate(divide="ignore", invalid="ignore"):
        kappa = np.where(denom > 0, 2.0 * cross / denom, 0.0)  # 1/R = 4S/(abc)
    out[k : n - k] = kappa
    return out


def _params(cfg: dict[str, Any], kind: ElementKind) -> dict[str, float]:
    rs = cfg["resample"]
    gs = cfg["geometry_smoothing"]
    cv = cfg["curvature"]
    return {
        "step": float(rs["internal_ramp_step_m"] if kind == "ramp" else rs["internal_mainline_step_m"]),
        "window": float(gs["ramp_window_m"] if kind == "ramp" else gs["mainline_window_m"]),
        "order": float(gs["order"]),
        "chord": float(cv["ramp_chord_m"] if kind == "ramp" else cv["mainline_chord_m"]),
        "min_arc": float(cv["min_arc_m"]),
        "curve_r": float(cv["curve_threshold_radius_m"]),
        "curve_min_len": float(cv.get("curve_min_length_m", 20)),
        "rate_window": float(cv["ramp_rate_window_m"] if kind == "ramp" else cv["mainline_rate_window_m"]),
    }


def heading_unwrapped(xy: FloatArray) -> FloatArray:
    if len(xy) < 2:
        return np.zeros(len(xy))
    d = np.diff(xy, axis=0)
    th = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
    return np.concatenate([th, th[-1:]])


def find_curves(s: FloatArray, kappa: FloatArray, curve_r: float, min_len: float) -> list[CurveSegment]:
    thr = 1.0 / curve_r
    mask = np.nan_to_num(np.abs(kappa)) > thr
    curves: list[CurveSegment] = []
    i = 0
    n = len(s)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1] and np.sign(kappa[j + 1]) == np.sign(kappa[i]):
            j += 1
        if s[j] - s[i] >= min_len:
            seg = kappa[i : j + 1]
            kmax = float(np.nanmax(np.abs(seg)))
            curves.append(
                CurveSegment(
                    start_m=float(s[i]),
                    end_m=float(s[j]),
                    side="left" if np.nanmean(seg) > 0 else "right",
                    min_radius_m=1.0 / kmax if kmax > 0 else float("inf"),
                )
            )
        i = j + 1
    return curves


def spline_resample(xy: FloatArray, bandwidth_m: float, step: float) -> FloatArray:
    """頂点列を罰則付き平滑化スプライン（lam = h^4 / 平均頂点間隔）で近似し、step 間隔で評価する。"""
    xy = dedupe_points(np.asarray(xy, dtype=float))
    t = arc_length(xy)
    total = float(t[-1]) if len(t) else 0.0
    if total <= 0 or len(xy) < 2:
        return xy
    if len(xy) < 5:
        tt0 = np.linspace(0.0, total, 6)
        xy = np.column_stack([np.interp(tt0, t, xy[:, 0]), np.interp(tt0, t, xy[:, 1])])
        t = tt0
    dm = total / (len(t) - 1)
    lam = bandwidth_m**4 / dm
    sx = make_smoothing_spline(t, xy[:, 0], lam=lam)
    sy = make_smoothing_spline(t, xy[:, 1], lam=lam)
    n = max(1, round(total / step))
    tt = np.linspace(0.0, total, n + 1)
    return np.column_stack([sx(tt), sy(tt)])


def _core(xy: FloatArray, p: dict[str, float], h: float | None) -> CurvatureResult:
    if h is not None:
        xy = spline_resample(xy, h, p["step"])
    s, xy = resample_uniform(np.asarray(xy, dtype=float), p["step"])
    n = len(s)
    step = float(s[1] - s[0]) if n > 1 else p["step"]
    xy_s = savgol_xy(xy, step, p["window"], int(p["order"])) if n > 3 else xy
    k = max(1, round(p["chord"] / step))
    kappa = three_point_curvature(xy_s, k)
    if h is not None and p.get("shrink", 0.0):
        kappa = kappa / (1.0 + h * h * kappa * kappa)
    heading = heading_unwrapped(xy_s)
    length = float(s[-1]) if n else 0.0
    metrics: dict[str, Any] = {
        "length_m": length,
        "min_radius_m": None,
        "max_curvature": None,
        "mean_curvature": None,
        "mean_radius_m": None,
        "curvature_rate_max": None,
        "curve_length_m": 0.0,
        "straight_length_m": length,
        "curve_count": 0,
        "turn_deg": float(np.degrees(np.ptp(heading))) if n > 1 else 0.0,
        "net_turn_deg": float(np.degrees(heading[-1] - heading[0])) if n > 1 else 0.0,
        "bandwidth_m": h,
    }
    valid = ~np.isnan(kappa)
    curves: list[CurveSegment] = []
    if valid.sum() >= 3:
        absk = np.abs(kappa[valid])
        mw = max(1, round(p["min_arc"] / step))
        if mw % 2 == 0:
            mw += 1
        mw = min(mw, len(absk) if len(absk) % 2 == 1 else len(absk) - 1)
        med = median_filter(absk, size=max(1, mw), mode="nearest")
        kmax = float(np.max(med))
        metrics["max_curvature"] = kmax
        # 直線（R > 100 km 相当）は最小曲率半径なしとする
        metrics["min_radius_m"] = 1.0 / kmax if kmax > 1e-5 else None
        metrics["mean_curvature"] = float(np.mean(absk))
        curve_mask = absk > 1.0 / p["curve_r"]
        if curve_mask.any():
            metrics["mean_radius_m"] = float(1.0 / np.mean(absk[curve_mask]))
        # 曲率変化率：窓 rate_window_m の線形回帰傾き（savgol 1 次）に移動中央値を適用した最大値
        kv = kappa[valid]
        dw = max(5, round(p["rate_window"] / step))
        if dw % 2 == 0:
            dw += 1
        if len(kv) > dw:
            dk = np.abs(savgol_filter(kv, dw, 1, deriv=1, delta=step, mode="interp"))
            # 端部は平滑化スプラインの自然境界条件（端で κ→0）の影響を受けるので帯域幅ぶん除外する
            margin = dw // 2 + (round(2.0 * h / step) if h else 0)
            dk = (
                dk[margin : len(dk) - margin] if len(dk) > 2 * margin + 1 else dk[dw // 2 : len(dk) - dw // 2]
            )
            dk = median_filter(dk, size=min(mw, len(dk) if len(dk) % 2 else len(dk) - 1), mode="nearest")
            metrics["curvature_rate_max"] = float(np.max(dk))
        curves = find_curves(s[valid], kv, p["curve_r"], p["curve_min_len"])
        clen = float(sum(c.length_m for c in curves))
        metrics["curve_length_m"] = clen
        metrics["straight_length_m"] = max(0.0, length - clen)
        metrics["curve_count"] = len(curves)
    return CurvatureResult(s=s, xy=xy_s, kappa=kappa, metrics=metrics, curves=curves, heading=heading)


def analyze_curvature(xy_raw: FloatArray, kind: ElementKind, analysis_cfg: dict[str, Any]) -> CurvatureResult:
    p = _params(analysis_cfg, kind)
    vs = analysis_cfg.get("geometry_smoothing", {}).get("vertex_spline", {})
    xy_raw = np.asarray(xy_raw, dtype=float)
    if not vs.get("enabled", False) or len(dedupe_points(xy_raw)) < 2:
        return _core(xy_raw, p, None)
    p["shrink"] = 1.0 if vs.get("shrinkage_correction", True) else 0.0
    h = float(vs["ramp_bandwidth_m"] if kind == "ramp" else vs["mainline_bandwidth_m"])
    res = _core(xy_raw, p, h)
    for _ in range(int(vs.get("iterations", 2))):
        r = res.metrics.get("min_radius_m")
        if r is None:
            break
        h_new = float(np.clip(float(vs["adaptive_alpha"]) * r, vs["min_bandwidth_m"], vs["max_bandwidth_m"]))
        if abs(h_new - h) < 0.05 * h:
            break
        h = h_new
        res = _core(xy_raw, p, h)
    return res
