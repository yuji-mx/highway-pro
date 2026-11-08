"""結合テストのゴールデン（tests/fixtures/golden/*.json）を現在の実装から生成する。

アルゴリズムを意図して変更したときだけ実行し、差分をレビューしてからコミットすること。
    uv run python scripts/make_golden.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from roadmri.config import load_config
from roadmri.pipeline import run_pipeline
from roadmri.store.duckdb_store import Store


def golden(region: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        cfg = load_config().with_overrides(
            {
                "settings.paths.processed_db": f"{d}/g.duckdb",
                "settings.paths.interim_dir": f"{d}/i",
                "settings.paths.tiles_dir": f"{d}/t",
                "dem_sources.offline": True,
            }
        )
        st = Store(cfg.path("processed_db"))
        run_pipeline(cfg, region, st)
        fac = st.df(
            "SELECT normalized_name, facility_type, shape_class, ramp_count, is_jct_combined, is_half_ic "
            "FROM facility ORDER BY normalized_name"
        )
        sec = st.df(
            "SELECT s.route_ref, f1.normalized_name a, f2.normalized_name b, s.direction, s.dist_standard_m "
            "FROM section s JOIN facility f1 ON s.from_facility_id=f1.facility_id "
            "JOIN facility f2 ON s.to_facility_id=f2.facility_id ORDER BY 1,2,3,4"
        )
        out = {
            "counts": {
                "facilities": len(fac),
                "ramps": int(st.scalar("SELECT count(*) FROM ramp")),
                "sections": len(sec),
            },
            "facilities": fac.to_dict("records"),
            "sections": [
                {**r, "dist_standard_m": round(float(r["dist_standard_m"]), 1)}
                for r in sec.to_dict("records")
            ],
        }
        st.close()
        return out


if __name__ == "__main__":
    regions = sys.argv[1:] or ["tochigi_small", "synthetic", "us_annarbor_small"]
    for r in regions:
        p = Path("tests/fixtures/golden") / f"{r}.json"
        p.write_text(json.dumps(golden(r), ensure_ascii=False, indent=2, default=bool), encoding="utf-8")
        print("wrote", p)
