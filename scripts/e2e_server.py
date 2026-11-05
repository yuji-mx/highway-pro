"""E2E 用サーバー：実データ fixture（宇都宮IC〜鹿沼IC）を data/e2e/ に build して起動する（オフライン）。

uv run python scripts/e2e_server.py [port]
"""

from __future__ import annotations

import shutil
import sys

import uvicorn

from roadmri.api.main import create_app
from roadmri.config import load_config
from roadmri.pipeline import run_pipeline
from roadmri.store.duckdb_store import Store

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    base = load_config()
    d = base.root / "data" / "e2e"
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
    uvicorn.run(create_app(cfg, store), host="127.0.0.1", port=port, log_level="warning")
