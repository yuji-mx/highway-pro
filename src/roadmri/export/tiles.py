"""表示用ベクタ（FlatGeobuf）の生成（§5.3）。解析用の高精度データとは分離し、簡略化したものを出す。"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkb

LAYERS = ("mainline", "ramps", "general", "facilities", "tolls", "structures")


def to_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    if df.empty:
        return gpd.GeoDataFrame({"feature_id": []}, geometry=[], crs="EPSG:4326")
    props = [json.loads(p) if isinstance(p, str) else (p or {}) for p in df["props"]]
    pdf = pd.DataFrame(props)
    pdf.insert(0, "feature_id", df["feature_id"].to_numpy())
    geoms = [wkb.loads(bytes(g)) for g in df["geom"]]
    return gpd.GeoDataFrame(pdf, geometry=geoms, crs="EPSG:4326")


def write_layers(disp: pd.DataFrame, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for layer in LAYERS:
        sub = disp[disp["layer"] == layer] if len(disp) else disp
        gdf = to_gdf(sub)
        p = out_dir / f"{layer}.fgb"
        if p.exists():
            p.unlink()
        if len(gdf):
            gdf.to_file(p, driver="FlatGeobuf", engine="pyogrio")
            paths.append(p)
    return paths
