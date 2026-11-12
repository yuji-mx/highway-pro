"""構造物（橋梁・高架・トンネル等）の識別と標高補正（§4.7.4）。"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

STRUCTURE_LOW_CONF = {"bridge", "viaduct", "tunnel", "covered", "layer"}


def classify_structure(tags: dict[str, Any]) -> str:
    """Way タグから構造物種別を返す。"""
    br = str(tags.get("bridge") or "no")
    if br not in ("no", ""):
        return "viaduct" if br == "viaduct" else "bridge"
    tn = str(tags.get("tunnel") or "no")
    if tn not in ("no", ""):
        return "tunnel"
    if str(tags.get("covered") or "no") == "yes":
        return "tunnel"
    if str(tags.get("cutting") or "no") not in ("no", ""):
        return "cutting"
    if str(tags.get("embankment") or "no") not in ("no", ""):
        return "embankment"
    return "none"


def parse_layer(tags: dict[str, Any]) -> int:
    try:
        return int(str(tags.get("layer", "0")).split(";")[0])
    except ValueError:
        return 0


def is_low_confidence(structure: str, layer: int) -> bool:
    return structure in ("bridge", "viaduct", "tunnel") or layer != 0


def _runs(mask: BoolArray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _hermite(t: FloatArray, z0: float, z1: float, m0: float, m1: float, length: float) -> FloatArray:
    h00 = 2 * t**3 - 3 * t**2 + 1
    h10 = t**3 - 2 * t**2 + t
    h01 = -2 * t**3 + 3 * t**2
    h11 = t**3 - t**2
    return h00 * z0 + h10 * length * m0 + h01 * z1 + h11 * length * m1


def correct_structures(
    s: FloatArray, z: FloatArray, low_conf: BoolArray, cfg: dict[str, Any], method: str | None = None
) -> FloatArray:
    """構造物区間を前後の非構造物点（各 anchor_len_m の平均）で端点補間する。"""
    sc = cfg["elevation"]["structure_correction"]
    method = method or str(sc["method"])
    anchor = float(sc["anchor_len_m"])
    zc = np.asarray(z, dtype=float).copy()
    if not low_conf.any():
        return zc
    for i, j in _runs(low_conf):
        s0, s1 = s[i], s[j]
        before = (~low_conf) & (s < s0) & (s >= s0 - anchor) & ~np.isnan(z)
        after = (~low_conf) & (s > s1) & (s <= s1 + anchor) & ~np.isnan(z)
        has_b, has_a = bool(before.any()), bool(after.any())
        if not has_b and not has_a:
            continue

        # 端点：アンカー区間の平均（位置はアンカー区間の平均距離）、端点勾配：アンカー区間の線形回帰
        def anchor_fit(m: BoolArray) -> tuple[float, float, float]:
            ss, zz = s[m], z[m]
            if len(ss) >= 2 and np.ptp(ss) > 0:
                slope, icpt = np.polyfit(ss, zz, 1)
            else:
                slope, icpt = 0.0, float(np.mean(zz))
            sm = float(np.mean(ss))
            return sm, float(slope * sm + icpt), float(slope)

        if has_b and has_a:
            sb, zb, mb = anchor_fit(before)
            sa, za, ma = anchor_fit(after)
            # 端点をアンカー平均位置から構造物の境界へ外挿する
            z0 = zb + mb * (s0 - sb)
            z1 = za + ma * (s1 - sa)
            length = s1 - s0
            seg = slice(i, j + 1)
            if length <= 0:
                zc[seg] = z0
                continue
            t = (s[seg] - s0) / length
            if method == "hermite":
                zc[seg] = _hermite(t, z0, z1, mb, ma, length)
            else:
                zc[seg] = z0 + (z1 - z0) * t
        else:
            sm, zm, m = anchor_fit(before if has_b else after)
            seg = slice(i, j + 1)
            zc[seg] = zm + m * (s[seg] - sm)
    return zc
