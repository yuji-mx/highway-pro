from __future__ import annotations

import numpy as np
import pytest

from roadmri.elevation.grade import analyze_grade, hysteresis_gain, window_grade
from roadmri.elevation.smoothing import METHODS, smooth_elevation
from roadmri.elevation.structures import classify_structure, correct_structures, is_low_confidence
from roadmri.ingest.gsi_tiles import decode_rgb, encode_elevation


def test_constant_grade_with_noise(analysis_cfg):
    """一定勾配 5 %（ノイズ ±0.5 m）→ g_100 最大の誤差 ≤ 0.3 ポイント（本線の既定パラメータ、内部間隔 5 m）。"""
    rng = np.random.default_rng(0)
    step = float(analysis_cfg["elevation"]["internal_step_m"]["mainline"])
    for _ in range(5):
        s = np.arange(0, 3000.0 + 1e-9, step)
        z = 100 + 0.05 * s + rng.uniform(-0.5, 0.5, len(s))
        zs = smooth_elevation(z, step, "mainline", analysis_cfg)
        g = analyze_grade(s, zs, analysis_cfg)
        assert g.metrics["grade_up_max_w100"] == pytest.approx(5.0, abs=0.3)
        assert g.metrics["grade_down_max_w100"] == pytest.approx(0.0, abs=0.01)
        assert g.metrics["grade_mean"] == pytest.approx(5.0, abs=0.1)


def test_window_truncated(analysis_cfg):
    s = np.arange(0, 60.0 + 1e-9, 5.0)
    z = 0.04 * s
    g = analyze_grade(s, z, analysis_cfg)
    assert g.truncated[100] is True
    assert g.metrics["window_truncated"] is True
    assert g.metrics["grade_up_max_w100"] == pytest.approx(4.0)
    gw, t = window_grade(s, z, 50.0)
    assert t is False and np.isnan(gw[0]) and gw[6] == pytest.approx(4.0)


def test_grade_sign_follows_direction(analysis_cfg):
    s = np.arange(0, 500.0 + 1e-9, 5.0)
    up = analyze_grade(s, 0.03 * s, analysis_cfg).metrics
    down = analyze_grade(s, 0.03 * s[::-1], analysis_cfg).metrics
    assert up["grade_up_max_w100"] == pytest.approx(3.0)
    assert down["grade_down_max_w100"] == pytest.approx(3.0)
    assert up["cont_up_len_m"] == pytest.approx(400.0)  # 窓の半分ずつは計算対象外


def _amp_ratio(method: str, wavelength: float, cfg) -> float:
    step = 5.0
    s = np.arange(0, 4000.0, step)
    z = np.sin(2 * np.pi * s / wavelength)
    zs = smooth_elevation(z, step, "ramp", cfg, method=method)
    mid = slice(len(s) // 4, 3 * len(s) // 4)
    return float(np.std(zs[mid]) / np.std(z[mid]))


@pytest.mark.parametrize("method", [m for m in METHODS if m != "none"])
def test_smoothing_sine_response(analysis_cfg, method):
    """正弦波：長波長（1 km）は振幅を保ち、短波長（12 m）は減衰する（外れ値除去は振幅を変えない）。"""
    long_ratio = _amp_ratio(method, 1000.0, analysis_cfg)
    assert 0.97 <= long_ratio <= 1.01
    short_ratio = _amp_ratio(method, 12.0, analysis_cfg)
    if method == "outlier":
        assert short_ratio == pytest.approx(1.0, abs=0.02)
    else:
        assert short_ratio < 0.6, short_ratio


def test_outlier_removal_replaces_spike(analysis_cfg):
    s = np.arange(0, 300.0, 5.0)
    z = 0.02 * s
    z[30] += 15.0
    zs = smooth_elevation(z, 5.0, "ramp", analysis_cfg, method="outlier")
    assert abs(zs[30] - 0.02 * s[30]) < 0.5


def test_structure_correction_bridge_dip(analysis_cfg):
    """橋梁で地表が −7 m 窪む → 補正後の勾配が真値 ±0.3 ポイント。"""
    s = np.arange(0, 1000.0 + 1e-9, 5.0)
    true = 50 + 0.02 * s
    ground = true.copy()
    bridge = (s >= 400) & (s <= 600)
    ground[bridge] -= 7.0 * np.sin(np.pi * (s[bridge] - 400) / 200)
    for method in ("hermite", "linear"):
        zc = correct_structures(s, ground, bridge, analysis_cfg, method=method)
        g = analyze_grade(s, zc, analysis_cfg).metrics
        assert g["grade_up_max_w100"] == pytest.approx(2.0, abs=0.3)
        assert g["grade_down_max_w100"] == pytest.approx(0.0, abs=0.3)


def test_structure_classification():
    assert classify_structure({"bridge": "yes"}) == "bridge"
    assert classify_structure({"bridge": "viaduct"}) == "viaduct"
    assert classify_structure({"tunnel": "yes"}) == "tunnel"
    assert classify_structure({"covered": "yes"}) == "tunnel"
    assert classify_structure({"cutting": "yes"}) == "cutting"
    assert classify_structure({}) == "none"
    assert is_low_confidence("none", 1) and not is_low_confidence("cutting", 0)


def test_hysteresis_gain():
    stairs = np.repeat(np.arange(0, 10.0), 5)  # 階段状 9 m 上昇
    assert hysteresis_gain(stairs, 0.3) == pytest.approx((9.0, 0.0))
    t = np.arange(0, 200)
    noisy = 0.05 * t + 0.1 * np.sin(t * 1.7)  # 振幅 0.1 m の振動
    gain, loss = hysteresis_gain(noisy, 0.3)
    assert abs((gain - loss) - (noisy[-1] - noisy[0])) < 0.3
    assert loss < 0.3
    flat = 0.2 * np.sin(np.arange(100) * 0.9)
    assert hysteresis_gain(flat, 0.3) == (0.0, 0.0)


def test_gsi_png_decode_exact():
    rgb = np.array(
        [[[0, 0, 100], [0, 1, 0], [128, 0, 0], [255, 255, 255], [0x01, 0x86, 0xA0]]], dtype=np.uint8
    )
    h = decode_rgb(rgb)
    assert h[0, 0] == pytest.approx(1.00)
    assert h[0, 1] == pytest.approx(2.56)
    assert np.isnan(h[0, 2])
    assert h[0, 3] == pytest.approx(-0.01)
    assert h[0, 4] == pytest.approx(1000.00)
    vals = np.array([[0.0, 123.45, -12.34, 3776.24, np.nan]])
    back = decode_rgb(encode_elevation(vals))
    assert np.allclose(back[~np.isnan(vals)], vals[~np.isnan(vals)], atol=1e-9)
    assert np.isnan(back[0, 4])
