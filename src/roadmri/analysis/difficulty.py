"""車両性能向け複合指標と難易度候補値（§4.15）。スコアは候補抽出用の目安であり絶対評価ではない。"""

from __future__ import annotations

from typing import Any


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def composite_indicators(m: dict[str, Any], flow: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    c = cfg["composite"]
    minr = m.get("min_radius_m")
    length = m.get("length_m") or 0.0
    accel = m.get("accel_lane_len_m")
    zmax, zmin = m.get("z_max"), m.get("z_min")
    zr = (float(zmax) - float(zmin)) if zmax is not None and zmin is not None else None
    return {
        "small_r_uphill": m.get("small_r_uphill"),
        "small_r_long": bool(
            minr is not None and minr <= c["small_r_m"] and length >= c["small_r_long_len_m"]
        ),
        "continuous_curve_relief": (m.get("curve_length_m") or 0.0) * zr if zr is not None else None,
        "pre_merge_grade": m.get("pre_merge_grade"),
        "short_accel": (accel < c["short_accel_m"]) if accel is not None else None,
        "curvature_rate_max": m.get("curvature_rate_max"),
        "end_uphill": (m.get("end_grade") or 0.0) > c["end_uphill_pct"]
        if m.get("end_grade") is not None
        else None,
        "high_alt_steep": bool(
            (m.get("z_max") or 0.0) >= c["high_alt_m"]
            and (m.get("grade_up_max_w100") or 0.0) >= c["high_alt_grade_pct"]
        ),
    }


def difficulty(m: dict[str, Any], flow: str | None, cfg: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    p = cfg["params"]
    w = cfg["weights"]
    minr = m.get("min_radius_m")
    c_r = _clamp((p["r_ref_m"] - minr) / p["r_span_m"]) if minr is not None else 0.0
    c_g = _clamp((m.get("grade_up_max_w100") or 0.0) / p["g_ref_pct"])
    c_l = _clamp((m.get("length_m") or 0.0) / p["l_ref_m"])
    c_k = _clamp((m.get("curvature_rate_max") or 0.0) / p["k_ref"])
    accel = m.get("accel_lane_len_m")
    missing_m = False
    if flow == "entry":
        if accel is None:
            c_m = 0.0
            missing_m = True
        else:
            c_m = _clamp((p["accel_ref_m"] - accel) / p["accel_span_m"])
    else:
        c_m = 0.0
    comps = {"R": c_r, "G": c_g, "L": c_l, "K": c_k, "M": c_m}
    contrib = {k: w[k] * v for k, v in comps.items()}
    score = 100.0 * sum(contrib.values())
    return score, {
        "components": comps,
        "weights": w,
        "contributions": {k: 100 * v for k, v in contrib.items()},
        "accel_missing": missing_m,
    }
