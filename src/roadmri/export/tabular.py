"""データ出力（§4.17.1）。CSV（BOM 付き）、Excel、GeoJSON、GeoPackage、GeoParquet、SQLite。

すべての出力に run_id 列と provenance.json を同梱し、ZIP にまとめる。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely import wkb

from roadmri.config import Config
from roadmri.store.duckdb_store import Store

DATASETS = {
    "facilities": ("facility", "facility_id", "geom", "IC/JCTマスター"),
    "ramps": ("ramp", "ramp_id", "geom", "ランプマスター"),
    "sections": ("section", "section_id", "geom", "区間マスター"),
    "samples": ("sample_point", None, None, "サンプルポイント"),
    "section_distances": ("section_distance", None, None, "区間距離（定義別）"),
    "quality_flags": ("quality_flag", None, None, "品質フラグ"),
}
FORMATS = ("csv", "xlsx", "geojson", "gpkg", "parquet", "sqlite")
TARGET_OF = {"facilities": "facility", "ramps": "ramp", "sections": "section"}


class ExportError(ValueError):
    pass


def load_dataset(store: Store, dataset: str, ids: set[str] | None = None) -> pd.DataFrame:
    if dataset not in DATASETS:
        raise ExportError(f"データセットが不正です: {dataset}")
    table, key, _, _ = DATASETS[dataset]
    df = store.df(f"SELECT * FROM {table}")
    if ids is not None and key is not None:
        df = df[df[key].isin(ids)]
    elif ids is not None and dataset == "samples":
        df = df[df["element_id"].isin(ids) | df["section_id"].isin(ids)]
    run = store.current_run() or {}
    if "run_id" not in df.columns:
        df["run_id"] = run.get("run_id")
    return df.reset_index(drop=True)


def to_geodf(df: pd.DataFrame, geom_col: str | None) -> gpd.GeoDataFrame:
    if geom_col and geom_col in df.columns:
        geoms = [wkb.loads(bytes(g)) if g is not None else None for g in df[geom_col]]
        out = df.drop(columns=[c for c in (geom_col, "hull", "from_point", "to_point") if c in df.columns])
        return gpd.GeoDataFrame(_flatten(out), geometry=geoms, crs="EPSG:4326")
    if {"lon", "lat"} <= set(df.columns):
        return gpd.GeoDataFrame(
            _flatten(df), geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326"
        )
    return gpd.GeoDataFrame(_flatten(df))


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """リスト・辞書・バイト列の列を JSON 文字列に変換（表形式の出力用）。"""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object:
            sample = next(
                (v for v in out[c] if v is not None and not (isinstance(v, float) and pd.isna(v))), None
            )
            if isinstance(sample, (bytes, bytearray, memoryview)):
                out[c] = [wkb.loads(bytes(v)).wkt if v is not None else None for v in out[c]]
            elif isinstance(sample, (list, dict, tuple)) or hasattr(sample, "tolist"):
                out[c] = [
                    json.dumps(v.tolist() if hasattr(v, "tolist") else v, ensure_ascii=False, default=str)
                    if v is not None
                    else None
                    for v in out[c]
                ]
    for c in out.columns:
        if str(out[c].dtype).startswith("datetime64") and getattr(out[c].dt, "tz", None) is not None:
            out[c] = out[c].dt.tz_convert("UTC").dt.tz_localize(None)
    return out


def provenance(store: Store) -> dict[str, Any]:
    run = store.current_run() or {}
    prov = run.get("provenance")
    if isinstance(prov, str):
        prov = json.loads(prov)
    return prov or {"run_id": run.get("run_id")}


def write_dataset(df: pd.DataFrame, dataset: str, fmt: str, out_dir: Path, prov: dict[str, Any]) -> Path:
    if fmt not in FORMATS:
        raise ExportError(f"形式が不正です: {fmt}")
    out_dir.mkdir(parents=True, exist_ok=True)
    geom_col = DATASETS[dataset][2] if dataset in DATASETS else "geom"
    stem = out_dir / dataset
    if fmt == "csv":
        p = stem.with_suffix(".csv")
        _flatten(df).to_csv(p, index=False, encoding="utf-8-sig")
    elif fmt == "xlsx":
        p = stem.with_suffix(".xlsx")
        with pd.ExcelWriter(p, engine="openpyxl") as xw:
            _flatten(df).to_excel(xw, sheet_name=dataset[:31], index=False)
            pv = pd.DataFrame(
                [
                    {
                        "項目": k,
                        "値": json.dumps(v, ensure_ascii=False, default=str)
                        if isinstance(v, (dict, list))
                        else v,
                    }
                    for k, v in prov.items()
                ]
            )
            pv.to_excel(xw, sheet_name="来歴", index=False)
    elif fmt == "geojson":
        p = stem.with_suffix(".geojson")
        to_geodf(df, geom_col).to_file(p, driver="GeoJSON", engine="pyogrio")
    elif fmt == "gpkg":
        p = stem.with_suffix(".gpkg")
        to_geodf(df, geom_col).to_file(p, layer=dataset, driver="GPKG", engine="pyogrio")
    elif fmt == "parquet":
        p = stem.with_suffix(".parquet")
        g = to_geodf(df, geom_col)
        if "geometry" in g.columns and g.geometry is not None and len(g):
            g.to_parquet(p, schema_version="1.1.0")
        else:
            _flatten(df).to_parquet(p)
    else:
        p = stem.with_suffix(".sqlite")
        if p.exists():
            p.unlink()
        flat = _flatten(
            df.drop(columns=[c for c in ("geom", "hull", "from_point", "to_point") if c in df.columns])
        )
        if geom_col and geom_col in df.columns:
            flat["geom_wkb"] = [bytes(g) if g is not None else None for g in df[geom_col]]
        with sqlite3.connect(p) as con:
            flat.to_sql(dataset, con, index=False)
            pd.DataFrame(
                [{"key": "provenance", "value": json.dumps(prov, ensure_ascii=False, default=str)}]
            ).to_sql("provenance", con, index=False)
    (out_dir / "provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return p


def zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src))
    return dest


def ids_from_query(store: Store, dataset: str, query: dict[str, Any] | None) -> set[str] | None:
    if not query:
        return None
    from roadmri.analysis.search import run_query

    q = {**query, "limit": 100000}
    res = run_query(store, q)
    tgt = TARGET_OF.get(dataset)
    qt = {"ramps": "ramp", "facilities": "facility", "sections": "section"}.get(
        q.get("target", "ramp"), q.get("target", "ramp")
    )
    if tgt is None:
        key = {"ramp": "ramp_id", "facility": "facility_id", "section": "section_id"}[qt]
        return set(res[key])
    if tgt == qt:
        return set(res[f"{tgt}_id"])
    if tgt == "facility" and qt == "ramp":
        return set(res["facility_id"])
    if tgt == "ramp" and qt == "facility":
        return set(
            store.df(
                "SELECT ramp_id FROM ramp WHERE facility_id IN (SELECT unnest(?))", [list(res["facility_id"])]
            )["ramp_id"]
        )
    raise ExportError("検索対象とデータセットの組み合わせに対応していません")


def export_dataset(
    store: Store,
    cfg: Config,
    dataset: str,
    fmt: str,
    query: dict[str, Any] | None = None,
    out: Path | None = None,
    ids: set[str] | None = None,
) -> Path:
    if ids is None:
        ids = ids_from_query(store, dataset, query)
    df = load_dataset(store, dataset, ids)
    prov = provenance(store)
    if query:
        prov = {**prov, "query": query}
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    base = out or cfg.path("exports_dir")
    work = base / f"{dataset}_{fmt}_{ts}"
    write_dataset(df, dataset, fmt, work, prov)
    return zip_dir(work, base / f"{dataset}_{fmt}_{ts}.zip")
