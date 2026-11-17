"""加速車線長（ADR-0016）：典型的な合流の形で、始点・終点・除外理由を確かめる。

座標は投影座標（m）。本線は x=0 を北向き（y が増える向き）に走り、ランプは西側（x<0）から合流する。
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np
import pytest

from roadmri.network.accel_lane import REASONS, measure

CFG: dict[str, Any] = {
    "max_search_m": 1500,
    "default_mainline_lanes": 2,
    "adjacency_tolerance_m": 1.5,
    "sample_step_m": 5,
    "min_length_m": 30,
    "sensitivity_m": 1.0,
    "low_confidence_spread_m": 50,
}
W = 3.5  # 車線幅。本線 2 車線・ランプ 1 車線の「すぐ隣」は 3.5 + 1.75 + 1.5 = 6.75 m


def _graph(
    main: list[tuple[float, str | None, dict[str, str]]], links: list[tuple[float, str]] = ()
) -> nx.MultiDiGraph:
    """main: (y, この点から次の点までの lanes, 追加タグ)。links: (y, "in" | "out") の連絡路（他の合流・分岐）。"""
    g = nx.MultiDiGraph()
    for i, (y, _, _) in enumerate(main):
        g.add_node(i, x=0.0, y=y)
    for i in range(len(main) - 1):
        y0, lanes, extra = main[i]
        y1 = main[i + 1][0]
        tags = {**({"lanes": lanes} if lanes else {}), **extra}
        xy = np.array([[0.0, y0], [0.0, y1]])
        g.add_edge(i, i + 1, key=f"m{i}", cls="mainline", route_ref="E1", tags=tags, xy=xy, length_m=y1 - y0)
    for j, (y, kind) in enumerate(links):
        n = next(i for i, (yy, _, _) in enumerate(main) if yy == y)
        other = 1000 + j
        g.add_node(other, x=200.0, y=y)
        e = (n, other) if kind == "out" else (other, n)
        g.add_edge(*e, key=f"l{j}", cls="link", route_ref=None, tags={}, xy=np.zeros((2, 2)), length_m=200.0)
    return g


def _merge_node(g: nx.MultiDiGraph, y: float) -> int:
    return next(n for n, d in g.nodes(data=True) if d["x"] == 0.0 and d["y"] == y)


def _ramp(*pts: tuple[float, float]) -> np.ndarray:
    return np.array(pts, dtype=float)


def test_parallel_style_start_is_end_of_separation_and_end_is_merge_node() -> None:
    """並走型（日本の OSM に多い）：ランプが本線の脇を 300 m 並走し、テーパーの終わりで接続する。"""
    g = _graph([(-2000.0, "2", {}), (0.0, "2", {}), (2000.0, None, {})])
    ramp = _ramp((-150, -800), (-6, -300), (-6, -50), (0, 0))  # 隔て部分は y=-300 付近で終わる
    r = measure(g, ramp, _merge_node(g, 0.0), 1, CFG, W)
    assert r.status == "measured" and r.method == "geometry" and r.confidence == "high"
    assert r.added_lane_m == 0 and r.length_m == pytest.approx(302, abs=5)
    assert r.start_xy == pytest.approx((-6.4, -301.6), abs=2) and r.end_xy == (0.0, 0.0)


def test_lane_count_style_end_is_where_lanes_return() -> None:
    """車線増減型（米国の OSM に多い）：隔て部分の終わりで接続し、本線が 250 m だけ 3 車線になる。"""
    g = _graph(
        [
            (-2000.0, "2", {}),
            (0.0, "3", {"turn:lanes": "||merge_to_left"}),
            (250.0, "2", {}),
            (2000.0, None, {}),
        ]
    )
    ramp = _ramp((-200, -400), (0, 0))  # 斜めに接続（並走部はほぼない）
    r = measure(g, ramp, _merge_node(g, 0.0), 1, CFG, W)
    assert r.status == "measured" and r.confidence == "high"
    assert r.added_lane_m == pytest.approx(250) and r.end_xy == (0.0, 250.0)
    assert r.parallel_m < 20 and r.length_m == pytest.approx(250 + r.parallel_m)


def test_lane_count_style_without_merge_tag_is_medium_confidence() -> None:
    g = _graph([(-2000.0, "2", {}), (0.0, "3", {}), (250.0, "2", {}), (2000.0, None, {})])
    r = measure(g, _ramp((-200, -400), (0, 0)), _merge_node(g, 0.0), 1, CFG, W)
    assert r.status == "measured" and r.confidence == "medium" and r.notes


@pytest.mark.parametrize(
    ("main", "links", "reason"),
    [
        # 合流車線がそのまま本線の車線になる（1,500 m 以内に車線が戻らない）
        ([(-2000.0, "2", {}), (0.0, "3", {}), (2000.0, None, {})], [], "lane_addition"),
        # 車線が戻る前に本線のデータが途切れる（範囲の端）：車線追加とは言えないので別の理由
        ([(-2000.0, "2", {}), (0.0, "3", {}), (800.0, None, {})], [], "mainline_ends"),
        # 車線が戻る前に出口へ分岐する（付加車線・織り込み）
        ([(-2000.0, "2", {}), (0.0, "3", {}), (300.0, "3", {}), (600.0, "2", {}), (2000.0, None, {})],
         [(300.0, "out")], "auxiliary_to_exit"),
        # 車線が戻る点がちょうど出口の分岐点
        ([(-2000.0, "2", {}), (0.0, "3", {}), (300.0, "2", {}), (2000.0, None, {})], [(300.0, "out")],
         "auxiliary_to_exit"),
        # 車線が戻る前に別の合流がある
        ([(-2000.0, "2", {}), (0.0, "3", {}), (300.0, "4", {}), (600.0, "2", {}), (2000.0, None, {})],
         [(300.0, "in")], "another_merge"),
        # 合流点で本線の車線が減っている
        ([(-2000.0, "3", {}), (0.0, "2", {}), (2000.0, None, {})], [], "lanes_inconsistent"),
        # 車線が増えず、ランプも並走しない（隔て部分の終わりが特定できない）
        ([(-2000.0, "2", {}), (0.0, "2", {}), (2000.0, None, {})], [], "no_parallel_section"),
    ],
)  # fmt: skip
def test_excluded_cases(main: list[Any], links: list[Any], reason: str) -> None:
    g = _graph(main, links)
    r = measure(g, _ramp((-200, -400), (0, 0)), _merge_node(g, 0.0), 1, CFG, W)
    assert (r.status, r.reason) == ("excluded", reason)
    assert r.metrics()["accel_lane_len_m"] is None and reason in REASONS


def test_no_mainline_upstream() -> None:
    g = _graph([(0.0, "2", {}), (2000.0, None, {})])  # 合流ノードが本線の始点
    r = measure(g, _ramp((-200, -400), (0, 0)), _merge_node(g, 0.0), 1, CFG, W)
    assert (r.status, r.reason) == ("excluded", "no_mainline")


def test_shallow_convergence_is_low_confidence() -> None:
    """ランプが本線へ非常に浅い角度で寄ってくる：許容差を 1 m 広げると始点が 60 m 動くので信頼度「低」。"""
    g = _graph([(-2000.0, "2", {}), (0.0, "2", {}), (2000.0, None, {})])
    r = measure(g, _ramp((-10, -600), (0, 0)), _merge_node(g, 0.0), 1, CFG, W)
    assert r.status == "measured" and r.confidence == "low"
    assert r.length_m == pytest.approx(600 * 6.75 / 10, abs=5)


def test_wider_mainline_and_two_lane_ramp_widen_the_adjacency() -> None:
    """本線 3 車線・2 車線ランプ：すぐ隣の距離は 5.25 + 3.5 + 1.5 = 10.25 m。9 m 離れて並走しても始点になる。"""
    g = _graph([(-2000.0, "3", {}), (0.0, "3", {}), (2000.0, None, {})])
    ramp = _ramp((-150, -800), (-9, -300), (-9, -50), (0, 0))
    assert measure(g, ramp, _merge_node(g, 0.0), 2, CFG, W).length_m == pytest.approx(302, abs=5)
    # 同じ形でも本線 2 車線・1 車線ランプでは 9 m は「隔てあり」
    g2 = _graph([(-2000.0, "2", {}), (0.0, "2", {}), (2000.0, None, {})])
    r = measure(g2, ramp, _merge_node(g2, 0.0), 1, CFG, W)
    assert r.length_m is None or r.length_m < 60
