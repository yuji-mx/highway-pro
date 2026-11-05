from __future__ import annotations

import numpy as np
import pytest

from roadmri.geometry.curvature import analyze_curvature, three_point_curvature
from tests.geom_helpers import Seg, arc_road, integrate, osm_like


@pytest.mark.parametrize(
    ("radius", "tol", "kind", "spacing"),
    [(50, 0.05, "ramp", 5), (100, 0.05, "ramp", 8), (300, 0.05, "ramp", 15), (1000, 0.10, "mainline", 25)],
)
def test_min_radius_synthetic_arc_with_noise(analysis_cfg, radius, tol, kind, spacing):
    """§14.1 合成円弧（ノイズ ±0.3 m、OSM 風の不等間隔点列）。"""
    rng = np.random.default_rng(radius)
    dense = arc_road(radius)
    errs = []
    for _ in range(10):
        pts = osm_like(dense, spacing, 0.3, rng)
        r = analyze_curvature(pts, kind, analysis_cfg).metrics["min_radius_m"]
        errs.append(abs(r - radius) / radius)
    assert max(errs) <= tol, errs


def test_three_point_exact_circle():
    t = np.linspace(0, np.pi, 400)
    xy = np.column_stack([100 * np.cos(t), 100 * np.sin(t)])  # 反時計回り = 左カーブ
    k = three_point_curvature(xy, 5)
    assert np.nanmean(k) == pytest.approx(0.01, rel=1e-6)
    assert np.isnan(k[:5]).all() and np.isnan(k[-5:]).all()


@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_curvature_sign_left_positive(analysis_cfg, sign):
    xy = arc_road(80, sign=sign)
    res = analyze_curvature(xy, "ramp", analysis_cfg)
    kmid = res.kappa[np.nanargmax(np.abs(np.nan_to_num(res.kappa)))]
    assert np.sign(kmid) == sign
    assert res.curves and res.curves[0].side == ("left" if sign > 0 else "right")


def test_s_curve_signs(analysis_cfg):
    segs: list[Seg] = [
        (100, lambda u: 0.0),
        (120, lambda u: 1 / 100),
        (60, lambda u: 0.0),
        (120, lambda u: -1 / 100),
        (100, lambda u: 0.0),
    ]
    res = analyze_curvature(integrate(segs), "ramp", analysis_cfg)
    sides = [c.side for c in res.curves]
    assert sides == ["left", "right"]
    assert res.metrics["min_radius_m"] == pytest.approx(100, rel=0.05)


def test_clothoid_curvature_rate(analysis_cfg):
    """合成クロソイド：曲率変化率の誤差 ≤ 10 %。"""
    length, r_end = 150.0, 100.0
    rate = 1 / (r_end * length)
    segs: list[Seg] = [(100, lambda u: 0.0), (length, lambda u: rate * u), (100, lambda u: 1 / r_end)]
    rng = np.random.default_rng(3)
    pts = osm_like(integrate(segs), 5, 0.05, rng)
    res = analyze_curvature(pts, "ramp", analysis_cfg)
    assert res.metrics["curvature_rate_max"] == pytest.approx(rate, rel=0.10)


def test_loop_turn(analysis_cfg):
    xy = arc_road(40, angle=np.radians(270), straight=50)
    res = analyze_curvature(xy, "ramp", analysis_cfg)
    assert res.metrics["turn_deg"] >= 200


def test_straight_line_has_no_curve(analysis_cfg):
    xy = np.column_stack([np.linspace(0, 500, 30), np.zeros(30)])
    res = analyze_curvature(xy, "mainline", analysis_cfg)
    assert res.metrics["curve_count"] == 0
    assert res.metrics["min_radius_m"] is None or res.metrics["min_radius_m"] > 1e5


def test_short_element(analysis_cfg):
    res = analyze_curvature(np.array([[0.0, 0.0], [8.0, 0.0]]), "ramp", analysis_cfg)
    assert res.metrics["length_m"] == pytest.approx(8.0)
