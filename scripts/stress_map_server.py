"""地図の負荷試験用サーバー：同梱 fixture（宇都宮IC〜鹿沼IC）の表示用データと施設を格子状に複製し、
州規模（California：施設約 4,000）のデータ量にして起動する（オフライン）。

uv run python scripts/stress_map_server.py [port] [copies]

copies は複製数（既定 600。施設数は fixture の施設数 × copies）。解析値は複製元と同じで、位置だけをずらす。
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import time

import numpy as np
import pandas as pd
import uvicorn
from shapely import wkb
from shapely.affinity import translate

from roadmri.api.main import create_app
from roadmri.config import load_config
from roadmri.pipeline import run_pipeline
from roadmri.store.duckdb_store import Store


def _shift_geom(blobs: pd.Series, dx: float, dy: float) -> list[bytes]:
    return [wkb.dumps(translate(wkb.loads(bytes(b)), dx, dy)) if b is not None else None for b in blobs]


def replicate(store: Store, copies: int) -> dict[str, int]:
    disp = store.df("SELECT * FROM display_feature")
    disp["props"] = [json.loads(p) if isinstance(p, str) else p for p in disp["props"]]
    fac = store.df("SELECT * FROM facility")
    w = float(disp["maxx"].max() - disp["minx"].min()) * 1.1
    h = float(disp["maxy"].max() - disp["miny"].min()) * 1.1
    cols = math.ceil(math.sqrt(copies))
    d_parts, f_parts = [], []
    for k in range(copies):
        dx, dy = (k % cols) * w, (k // cols) * h
        if k == 0:
            d_parts.append(disp)
            f_parts.append(fac)
            continue
        d = disp.copy()
        d["feature_id"] = d["feature_id"] + f"#{k}"
        d["geom"] = _shift_geom(d["geom"], dx, dy)
        for c, off in (("minx", dx), ("maxx", dx), ("miny", dy), ("maxy", dy)):
            d[c] = d[c] + off
        # 施設レイヤーの props の facility_id も複製先に合わせる（クリック時に存在する施設になる）
        fl = d["layer"] == "facilities"
        d.loc[fl, "props"] = pd.Series(
            [{**p, "facility_id": f"K{k}-{p['facility_id']}"} for p in d.loc[fl, "props"]], index=d.index[fl]
        )
        d_parts.append(d)
        f = fac.copy()
        f["facility_id"] = f"K{k}-" + f["facility_id"]
        f["lon"] = f["lon"] + dx
        f["lat"] = f["lat"] + dy
        f["geom"] = _shift_geom(f["geom"], dx, dy)
        f["hull"] = _shift_geom(f["hull"], dx, dy)
        f_parts.append(f)
    store.replace_table("display_feature", pd.concat(d_parts, ignore_index=True))
    store.replace_table("facility", pd.concat(f_parts, ignore_index=True))
    out = store.df("SELECT layer, count(*) AS n FROM display_feature GROUP BY 1 ORDER BY 1")
    return {
        **dict(zip(out["layer"], out["n"].astype(int), strict=True)),
        "facility": int(np.int64(len(fac) * copies)),
    }


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    copies = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    base = load_config()
    d = base.root / "data" / f"stress_{port}"
    if d.exists():
        shutil.rmtree(d)
    cfg = base.with_overrides(
        {
            "settings.paths.processed_db": str(d / "roadmri.duckdb"),
            "settings.paths.interim_dir": str(d / "interim"),
            "settings.paths.tiles_dir": str(d / "tiles"),
            "settings.paths.exports_dir": str(d / "exports"),
            "settings.paths.logs_dir": str(d / "logs"),
            "dem_sources.offline": True,
        }
    )
    store = Store(cfg.path("processed_db"))
    run_pipeline(cfg, "tochigi_small", store)
    t0 = time.time()
    counts = replicate(store, copies)
    # 公開済みの FlatGeobuf は複製前のものなので消す（API が DB から作り直す）
    shutil.rmtree(cfg.path("tiles_dir"), ignore_errors=True)
    print(f"replicated in {time.time() - t0:.1f}s: {counts}", flush=True)
    uvicorn.run(create_app(cfg, store), host="127.0.0.1", port=port, log_level="warning")
