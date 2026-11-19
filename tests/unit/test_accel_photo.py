"""航空写真による補正候補（検証専用。ADR-0020）を合成画像で確かめる。真の端点が既知の合流を描き、
日本は終点（下流から外縁をたどる）、米国は始点（実線→破線）と終点（両側の線の間の幅）を検出できること、
影・隣のランプ・画像端では候補を出さずに保留すること、左右を反転しても反対側を選ぶこと。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from roadmri.validation.accel_photo import _stations, detect, entry_side

W = 3.5
GORE_END, TAPER_START, TAPER_END = 400.0, 560.0, 630.0  # x 座標（合流ノード x=0 から下流へ）


def _path(side: str) -> np.ndarray:
    sg = 1.0 if side == "left" else -1.0
    ramp = np.array([[-300.0, sg * 40.0], [-120.0, sg * 12.0], [0.0, sg * 0.0]])
    # ランプの終わり（合流ノード）は本線の中心線上。合流ノードは x=0 だが、写真の始点（ゼブラの終わり）は下流
    main = np.column_stack([np.arange(5.0, 1200.0, 5.0), np.zeros(239)])
    return np.vstack([ramp, main])


def _render(
    side: str,
    shadow: tuple[float, float] | None = None,
    adjacent: bool = False,
    next_merge: bool = False,
    rng_seed: int = 7,
):
    """x, y（m）で明るさを返す合成の航空写真。y の正が左。"""
    sg = 1.0 if side == "left" else -1.0
    rng = np.random.default_rng(rng_seed)

    def outer(x: np.ndarray) -> np.ndarray:
        w = np.where((x >= 0) & (x <= TAPER_START), W, 0.0)
        if next_merge:  # 下流で次の流入の車線が現れる（別の合流線）
            w = w + np.where((x >= 800) & (x <= 950), W, 0.0)
        t = (x > TAPER_START) & (x < TAPER_END)
        return W + w + np.where(t, W * (TAPER_END - x) / (TAPER_END - TAPER_START), 0.0)

    def f(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        yy = sg * y
        g = np.full(x.shape, 60.0)  # 路肩の外（草地）
        road = (np.abs(y) <= W + 0.6) | ((yy >= 0) & (yy <= outer(x) + 0.6))
        g[road] = 95.0

        def line(c: np.ndarray | float, on: np.ndarray) -> None:
            sel = on & (np.abs(yy - c) <= 0.15)
            g[sel] = 225.0

        line(-W, np.ones(x.shape, bool))  # 反対側の外縁
        line(0.0, (np.mod(x, 20) < 8))  # 車線境界（破線）
        # 本線と加速車線の境：ゼブラの終わりまでは実線、加速車線では破線、テーパー後は外縁（実線）
        dashed = ((x >= GORE_END) & (x < TAPER_END)) | (next_merge & (x >= 800) & (x <= 950))
        line(W, ~dashed | (np.mod(x - GORE_END, 12) < 3))
        # 加速車線の外縁（テーパーで本線の外縁に寄る）
        line(outer(x), ((x >= 0) & (x < TAPER_END)) | (next_merge & (x >= 800) & (x <= 950)))
        if adjacent:  # 外側に並ぶ別のランプの線
            line(outer(x) + 4.0, (x > 300) & (x < 900))
        if shadow:
            s0, s1 = shadow
            g[(x >= s0) & (x <= s1) & (yy > 0)] = 25.0
        g += rng.normal(0, 4, x.shape)
        return g, np.ones(x.shape, bool)

    return f


def _sampler(render):
    def sample(lon: np.ndarray, lat: np.ndarray):
        return render(lon, lat)

    return sample


def _ident(x: np.ndarray, y: np.ndarray):
    return x, y


@pytest.fixture(scope="module")
def pcfg(cfg) -> dict[str, Any]:
    return cfg.settings["validation"]["accel_photo"]


def _merge_s(path: np.ndarray) -> float:
    return float(_stations(path)[2])  # ランプの最後の点＝合流ノード


def _x_to_s(path: np.ndarray, x: float) -> float:
    return _merge_s(path) + x


@pytest.mark.parametrize("side", ["left", "right"])
def test_entry_side_from_geometry_not_country(side, pcfg):
    path = _path(side)
    got, how = entry_side(path, _merge_s(path), "JP" if side == "right" else "US", pcfg)
    assert got == side and how == "geometry"  # 国の既定と逆でも幾何で決める


def test_entry_side_falls_back_to_country(pcfg):
    path = np.column_stack([np.arange(0.0, 800.0, 5.0), np.zeros(160)])
    assert entry_side(path, 300.0, "JP", pcfg) == ("left", "country")
    assert entry_side(path, 300.0, "US", pcfg) == ("right", "country")


def test_jp_end_traces_outer_edge_from_downstream(pcfg):
    path = _path("left")
    ms = _merge_s(path)
    base = [_x_to_s(path, GORE_END), ms + 5.0]  # 現行：始点は正しいが終点が合流ノード付近（過小）
    c = detect(path, _ident, _sampler(_render("left")), "J1", "JP", ms, base, pcfg)
    assert c.method == "jp_edge" and c.side == "left"
    assert c.adjusted is not None
    assert c.adjusted[0] == base[0]  # 日本の始点は現行値
    assert abs(c.adjusted[1] - _x_to_s(path, TAPER_END)) <= 15.0
    d = c.as_dict()
    assert d["adopted"] is False and d["held"] is None


@pytest.mark.parametrize("side", ["right", "left"])
def test_us_start_dash_and_end_width(side, pcfg):
    """米国：始点＝境の線が実線から破線へ、終点＝両側の線の間の幅が消える点。左右反転でも同じ測点。"""
    path = _path(side)
    ms = _merge_s(path)
    c = detect(path, _ident, _sampler(_render(side)), "U1", "US", ms, [ms, ms + 60.0], pcfg)
    assert c.side == side and c.adjusted is not None, c.as_dict()
    assert abs(c.adjusted[0] - _x_to_s(path, GORE_END)) <= 15.0
    assert abs(c.adjusted[1] - _x_to_s(path, TAPER_END)) <= 15.0


def test_shadow_over_end_is_held_not_guessed(pcfg):
    path = _path("left")
    ms = _merge_s(path)
    r = _render("left", shadow=(TAPER_START - 30, TAPER_END + 40))
    c = detect(path, _ident, _sampler(r), "J2", "JP", ms, [_x_to_s(path, GORE_END), ms + 5.0], pcfg)
    assert c.adjusted is None and c.held.get("end") in {"shadow", "lost", "ambiguous"}


def test_adjacent_ramp_line_is_held(pcfg):
    path = _path("left")
    ms = _merge_s(path)
    c = detect(
        path, _ident, _sampler(_render("left", adjacent=True)), "J3", "JP", ms, [ms + 400, ms + 5], pcfg
    )
    assert c.adjusted is None and c.held.get("end") == "adjacent_ramp"


def test_end_at_image_edge_asks_for_extension(pcfg):
    path = _path("left")
    short = path[_stations(path) <= _x_to_s(path, TAPER_END) + 5.0]  # 終点の直後で道筋が切れる
    ms = _merge_s(path)
    c = detect(short, _ident, _sampler(_render("left")), "J4", "JP", ms, [ms + 400, ms + 5], pcfg)
    assert c.adjusted is None and c.held.get("end") == "out_of_image"
    assert c.end.detail.get("extend") is True


def test_next_merge_downstream_is_ambiguous_not_overshoot(pcfg):
    """P01 の種類：下流の別の合流の車線を終点として拾わない（同程度の候補が離れて複数なら保留）。"""
    path = _path("left")
    ms = _merge_s(path)
    r = _render("left", next_merge=True)
    c = detect(
        path,
        _ident,
        _sampler(r),
        "J5",
        "JP",
        ms,
        [_x_to_s(path, GORE_END), ms + 5.0],
        pcfg,
        downstream_m=1150,
    )
    # 外縁は連続する線だけをたどるので、急に現れる下流の車線には乗り移らない。拾うなら真の終点、迷えば保留
    if c.adjusted is None:
        assert c.held.get("end") in {"ambiguous", "adjacent_ramp"}
    else:
        assert abs(c.adjusted[1] - _x_to_s(path, TAPER_END)) <= 15.0


def test_lane_continuing_past_image_is_not_called_lane_addition(pcfg):
    """終点を見失っただけ（画像の範囲まで車線が続く）は、車線追加とせず画像端として保留。"""
    path = _path("left")
    stop = _x_to_s(path, TAPER_START - 100)
    short = path[_stations(path) <= stop]
    ms = _merge_s(path)
    c = detect(
        short, _ident, _sampler(_render("left")), "J6", "JP", ms, [ms + 400, ms + 5], pcfg, main_half_m=W
    )
    assert c.adjusted is None and c.held.get("end") == "out_of_image"
