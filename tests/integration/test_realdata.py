"""実データ検証（§14.3、@pytest.mark.network。MVP 受け入れ時に実施。既定では実行しない）。

    uv run roadmri fetch --range tochigi
    uv run pytest -m network tests/integration/test_realdata.py

NEXCO 公開値との照合結果は docs/validation/tochigi.md に記録する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roadmri.config import load_config
from roadmri.pipeline import run_pipeline
from roadmri.store.duckdb_store import Store

pytestmark = pytest.mark.network
REF = Path(__file__).resolve().parents[2] / "docs" / "validation" / "tochigi_reference.json"


@pytest.fixture(scope="module")
def tochigi(tmp_path_factory):
    d = tmp_path_factory.mktemp("real")
    cfg = load_config().with_overrides(
        {
            "settings.paths.processed_db": str(d / "r.duckdb"),
            "settings.paths.interim_dir": str(d / "i"),
            "settings.paths.tiles_dir": str(d / "t"),
        }
    )
    st = Store(cfg.path("processed_db"))
    run_pipeline(cfg, "tochigi", st)
    return cfg, st


def test_facilities_extracted_without_duplicates(tochigi):
    _, st = tochigi
    ref = json.loads(REF.read_text(encoding="utf-8"))
    names = set(st.df("SELECT normalized_name FROM facility WHERE in_range")["normalized_name"].dropna())
    missing = [n for n in ref["facilities"] if n not in names]
    assert not missing, f"取りこぼし: {missing}"
    dup = st.df(
        "SELECT normalized_name, count(*) c FROM facility WHERE normalized_name IS NOT NULL GROUP BY 1 HAVING c > 1"
    )
    assert dup.empty, f"重複: {dup.to_dict('records')}"


def test_section_distance_vs_nexco(tochigi):
    _, st = tochigi
    ref = json.loads(REF.read_text(encoding="utf-8"))
    if not ref["distances_km"]:
        pytest.skip("NEXCO 公開の IC 間距離が docs/validation/tochigi_reference.json に未登録です")
    errs = []
    for r in ref["distances_km"]:
        d = st.scalar(
            "SELECT avg(s.dist_standard_m) FROM section s JOIN facility a ON s.from_facility_id=a.facility_id "
            "JOIN facility b ON s.to_facility_id=b.facility_id WHERE a.normalized_name IN (?, ?) "
            "AND b.normalized_name IN (?, ?)",
            [r["a"], r["b"], r["a"], r["b"]],
        )
        if d is not None:
            errs.append(abs(d / 1000 - r["km"]) / r["km"])
    assert errs, "照合できる区間がありません"
    assert max(errs) <= 0.03
    assert sorted(errs)[len(errs) // 2] <= 0.01


def test_mainline_steep_grades_explained(tochigi):
    _, st = tochigi
    steep = st.df(
        "SELECT low_confidence FROM sample_point WHERE element_type='mainline' AND abs(grade_w100) > 7"
    )
    assert steep["low_confidence"].all()


def test_spec_example_query_runs(tochigi):
    cfg, st = tochigi
    from roadmri.analysis.presets import get_preset
    from roadmri.analysis.search import run_query

    df = run_query(st, get_preset(cfg, "spec_example")["query"])
    assert {"ramp_id", "difficulty"} <= set(df.columns)
