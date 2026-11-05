"""要素（ランプ・区間）ごとの線形・標高・勾配指標とサンプル点の算出（§4.6〜§4.8）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from roadmri.elevation.grade import analyze_grade, max_window_grade_in, tail_mean_grade, window_grade
from roadmri.elevation.smoothing import smooth_elevation
from roadmri.elevation.structures import correct_structures, is_low_confidence
from roadmri.geometry.curvature import analyze_curvature
from roadmri.geometry.projection import Projector
from roadmri.geometry.resample import arc_length, resample_step
from roadmri.ingest.dem import DemSampler

FloatArray = NDArray[np.float64]


@dataclass
class ElementGeom:
    element_type: str  # ramp / mainline
    element_id: str
    xy: FloatArray
    struct: list[tuple[float, float, str, int]]  # (s0, s1, structure, layer)
    section_id: str | None = None
    merge_at_end: bool = False


@dataclass
class ElementResult:
    metrics: dict[str, Any]
    samples: pd.DataFrame
    curves: list[dict[str, Any]] = field(default_factory=list)
    profile_internal: dict[str, FloatArray] = field(default_factory=dict)


def _struct_at(
    s: FloatArray, struct: list[tuple[float, float, str, int]]
) -> tuple[NDArray[np.object_], NDArray[np.int64]]:
    st = np.full(len(s), "none", dtype=object)
    layer = np.zeros(len(s), dtype=np.int64)
    for s0, s1, kind, lay in struct:
        m = (s >= s0 - 1e-6) & (s <= s1 + 1e-6)
        if kind != "none":
            st[m] = kind
        if lay != 0:
            layer[m] = lay
    return st, layer


def element_struct_from_edges(edges: list[Any]) -> list[tuple[float, float, str, int]]:
    out = []
    acc = 0.0
    for e in edges:
        L = float(e.data["length_m"])
        out.append((acc, acc + L, str(e.data["structure"]), int(e.data["layer"])))
        acc += L
    return out


def analyze_element(
    geom: ElementGeom, proj: Projector, sampler: DemSampler, cfg: dict[str, Any]
) -> ElementResult:
    kind: Literal["ramp", "mainline"] = "ramp" if geom.element_type == "ramp" else "mainline"
    xy = np.asarray(geom.xy, dtype=float)
    total = float(arc_length(xy)[-1]) if len(xy) > 1 else 0.0
    # ---- 線形（曲率）
    cres = analyze_curvature(xy, kind, cfg)
    # ---- 標高：内部間隔で DEM を双一次補間
    estep = float(cfg["elevation"]["internal_step_m"][kind])
    s_e, xy_e = resample_step(xy, estep)
    lon_e, lat_e = proj.to_lonlat(xy_e[:, 0], xy_e[:, 1])
    z_raw, src, res = sampler.sample(lon_e, lat_e)
    st_e, layer_e = _struct_at(s_e, geom.struct)
    low = np.array(
        [is_low_confidence(str(a), int(b)) for a, b in zip(st_e, layer_e, strict=True)], dtype=bool
    )
    missing = np.isnan(z_raw)
    # 平滑化は構造物の外（地表追従区間）を基準に、トンネル内の地表高を勾配計算から外す
    z_for_smooth = z_raw.copy()
    z_for_smooth[low] = np.nan
    if np.isnan(z_for_smooth).all():
        z_for_smooth = z_raw.copy()
    z_s = smooth_elevation(z_raw, estep, kind, cfg) if not np.isnan(z_raw).all() else z_raw.copy()
    z_s_masked = (
        smooth_elevation(z_for_smooth, estep, kind, cfg) if not np.isnan(z_for_smooth).all() else z_raw.copy()
    )
    z_c = correct_structures(s_e, z_s_masked, low | missing, cfg)
    if np.isnan(z_c).all():
        z_c = z_s
    g = analyze_grade(s_e, z_c, cfg) if not np.isnan(z_c).all() else None
    # ---- 出力サンプル点
    ostep = float(cfg["resample"]["ramp_step_m" if kind == "ramp" else "mainline_step_m"])
    s_o, xy_o = resample_step(xy, ostep)
    lon_o, lat_o = proj.to_lonlat(xy_o[:, 0], xy_o[:, 1])
    st_o, layer_o = _struct_at(s_o, geom.struct)
    k_int = (
        np.interp(s_o, cres.s, np.nan_to_num(cres.kappa, nan=0.0)) if len(cres.s) > 1 else np.zeros(len(s_o))
    )
    kmask = (
        np.interp(s_o, cres.s, (~np.isnan(cres.kappa)).astype(float)) > 0.5
        if len(cres.s) > 1
        else np.zeros(len(s_o), bool)
    )
    k_out = np.where(kmask, k_int, np.nan)
    idx_near = np.clip(np.searchsorted(s_e, s_o), 0, len(s_e) - 1)

    def interp(a: FloatArray) -> FloatArray:
        if np.isnan(a).all():
            return np.full(len(s_o), np.nan)
        ok = ~np.isnan(a)
        out = np.interp(s_o, s_e[ok], a[ok])
        return out

    g100 = g.g_windows.get(int(cfg["grade"]["default_window_m"])) if g else None
    samples = pd.DataFrame(
        {
            "element_type": geom.element_type,
            "element_id": geom.element_id,
            "section_id": geom.section_id,
            "seq": np.arange(len(s_o), dtype=np.int64),
            "s_m": s_o,
            "lon": lon_o,
            "lat": lat_o,
            "x": xy_o[:, 0],
            "y": xy_o[:, 1],
            "z_dem_raw": np.where(np.isnan(z_raw[idx_near]), np.nan, interp(z_raw)),
            "z_smoothed": interp(z_s),
            "z_corrected": interp(z_c),
            "dem_source": [src[i] for i in idx_near],
            "dem_resolution_m": res[idx_near],
            "grade_inst": interp(g.g_inst) if g else np.nan,
            "grade_w100": np.interp(s_o, s_e, g100, left=np.nan, right=np.nan)
            if g100 is not None
            else np.nan,
            "curvature": k_out,
            "structure": st_o.astype(str),
            "layer": layer_o.astype(np.int64),
            "low_confidence": np.array(
                [is_low_confidence(str(a), int(b)) for a, b in zip(st_o, layer_o, strict=True)]
            )
            | np.isnan(z_raw[idx_near]),
        }
    )
    # ---- 指標
    m: dict[str, Any] = {}
    cm = cres.metrics
    for k in (
        "min_radius_m",
        "max_curvature",
        "mean_curvature",
        "mean_radius_m",
        "curvature_rate_max",
        "curve_length_m",
        "straight_length_m",
        "curve_count",
        "turn_deg",
    ):
        m[k] = cm.get(k)
    m["length_m"] = total
    if g:
        m.update(g.metrics)
    else:
        for w in cfg["grade"]["windows_m"]:
            m[f"grade_up_max_w{w}"] = None
            m[f"grade_down_max_w{w}"] = None
        for k in (
            "grade_inst_max",
            "grade_inst_min",
            "grade_mean",
            "elev_diff_m",
            "z_max",
            "z_min",
            "elev_gain_m",
            "elev_loss_m",
            "cont_up_len_m",
            "cont_down_len_m",
        ):
            m[k] = None
        m["window_truncated"] = True

    def struct_len(kinds: set[str]) -> float:
        return float(sum(s1 - s0 for s0, s1, kd, _ in geom.struct if kd in kinds))

    m["tunnel_len_m"] = struct_len({"tunnel"})
    m["bridge_len_m"] = struct_len({"bridge"})
    m["viaduct_len_m"] = struct_len({"viaduct"})
    n = max(1, len(s_e))
    m["dem_missing_ratio"] = float(missing.sum() / n)
    m["dem_low_conf_ratio"] = float((low | missing).sum() / n)
    m["dem_lowres_ratio"] = float(np.nansum(res > 10) / n) if len(res) else 0.0
    m["dem_sources"] = sorted({str(x) for x in src.tolist() if x})
    # 合流直前・終端
    if g and total > 0:
        g50, _ = window_grade(s_e, z_c, 50.0)
        pm = float(cfg["grade"]["pre_merge_len_m"])
        m["pre_merge_grade"] = max_window_grade_in(s_e, g50, total - pm, total) if geom.merge_at_end else None
        m["end_grade"] = tail_mean_grade(s_e, z_c, float(cfg["grade"]["end_len_m"]))
        # 小R区間内の最大 g_100（複合指標用）
        r_thr = float(cfg.get("_composite", {}).get("small_r_m", 150))
        if g100 is not None and len(cres.s) > 1:
            k_e = np.abs(np.interp(s_e, cres.s, np.nan_to_num(cres.kappa, nan=0.0)))
            mask = (k_e >= 1.0 / r_thr) & ~np.isnan(g100)
            m["small_r_uphill"] = float(np.max(g100[mask])) if mask.any() else None
        else:
            m["small_r_uphill"] = None
    else:
        m["pre_merge_grade"] = m["end_grade"] = m["small_r_uphill"] = None
    curves = [
        {"start_m": c.start_m, "end_m": c.end_m, "side": c.side, "min_radius_m": c.min_radius_m}
        for c in cres.curves
    ]
    return ElementResult(
        metrics=m,
        samples=samples,
        curves=curves,
        profile_internal={"s": s_e, "z_raw": z_raw, "z_s": z_s, "z_c": z_c},
    )
