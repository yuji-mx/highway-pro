"""実走データ（GPS/CAN/MF4）の入力アダプタとマップマッチングのインターフェース（P6、§4.21）。

MVP ではインターフェースと出力スキーマ（drive_match）だけを定義する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

#: drive_match テーブルの列（P6 で DuckDB に追加する）
DRIVE_MATCH_COLUMNS = (
    "drive_id",
    "t",
    "lon",
    "lat",
    "gps_alt_m",
    "speed_kmh",
    "accel_mps2",
    "element_type",
    "element_id",
    "s_m",
    "match_prob",
    "dem_z_m",
    "dem_grade_pct",
    "meas_grade_pct",
    "curvature",
    "load",
)


@dataclass
class DriveSample:
    t: float
    lon: float
    lat: float
    alt_m: float | None = None
    speed_kmh: float | None = None
    heading_deg: float | None = None
    channels: dict[str, float] | None = None


class DriveLogReader(ABC):
    """CSV / GPX / GeoJSON / MDF(MF4、asammdf) / 緯度経度付き CAN ログの読み込み。"""

    suffixes: tuple[str, ...] = ()

    @abstractmethod
    def read(self, path: Path) -> Iterator[DriveSample]:
        """時刻順のサンプルを返す。"""


def map_match(samples: list[DriveSample], network: object, sigma_m: float = 10.0) -> pd.DataFrame:
    """HMM マップマッチング（最近傍道路、進行方向、速度、ネットワーク接続、時刻連続性）。P6 で実装する。"""
    raise NotImplementedError("実走データ照合は P6 で実装予定です")
