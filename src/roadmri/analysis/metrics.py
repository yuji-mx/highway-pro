"""統計比較の指標レジストリ（§4.14.4）。指標の追加はこのレジストリへの 1 エントリ追加で済む。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    target: str  # ramp / facility / section
    unit: str
    digits: int
    getter: Callable[[pd.DataFrame], np.ndarray]
    harsher_if_larger: bool = True


def col(name: str) -> Callable[[pd.DataFrame], np.ndarray]:
    def f(df: pd.DataFrame) -> np.ndarray:
        if name not in df.columns:
            return np.array([], dtype=float)
        out: np.ndarray = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=float)
        return out

    return f


METRICS: dict[str, Metric] = {
    m.key: m
    for m in [
        Metric("ramp.length_m", "ランプ長", "ramp", "m", 0, col("length_m")),
        Metric("ramp.min_radius_m", "ランプ最小曲率半径", "ramp", "m", 0, col("min_radius_m"), False),
        Metric("ramp.grade_up_max_w100", "ランプ100m最大上り", "ramp", "%", 2, col("grade_up_max_w100")),
        Metric("ramp.grade_down_max_w100", "ランプ100m最大下り", "ramp", "%", 2, col("grade_down_max_w100")),
        Metric("ramp.abs_elev_diff_m", "ランプ|標高差|", "ramp", "m", 1, col("abs_elev_diff_m")),
        Metric("ramp.z_max", "ランプ最高標高", "ramp", "m", 0, col("z_max")),
        Metric("ramp.difficulty", "難易度候補値", "ramp", "", 1, col("difficulty")),
        Metric("ramp.curvature_rate_max", "曲率変化率", "ramp", "1/m²", 6, col("curvature_rate_max")),
        Metric("ramp.accel_lane_len_m", "加速車線長", "ramp", "m", 0, col("accel_lane_len_m"), False),
        Metric("facility.ramp_count", "施設のランプ数", "facility", "本", 0, col("ramp_count")),
        Metric("facility.loop_ramp_count", "施設のループ数", "facility", "本", 0, col("loop_ramp_count")),
        Metric("facility.shape_confidence", "分類信頼度", "facility", "", 2, col("shape_confidence"), False),
        Metric("facility.min_radius_m", "施設内最小R", "facility", "m", 0, col("min_radius_m"), False),
        Metric("section.dist_standard_m", "区間距離", "section", "m", 0, col("dist_standard_m")),
        Metric("section.grade_up_max_w100", "区間100m最大上り", "section", "%", 2, col("grade_up_max_w100")),
        Metric("section.gain_per_km", "累積上昇/km", "section", "m/km", 1, col("gain_per_km")),
        Metric("section.min_radius_m", "区間最小曲率半径", "section", "m", 0, col("min_radius_m"), False),
        Metric("section.tunnel_ratio", "トンネル比率", "section", "", 3, col("tunnel_ratio")),
    ]
}
