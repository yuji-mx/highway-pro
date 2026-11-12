"""性能テスト（§5.4、§14.7）。pytest-benchmark で計測し、目標値を超えないことを確認する。"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from roadmri.analysis import stats as st
from roadmri.analysis.indicators import ElementGeom, analyze_element
from roadmri.analysis.search import run_query
from roadmri.ingest.dem import DemSampler, SyntheticProvider
from roadmri.store.duckdb_store import Store

pytestmark = pytest.mark.perf


def test_single_ramp_analysis(benchmark, analysis_cfg):
    """単一ランプの解析（再計算）：5 秒以内。"""
    from roadmri.geometry.projection import Projector
    from tests.geom_helpers import arc_road, osm_like

    xy = osm_like(arc_road(80, np.radians(270), 0.3, 150), 6, 0.2, np.random.default_rng(0))
    proj = Projector.for_lonlat(139.85, 36.55)
    xy = xy + np.array(proj.to_xy(139.85, 36.55))
    sampler = DemSampler([SyntheticProvider("synthetic", {"name": "s", "resolution_m": 5}, {})])
    cfg = {**analysis_cfg, "_composite": {"small_r_m": 150}}
    res = benchmark(
        lambda: analyze_element(ElementGeom("ramp", "r", xy, [(0, 1e9, "none", 0)]), proj, sampler, cfg)
    )
    assert res.metrics["min_radius_m"] is not None
    assert benchmark.stats["max"] < 5.0


def test_facility_search(benchmark, tochigi_db):
    _, store, _ = tochigi_db
    q = {"target": "facility", "where": {"all": [{"field": "name", "op": "contains", "value": "宇都宮"}]}}
    df = benchmark(lambda: run_query(store, q))
    assert len(df) == 1 and benchmark.stats["max"] < 1.0


def test_range_stats_5000(benchmark):
    """範囲統計比較（各範囲 5,000 ランプ、ブートストラップ込み）：3 秒以内。"""
    rng = np.random.default_rng(0)
    a, b = rng.lognormal(5, 0.6, 5000), rng.lognormal(5.1, 0.6, 5000)

    def run() -> None:
        for _ in range(9):  # 既定の連続量指標 9 個ぶん
            st.describe(a), st.describe(b)
            st.mann_whitney(a, b)
            st.cliffs_delta(a, b)
            st.bootstrap_median_diff(a, b, 2000, 42)

    benchmark.pedantic(run, rounds=1, iterations=1)
    assert benchmark.stats["max"] < 3.0


def test_segment_search_100k(tmp_path):
    """10 万件規模の道路セグメント検索（DuckDB）：3 秒以内（合成データ）。"""
    s = Store(tmp_path / "p.duckdb")
    n = 100_000
    rng = np.random.default_rng(1)
    df = pd.DataFrame(
        {
            "element_type": "mainline",
            "element_id": [f"e{i // 100}" for i in range(n)],
            "seq": np.arange(n) % 100,
            "s_m": np.arange(n) * 10.0,
            "lon": 139 + rng.random(n),
            "lat": 36 + rng.random(n),
            "grade_w100": rng.normal(0, 3, n),
            "curvature": rng.normal(0, 0.002, n),
            "z_corrected": rng.normal(200, 50, n),
        }
    )
    s.replace_table("sample_point", df)
    t0 = time.time()
    r = s.df(
        "SELECT element_id, max(grade_w100) g, max(abs(curvature)) k FROM sample_point "
        "WHERE lon BETWEEN 139.2 AND 139.6 AND lat BETWEEN 36.2 AND 36.6 AND grade_w100 > 5 GROUP BY 1 ORDER BY g DESC"
    )
    assert time.time() - t0 < 3.0 and len(r) > 0
