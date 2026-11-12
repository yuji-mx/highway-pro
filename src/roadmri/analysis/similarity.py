"""類似地点検索（§4.12）。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from roadmri.store.duckdb_store import Store

FAC_FEATURES = [
    ("ramp_count", "ランプ数", lambda v: v),
    ("loop_ramp_count", "ループ数", lambda v: v),
    ("mean_ramp_length_m", "平均ランプ長", lambda v: v),
    ("log_min_radius", "最小R(log)", lambda v: v),
    ("grade_up_max_w100", "最大上り", lambda v: v),
    ("max_elev_diff_m", "最大高低差", lambda v: v),
    ("connection_directions", "接続方向数", lambda v: v),
    ("design_speed_kmh", "道路規格", lambda v: v),
]
SQUARES = [
    "ramp_count",
    "loop_ramp_count",
    "mean_ramp_length_m",
    "log_min_radius",
    "grade_up_max_w100",
    "max_elev_diff_m",
    "connection_directions",
]


def _fac_frame(store: Store) -> pd.DataFrame:
    df = store.df(
        "SELECT facility_id, name, facility_type, shape_class, shape_confidence, ramp_count, loop_ramp_count, "
        "mean_ramp_length_m, min_radius_m, grade_up_max_w100, max_elev_diff_m, connection_directions, "
        "design_speed_kmh, quality_level, route_refs, lon, lat FROM facility WHERE coalesce(excluded,false)=false"
    )
    df["log_min_radius"] = np.log(
        pd.to_numeric(df["min_radius_m"], errors="coerce").fillna(5000).clip(lower=1)
    )
    return df


def facility_similarity_row(
    a: pd.Series, b: pd.Series, sigma: dict[str, float], cfg: dict[str, Any]
) -> dict[str, Any]:
    w = cfg["facility"]["weights"]
    num, den = 0.0, 0.0
    per: dict[str, float | None] = {}
    for key, _, _ in FAC_FEATURES:
        va, vb = a.get(key), b.get(key)
        wi = float(w.get(key, 1.0))
        if va is None or vb is None or pd.isna(va) or pd.isna(vb):
            per[key] = None
            continue
        s = sigma.get(key) or 1.0
        sim = math.exp(-abs(float(va) - float(vb)) / s)
        per[key] = sim
        num += wi * sim
        den += wi
    ws, wt = float(cfg["facility"]["w_shape"]), float(cfg["facility"]["w_type"])
    shape_eq = a.get("shape_class") == b.get("shape_class")
    type_eq = a.get("facility_type") == b.get("facility_type")
    num += ws * shape_eq + wt * type_eq
    den += ws + wt
    return {
        "similarity": num / den if den else 0.0,
        "features": per,
        "shape_match": bool(shape_eq),
        "type_match": bool(type_eq),
    }


def _sigma(df: pd.DataFrame) -> dict[str, float]:
    out = {}
    for key, _, _ in FAC_FEATURES:
        v = pd.to_numeric(df[key], errors="coerce").dropna()
        sd = float(v.std(ddof=1)) if len(v) > 1 else 0.0
        out[key] = sd if sd > 0 else 1.0
    return out


def similar_facilities(
    store: Store, facility_id: str, cfg: dict[str, Any], limit: int | None = None
) -> dict[str, Any]:
    df = _fac_frame(store)
    if facility_id not in set(df["facility_id"]):
        raise LookupError(f"施設が見つかりません: {facility_id}")
    base = df[df["facility_id"] == facility_id].iloc[0]
    is_sapa = base["facility_type"] in ("SA", "PA")
    pool = df[df["facility_type"].isin(["SA", "PA"]) == is_sapa]
    sigma = _sigma(pool)
    rows = []
    for _, r in pool.iterrows():
        if r["facility_id"] == facility_id:
            continue
        s = facility_similarity_row(base, r, sigma, cfg)
        rows.append(
            {
                **{
                    k: _py(r[k])
                    for k in (
                        "facility_id",
                        "name",
                        "facility_type",
                        "shape_class",
                        "ramp_count",
                        "min_radius_m",
                        "grade_up_max_w100",
                        "loop_ramp_count",
                    )
                },
                **s,
                "values": {k: _py(r[k]) for k, _, _ in FAC_FEATURES},
            }
        )
    rows.sort(key=lambda x: (-x["similarity"], x["facility_id"]))
    n = limit or int(cfg["facility"]["top_n"])
    return {
        "base": {k: _py(base[k]) for k in base.index},
        "results": rows[:n],
        "squares": SQUARES,
        "feature_labels": {k: lab for k, lab, _ in FAC_FEATURES},
        "sigma": sigma,
    }


def compare_facilities(store: Store, a_id: str, b_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    df = _fac_frame(store)
    ids = set(df["facility_id"])
    for i in (a_id, b_id):
        if i not in ids:
            raise LookupError(f"施設が見つかりません: {i}")
    a = df[df["facility_id"] == a_id].iloc[0]
    b = df[df["facility_id"] == b_id].iloc[0]
    s = facility_similarity_row(a, b, _sigma(df), cfg)
    table_keys = [
        ("facility_type", "種別"),
        ("shape_class", "形状分類"),
        ("shape_confidence", "信頼度"),
        ("ramp_count", "ランプ数"),
        ("loop_ramp_count", "ループ数"),
        ("mean_ramp_length_m", "平均ランプ長"),
        ("min_radius_m", "最小R"),
        ("grade_up_max_w100", "最大上り勾配"),
        ("max_elev_diff_m", "最大高低差"),
        ("connection_directions", "接続方向"),
        ("quality_level", "データ品質"),
    ]
    rows = []
    for k, lab in table_keys:
        va, vb = _py(a[k]), _py(b[k])
        if isinstance(va, float) and isinstance(vb, float):
            differs = not math.isclose(va, vb, rel_tol=0.05, abs_tol=1e-9)
        else:
            differs = va != vb
        rows.append({"key": k, "label": lab, "a": va, "b": vb, "differs": differs})
    return {
        "a": {k: _py(a[k]) for k in a.index},
        "b": {k: _py(b[k]) for k in b.index},
        **s,
        "table": rows,
        "squares": SQUARES,
        "feature_labels": {k: lab for k, lab, _ in FAC_FEATURES},
    }


# ---- ランプ ---------------------------------------------------------------------
RAMP_SCALARS = ["length_m", "log_min_radius", "grade_up_max_w100", "grade_down_max_w100", "abs_elev_diff"]


def _profiles(store: Store, ramp_ids: list[str], n: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    sp = store.df(
        "SELECT element_id, s_m, curvature, grade_w100, grade_inst FROM sample_point "
        "WHERE element_type='ramp' ORDER BY element_id, seq"
    )
    out = {}
    grid = np.linspace(0, 1, n)
    for rid, g in sp.groupby("element_id", sort=False):
        s = g["s_m"].to_numpy(dtype=float)
        if len(s) < 2 or s[-1] <= 0:
            continue
        u = s / s[-1]
        k = np.nan_to_num(g["curvature"].to_numpy(dtype=float))
        gr = g["grade_w100"].to_numpy(dtype=float)
        gi = g["grade_inst"].to_numpy(dtype=float)
        gr = np.where(np.isnan(gr), np.nan_to_num(gi), gr)
        out[str(rid)] = (np.interp(grid, u, k), np.interp(grid, u, gr))
    return out


def similar_ramps(
    store: Store,
    ramp_id: str,
    cfg: dict[str, Any],
    limit: int | None = None,
    pool_ids: set[str] | None = None,
) -> dict[str, Any]:
    rc = cfg["ramp"]
    df = store.df(
        "SELECT r.ramp_id, r.facility_id, f.name AS facility_name, r.flow, f.shape_class, r.length_m, "
        "r.min_radius_m, r.grade_up_max_w100, r.grade_down_max_w100, r.elev_diff_m, r.difficulty "
        "FROM ramp r LEFT JOIN facility f USING (facility_id) WHERE coalesce(r.excluded,false)=false"
    )
    if ramp_id not in set(df["ramp_id"]):
        raise LookupError(f"ランプが見つかりません: {ramp_id}")
    if pool_ids is not None:
        df = df[df["ramp_id"].isin(pool_ids | {ramp_id})]
    df["log_min_radius"] = np.log(
        pd.to_numeric(df["min_radius_m"], errors="coerce").fillna(5000).clip(lower=1)
    )
    df["abs_elev_diff"] = pd.to_numeric(df["elev_diff_m"], errors="coerce").abs()
    for c in RAMP_SCALARS:
        v = pd.to_numeric(df[c], errors="coerce")
        sd = float(v.std(ddof=0)) or 1.0
        df[f"z_{c}"] = ((v - v.mean()) / sd).fillna(0.0)
    profs = _profiles(store, list(df["ramp_id"]), int(rc["profile_points"]))
    allk = np.concatenate([p[0] for p in profs.values()]) if profs else np.zeros(1)
    allg = np.concatenate([p[1] for p in profs.values()]) if profs else np.zeros(1)
    sk = float(np.nanstd(allk)) or 1.0
    sg = float(np.nanstd(allg)) or 1.0
    w = rc["weights"]
    wmap = {
        "length_m": "length_m",
        "log_min_radius": "log_min_radius",
        "grade_up_max_w100": "grade_up_max_w100",
        "grade_down_max_w100": "grade_down_max_w100",
        "abs_elev_diff": "abs_elev_diff_m",
    }
    base = df[df["ramp_id"] == ramp_id].iloc[0]
    bp = profs.get(ramp_id)
    rows = []
    for _, r in df.iterrows():
        if r["ramp_id"] == ramp_id:
            continue
        contrib: dict[str, float] = {}
        for c in RAMP_SCALARS:
            contrib[c] = float(w.get(wmap[c], 1.0)) * float(base[f"z_{c}"] - r[f"z_{c}"]) ** 2
        rp = profs.get(r["ramp_id"])
        if bp is not None and rp is not None:
            dk = np.sqrt(np.mean(((bp[0] - rp[0]) / sk) ** 2))
            if rc.get("allow_mirror", True):
                dk = min(dk, float(np.sqrt(np.mean(((bp[0] + rp[0]) / sk) ** 2))))
            dg = np.sqrt(np.nanmean(((bp[1] - rp[1]) / sg) ** 2))
            contrib["curvature_profile"] = float(w["curvature_profile"]) * float(dk) ** 2
            contrib["grade_profile"] = float(w["grade_profile"]) * float(np.nan_to_num(dg)) ** 2
        pen = 0.0
        if r["flow"] != base["flow"]:
            pen += float(rc["category_penalty"]["flow"])
        if r["shape_class"] != base["shape_class"]:
            pen += float(rc["category_penalty"]["shape_class"])
        d = math.sqrt(sum(contrib.values())) + pen
        rows.append(
            {
                "ramp_id": r["ramp_id"],
                "facility_id": r["facility_id"],
                "facility_name": r["facility_name"],
                "flow": r["flow"],
                "shape_class": r["shape_class"],
                "length_m": _py(r["length_m"]),
                "min_radius_m": _py(r["min_radius_m"]),
                "grade_up_max_w100": _py(r["grade_up_max_w100"]),
                "difficulty": _py(r["difficulty"]),
                "distance": d,
                "similarity": 1.0 / (1.0 + d),
                "contributions": contrib,
                "category_penalty": pen,
            }
        )
    rows.sort(key=lambda x: (-x["similarity"], x["ramp_id"]))
    n = limit or int(rc["top_n"])
    return {
        "base": {
            k: _py(base[k])
            for k in (
                "ramp_id",
                "facility_id",
                "facility_name",
                "flow",
                "shape_class",
                "length_m",
                "min_radius_m",
                "grade_up_max_w100",
            )
        },
        "results": rows[:n],
    }


def ramp_self_similarity() -> float:
    return 1.0


def _py(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(float(v)) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, (list, dict, str, int, bool)):
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v) if not isinstance(v, (int, float)) else v
