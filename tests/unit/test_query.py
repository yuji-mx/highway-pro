"""QueryDSL（全演算子、順序付きカテゴリ、ネスト、不正フィールド）。"""

from __future__ import annotations

import pytest

from roadmri.analysis.query import QueryError, compile_query, to_text, warnings
from roadmri.analysis.search import run_query


def q(where, target="ramp", **kw):
    return {"target": target, "where": where, **kw}


def ids(store, query, key="ramp_id"):
    return set(run_query(store, query)[key])


def test_all_operators(synthetic_db):
    _, st, _ = synthetic_db
    all_r = ids(st, q({}, limit=10000))
    entry = ids(st, q({"all": [{"field": "flow", "op": "eq", "value": "entry"}]}))
    not_entry = ids(st, q({"all": [{"field": "flow", "op": "ne", "value": "entry"}]}))
    assert entry and entry | not_entry == all_r and not entry & not_entry
    assert ids(st, q({"all": [{"field": "flow", "op": "in", "value": ["entry", "exit"]}]})) >= entry
    long_ = ids(st, q({"all": [{"field": "length_m", "op": "gte", "value": 700}]}))
    short = ids(st, q({"all": [{"field": "length_m", "op": "lt", "value": 700}]}))
    assert long_ | short == all_r
    gt = ids(st, q({"all": [{"field": "length_m", "op": "gt", "value": 700}]}))
    lte = ids(st, q({"all": [{"field": "length_m", "op": "lte", "value": 700}]}))
    assert gt | lte == all_r
    btw = ids(st, q({"all": [{"field": "length_m", "op": "between", "value": [400, 600]}]}))
    df = run_query(st, q({"all": [{"field": "length_m", "op": "between", "value": [400, 600]}]}))
    assert btw and df["length_m"].between(400, 600).all()
    has_accel = ids(st, q({"all": [{"field": "accel_lane_len_m", "op": "exists", "value": True}]}))
    no_accel = ids(st, q({"all": [{"field": "accel_lane_len_m", "op": "exists", "value": False}]}))
    assert has_accel | no_accel == all_r
    fac = run_query(
        st, q({"all": [{"field": "name", "op": "contains", "value": "クローバーIC"}]}, target="facility")
    )
    assert list(fac["name"]) == ["合成クローバーIC"]
    rt = run_query(
        st, q({"all": [{"field": "route_ref", "op": "contains", "value": "SYN2"}]}, target="facility")
    )
    assert set(rt["name"]) == {"合成タービンJCT"}


def test_ordinal_quality(synthetic_db):
    _, st, _ = synthetic_db
    med = run_query(st, q({"all": [{"field": "dem_quality", "op": "gte", "value": "medium"}]}))
    assert set(med["dem_quality"]) <= {"medium", "high"}
    low = run_query(st, q({"all": [{"field": "dem_quality", "op": "lte", "value": "low"}]}))
    assert set(low["dem_quality"]) <= {"low", "unusable"}
    assert len(med) + len(low) == len(run_query(st, q({}, limit=10000)))


def test_nested_and_or(synthetic_db):
    _, st, _ = synthetic_db
    w = {
        "all": [{"field": "flow", "op": "eq", "value": "entry"}],
        "any": [
            {"field": "length_m", "op": "gte", "value": 800},
            {"all": [{"field": "is_loop", "op": "eq", "value": True}]},
        ],
    }
    df = run_query(st, q(w))
    assert len(df) and (df["flow"] == "entry").all()
    assert ((df["length_m"] >= 800) | df["is_loop"]).all()


def test_invalid_field_and_op():
    with pytest.raises(QueryError):
        compile_query(q({"all": [{"field": "length_m; DROP TABLE ramp", "op": "eq", "value": 1}]}))
    with pytest.raises(QueryError):
        compile_query(q({"all": [{"field": "length_m", "op": "like", "value": 1}]}))
    with pytest.raises(QueryError):
        compile_query(q({"all": [{"field": "length_m", "op": "gte", "value": "abc"}]}))
    with pytest.raises(QueryError):
        compile_query(q({}, target="building"))
    with pytest.raises(QueryError):
        compile_query(q({"all": [{"field": "dem_quality", "op": "gte", "value": "excellent"}]}))


def test_values_are_bound_not_interpolated():
    sql, params = compile_query(q({"all": [{"field": "flow", "op": "eq", "value": "x' OR '1'='1"}]}))
    assert "x' OR" not in sql and params[0] == "x' OR '1'='1"


def test_to_text_spec_example(cfg):
    from roadmri.analysis.presets import get_preset

    text = to_text(get_preset(cfg, "spec_example")["query"])
    assert text.startswith("ランプ：区分 ＝ 流入 かつ 長さ ≥ 500 m かつ 最小R ≤ 100 m かつ 100m上り ≥ 5%")
    assert "DEM品質 中以上" in text and "難易度候補値の降順" in text


def test_instant_grade_warning():
    assert warnings(q({"all": [{"field": "grade_inst_max", "op": "gte", "value": 8}]}))
    assert not warnings(q({"all": [{"field": "grade_up_max_w100", "op": "gte", "value": 8}]}))


def test_range_in_query(synthetic_db):
    _, st, _ = synthetic_db
    df = run_query(st, q({}, range={"type": "route", "route_refs": ["SYN2"]}))
    assert set(df["facility_name"]) == {"合成タービンJCT"}
