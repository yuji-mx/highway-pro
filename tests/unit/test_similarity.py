"""類似度（自分自身との類似度＝1、特徴量の単調性）。"""

from __future__ import annotations

import pandas as pd

from roadmri.analysis.similarity import (
    _fac_frame,
    _sigma,
    compare_facilities,
    facility_similarity_row,
    similar_facilities,
    similar_ramps,
)


def test_self_similarity_is_one(synthetic_db, cfg):
    _, st, _ = synthetic_db
    df = _fac_frame(st)
    row = df.iloc[0]
    s = facility_similarity_row(row, row, _sigma(df), cfg.similarity)
    assert s["similarity"] == 1.0
    assert all(v is None or v == 1.0 for v in s["features"].values())


def test_monotonic_in_feature_distance(synthetic_db, cfg):
    _, st, _ = synthetic_db
    df = _fac_frame(st)
    base = df.iloc[1].copy()
    sims = []
    for delta in (0, 1, 2, 4, 8):
        other = base.copy()
        other["ramp_count"] = base["ramp_count"] + delta
        sims.append(facility_similarity_row(base, other, _sigma(df), cfg.similarity)["similarity"])
    assert sims == sorted(sims, reverse=True) and sims[0] > sims[-1]


def test_similar_facilities_and_sapa_isolation(synthetic_db, cfg):
    _, st, _ = synthetic_db
    fid = st.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成クローバー|IC'")
    res = similar_facilities(st, fid, cfg.similarity)
    assert res["results"] and all(r["facility_id"] != fid for r in res["results"])
    sims = [r["similarity"] for r in res["results"]]
    assert sims == sorted(sims, reverse=True)
    assert all(r["facility_type"] not in ("SA", "PA") for r in res["results"])
    sa = st.scalar("SELECT facility_id FROM facility WHERE facility_type='SA'")
    assert similar_facilities(st, sa, cfg.similarity)["results"] == []


def test_compare_facilities_table(synthetic_db, cfg):
    _, st, _ = synthetic_db
    a = st.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成ダイヤモンド|IC'")
    b = st.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成スマート|SIC'") or st.scalar(
        "SELECT facility_id FROM facility WHERE normalized_name='合成|SIC'"
    )
    res = compare_facilities(st, a, b, cfg.similarity)
    rows = {r["key"]: r for r in res["table"]}
    assert rows["facility_type"]["differs"] and not rows["ramp_count"]["differs"]
    assert len(res["squares"]) == 7


def test_similar_ramps(synthetic_db, cfg):
    _, st, _ = synthetic_db
    rid = st.scalar("SELECT ramp_id FROM ramp WHERE is_loop LIMIT 1")
    res = similar_ramps(st, rid, cfg.similarity)
    top = res["results"][0]
    assert top["ramp_id"] != rid
    # ループランプには別のループランプが最も似る
    loops = set(pd.Series(st.df("SELECT ramp_id FROM ramp WHERE is_loop")["ramp_id"]))
    assert top["ramp_id"] in loops
    assert 0 < top["similarity"] <= 1 and "curvature_profile" in top["contributions"]
