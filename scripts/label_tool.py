"""形状分類の正解ラベル付け補助（§14.3）。

1. ラベル付け用 CSV を出力（施設名・座標・推定分類・地理院地図リンク）：
     uv run python scripts/label_tool.py export labels.csv
   → label 列に正解の形状分類を記入する（地理院地図の航空写真で確認）。
2. 精度を計算（top1 / top2 正解率）：
     uv run python scripts/label_tool.py score labels.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from roadmri.config import load_config
from roadmri.store.duckdb_store import Store


def export(out: Path) -> None:
    cfg = load_config()
    st = Store(cfg.path("processed_db"), read_only=True)
    df = st.df(
        "SELECT facility_id, name, facility_type, route_refs, lat, lon, shape_class_auto, shape_confidence_auto, "
        "shape_class_2 FROM facility WHERE in_range AND facility_type NOT IN ('SA','PA') ORDER BY name"
    )
    df["gsi_url"] = [
        f"https://maps.gsi.go.jp/#17/{a}/{o}/&base=seamlessphoto" for a, o in zip(df.lat, df.lon, strict=True)
    ]
    df["label"] = ""
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"{len(df)} 施設を書き出しました: {out}")


def score(path: Path) -> None:
    df = pd.read_csv(path, encoding="utf-8-sig").dropna(subset=["label"])
    df = df[df["label"].astype(str).str.len() > 0]
    if df.empty:
        raise SystemExit("label 列が空です")
    top1 = (df["label"] == df["shape_class_auto"]).mean()
    top2 = ((df["label"] == df["shape_class_auto"]) | (df["label"] == df["shape_class_2"])).mean()
    print(f"n={len(df)}  top1={top1:.3f}（基準 ≥ 0.80）  top2={top2:.3f}（基準 ≥ 0.95）")
    print(
        df[df["label"] != df["shape_class_auto"]][
            ["name", "label", "shape_class_auto", "shape_class_2"]
        ].to_string()
    )


if __name__ == "__main__":
    {"export": export, "score": score}[sys.argv[1]](Path(sys.argv[2]))
