"""標高の平滑化（§4.7.3）。移動平均・移動中央値・Savitzky–Golay・距離ベースのローパス・外れ値除去。"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import butter, filtfilt, savgol_filter

FloatArray = NDArray[np.float64]
Method = Literal["moving_average", "moving_median", "savgol", "lowpass", "outlier", "outlier_savgol", "none"]
METHODS: tuple[str, ...] = (
    "moving_average",
    "moving_median",
    "savgol",
    "lowpass",
    "outlier",
    "outlier_savgol",
    "none",
)


def _odd_window(window_m: float, step: float, n: int, minimum: int = 3) -> int:
    w = max(minimum, round(window_m / step))
    if w % 2 == 0:
        w += 1
    if w > n:
        w = n if n % 2 == 1 else n - 1
    return w


def fill_nan(z: FloatArray) -> FloatArray:
    z = np.asarray(z, dtype=float).copy()
    bad = np.isnan(z)
    if bad.all() or not bad.any():
        return z
    idx = np.arange(len(z))
    z[bad] = np.interp(idx[bad], idx[~bad], z[~bad])
    return z


def _pad_odd(z: FloatArray, k: int) -> FloatArray:
    """端点まわりの点対称（odd）拡張。線形トレンドを端でも保存する。"""
    k = min(k, len(z) - 1)
    if k <= 0:
        return z
    left = 2 * z[0] - z[k:0:-1]
    right = 2 * z[-1] - z[-2 : -k - 2 : -1]
    return np.concatenate([left, z, right])


def moving_average(z: FloatArray, step: float, window_m: float) -> FloatArray:
    w = _odd_window(window_m, step, len(z), 1)
    if w <= 1:
        return z.copy()
    k = w // 2
    zp = _pad_odd(z, k)
    kk = (len(zp) - len(z)) // 2
    out: FloatArray = uniform_filter1d(zp, size=w, mode="nearest")[kk : kk + len(z)]
    return out


def moving_median(z: FloatArray, step: float, window_m: float) -> FloatArray:
    w = _odd_window(window_m, step, len(z), 1)
    if w <= 1:
        return z.copy()
    k = w // 2
    zp = _pad_odd(z, k)
    kk = (len(zp) - len(z)) // 2
    out: FloatArray = median_filter(zp, size=w, mode="nearest")[kk : kk + len(z)]
    return out


def savgol(z: FloatArray, step: float, window_m: float, order: int = 2) -> FloatArray:
    """Savitzky–Golay。端部（窓の半分）は外挿分散の小さい 1 次の当てはめで置き換える。"""
    w = _odd_window(window_m, step, len(z), order + 2)
    if w <= order:
        return z.copy()
    out = savgol_filter(z, w, order, mode="interp")
    if order > 1 and len(z) >= w:
        edge = savgol_filter(z, w, 1, mode="interp")
        k = w // 2
        out[:k] = edge[:k]
        out[-k:] = edge[-k:]
    return out  # type: ignore[no-any-return]


def lowpass(z: FloatArray, step: float, cutoff_wavelength_m: float, order: int = 2) -> FloatArray:
    """ゼロ位相 Butterworth。カットオフ波長 λc → 正規化周波数 = (1/λc) / (1/(2·step))。"""
    nyq = 1.0 / (2.0 * step)
    wn = (1.0 / cutoff_wavelength_m) / nyq
    if wn >= 1.0 or len(z) < 3 * (order + 1) * 3:
        return z.copy()
    b, a = butter(order, wn)
    padlen = min(len(z) - 1, max(3 * max(len(a), len(b)), round(2 * cutoff_wavelength_m / step)))
    return filtfilt(b, a, z, padtype="odd", padlen=padlen)  # type: ignore[no-any-return]


def robust_local_reference(z: FloatArray, step: float, window_m: float) -> FloatArray:
    """局所的な頑健直線あてはめ：傾き＝差分の移動中央値、切片＝傾き補正後の窓内中央値。"""
    n = len(z)
    w = _odd_window(window_m, step, n, 3)
    if w < 3:
        return z.copy()
    k = w // 2
    slope = moving_median(np.gradient(z, step), step, window_m)
    zp = _pad_odd(z, k)
    kk = (len(zp) - n) // 2
    win = np.lib.stride_tricks.sliding_window_view(zp, 2 * kk + 1)[:n]
    offs = (np.arange(2 * kk + 1) - kk) * step
    return np.median(win - slope[:, None] * offs[None, :], axis=1)


def remove_outliers(
    z: FloatArray, step: float, window_m: float, k_mad: float = 3.0
) -> tuple[FloatArray, NDArray[np.bool_]]:
    """ローカル中央値との差 > k×MAD（正規分布換算 1.4826×MAD）の点を補間で置換する（Hampel 型）。

    勾配のある道路では単純な移動中央値が元の点列とほぼ一致するため、局所的な頑健直線
    （傾き補正後の中央値）を基準値とする。
    """
    if len(z) < 5:
        return z.copy(), np.zeros(len(z), dtype=bool)
    ref = robust_local_reference(z, step, window_m)
    resid = z - ref
    mad = float(np.median(np.abs(resid - np.median(resid))))
    scale = max(1.4826 * mad, 0.01)
    out = np.abs(resid) > k_mad * scale
    zc = z.copy()
    zc[out] = np.nan
    return fill_nan(zc), out


def smooth_elevation(
    z: FloatArray, step: float, kind: str, cfg: dict[str, Any], method: str | None = None
) -> FloatArray:
    sc = cfg["elevation"]["smoothing"]
    method = method or str(sc["method"])
    window = float(sc["ramp_window_m"] if kind == "ramp" else sc["mainline_window_m"])
    order = int(sc.get("order", 2))
    zf = fill_nan(z)
    if np.isnan(zf).all() or len(zf) < 3:
        return zf
    if method == "moving_average":
        return moving_average(zf, step, window)
    if method == "moving_median":
        return moving_median(zf, step, window)
    if method == "savgol":
        return savgol(zf, step, window, order)
    if method == "lowpass":
        cutoff = float(sc["lowpass_ramp_cutoff_m"] if kind == "ramp" else sc["lowpass_mainline_cutoff_m"])
        return lowpass(zf, step, cutoff)
    if method == "outlier":
        return remove_outliers(zf, step, window, float(sc["outlier_mad"]))[0]
    if method == "outlier_savgol":
        cleaned, _ = remove_outliers(zf, step, window, float(sc["outlier_mad"]))
        return savgol(cleaned, step, window, order)
    if method == "none":
        return zf
    raise ValueError(f"未知の平滑化方式: {method}")
