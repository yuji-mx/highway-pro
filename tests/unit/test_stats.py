"""統計（scipy・既知値と照合、hypothesis で乱数入力）、ブートストラップの再現性、範囲の重複処理。"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as hs
from scipy import stats as sps

from roadmri.analysis import stats as st
from roadmri.analysis.compare import compare_ranges

floats = hs.lists(
    hs.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False), min_size=2, max_size=40
)


@settings(max_examples=60, deadline=None)
@given(floats, floats)
def test_cliffs_delta_matches_bruteforce(a, b):
    x, y = np.array(a), np.array(b)
    brute = (np.sum(y[None, :] > x[:, None]) - np.sum(y[None, :] < x[:, None])) / (len(x) * len(y))
    assert abs(st.cliffs_delta(x, y) - brute) < 1e-9


@settings(max_examples=60, deadline=None)
@given(floats, floats)
def test_mann_whitney_matches_scipy(a, b):
    p, test = st.mann_whitney(a, b, 20)
    x, y = np.array(a), np.array(b)
    if np.all(np.concatenate([x, y]) == x[0]):
        return
    method = "exact" if test == "mannwhitney_exact" else "asymptotic"
    ref = sps.mannwhitneyu(x, y, alternative="two-sided", method=method, use_continuity=True).pvalue
    assert p == ref  # 同じ実装を使うので完全一致


@settings(max_examples=60, deadline=None)
@given(hs.lists(hs.floats(min_value=0, max_value=1), min_size=1, max_size=30))
def test_bh_matches_scipy(ps):
    q = st.bh_qvalues(ps)
    ref = sps.false_discovery_control(np.array(ps), method="bh")
    assert np.allclose(q, ref, atol=1e-9)


def test_bh_with_missing():
    q = st.bh_qvalues([0.01, None, 0.04, 0.03])
    assert q[1] is None and q[0] == pytest.approx(0.03) and q[2] == pytest.approx(0.04)


def test_wilson_known_values():
    lo, hi = st.wilson_ci(8, 20)
    assert lo == pytest.approx(0.218807, abs=1e-5) and hi == pytest.approx(0.613418, abs=1e-5)
    assert st.wilson_ci(0, 10)[0] == 0.0
    assert st.wilson_ci(0, 0) is None


@settings(max_examples=40, deadline=None)
@given(floats, floats)
def test_cohens_d(a, b):
    x, y = np.array(a), np.array(b)
    sp_ = math.sqrt(((len(x) - 1) * x.var(ddof=1) + (len(y) - 1) * y.var(ddof=1)) / (len(x) + len(y) - 2))
    d = st.cohens_d(x, y)
    if sp_ == 0:
        assert d == 0.0
    else:
        assert abs(d - (y.mean() - x.mean()) / sp_) < 1e-9


def test_proportion_tests_match_scipy():
    p, t = st.two_proportion_test(40, 100, 60, 100)
    assert t == "chi2_2x2" and p == sps.chi2_contingency([[40, 60], [60, 40]], correction=False).pvalue
    p, t = st.two_proportion_test(1, 10, 6, 10)
    assert t == "fisher_exact" and p == sps.fisher_exact([[1, 9], [6, 4]]).pvalue


def test_categorical_tests():
    a = {"x": 30, "y": 40, "z": 30}
    b = {"x": 50, "y": 20, "z": 30}
    p, t = st.categorical_test(a, b)
    assert (
        t == "chi2"
        and p == sps.chi2_contingency(np.array([[30, 40, 30], [50, 20, 30]]), correction=False).pvalue
    )
    p1, t1 = st.categorical_test({"x": 3, "y": 1}, {"x": 0, "y": 4, "z": 1}, 2000, 7)
    p2, _ = st.categorical_test({"x": 3, "y": 1}, {"x": 0, "y": 4, "z": 1}, 2000, 7)
    assert t1 == "fisher_freeman_halton_mc" and p1 == p2 and 0 < p1 <= 1
    # 2x2 では Fisher の正確検定とほぼ一致する
    mc = st.fisher_freeman_halton_mc(np.array([[3, 1], [0, 4]]), 20000, 1)
    assert mc == pytest.approx(sps.fisher_exact([[3, 1], [0, 4]]).pvalue, abs=0.02)


def test_bootstrap_seed_reproducible():
    rng = np.random.default_rng(0)
    a, b = rng.normal(0, 1, 50), rng.normal(0.5, 1, 60)
    r1 = st.bootstrap_median_diff(a, b, 2000, 42)
    r2 = st.bootstrap_median_diff(a, b, 2000, 42)
    assert r1 == r2
    assert r1[1][0] < r1[0] < r1[1][1]


def test_describe_and_poisson():
    d = st.describe([1, 2, 3, 4, 100])
    assert d["n"] == 5 and d["median"] == 3 and d["max"] == 100
    assert st.describe([])["n"] == 0
    assert st.poisson_rate_test(10, 10.0, 10, 10.0) == pytest.approx(1.0)


def test_fd_bins_capped():
    e = st.fd_bins(np.random.default_rng(0).normal(size=100000), 30)
    assert len(e) - 1 <= 30


@pytest.mark.parametrize(
    ("mode", "check"),
    [
        ("keep_warn", lambda a, b, o: a["facility"] == 5 and b["facility"] == 4 and o == 2),
        ("exclude_both", lambda a, b, o: a["facility"] == 3 and b["facility"] == 2),
        ("keep_in_a", lambda a, b, o: a["facility"] == 5 and b["facility"] == 2),
    ],
)
def test_range_overlap_modes(synthetic_db, mode, check):
    cfg, store, _ = synthetic_db
    fac = store.df("SELECT facility_id FROM facility ORDER BY facility_id")["facility_id"].tolist()
    req = {
        "range_a": {"type": "facilities", "facility_ids": fac[:5]},
        "range_b": {"type": "facilities", "facility_ids": fac[3:7]},
        "overlap": mode,
        "bootstrap": 200,
    }
    res = compare_ranges(store, cfg, req)
    assert check(res["a"]["counts"], res["b"]["counts"], res["overlap"]["facilities"])
    if mode == "keep_warn":
        assert any("重複" in n for n in res["notes_ja"])


def test_compare_ranges_structure(synthetic_db):
    cfg, store, _ = synthetic_db
    req = {
        "range_a": {"type": "bbox", "bbox": [139, 36, 140, 36.55]},
        "range_b": {"type": "bbox", "bbox": [139, 36.55, 140, 37]},
        "hit_query": {"target": "ramp", "where": {"all": [{"field": "length_m", "op": "gte", "value": 500}]}},
    }
    r1 = compare_ranges(store, cfg, req)
    r2 = compare_ranges(store, cfg, req)
    assert r1["metrics"] == r2["metrics"]  # シード固定で同値
    keys = {m["key"] for m in r1["metrics"]}
    assert {"ramp.min_radius_m", "facility.ramp_count", "section.gain_per_km"} <= keys
    assert any(s["key"] == "hit_ratio" for s in r1["summary"])
    assert any("該当率" in s for s in r1["insights_ja"])
    assert all(m["q"] is None or m["q"] >= (m["p"] or 0) - 1e-12 for m in r1["metrics"])
    ref = [m for m in r1["metrics"] if min(m["a"]["n"], m["b"]["n"]) < 10]
    assert all(m["reference_only"] for m in ref)


def test_bootstrap_order_statistic_sampler_matches_resampling():
    """大標本用の順序統計量サンプリングが、通常の復元抽出と同じ分布を与える（CI がほぼ一致）。"""
    rng = np.random.default_rng(3)
    for n in (2001, 2400):
        a, b = rng.lognormal(5, 0.5, n), rng.lognormal(5.05, 0.5, n)
        _, ci_fast = st.bootstrap_median_diff(a, b, 4000, 1, resample_max_n=10)
        _, ci_ref = st.bootstrap_median_diff(a, b, 4000, 1, resample_max_n=10**9)
        width = ci_ref[1] - ci_ref[0]
        assert abs(ci_fast[0] - ci_ref[0]) < 0.15 * width and abs(ci_fast[1] - ci_ref[1]) < 0.15 * width
