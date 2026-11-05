"""ネットワーク抽出（重複統合、ランプ縮約、ID、種別判定、区間距離、経路異常）。"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import box

from roadmri.ingest.synthetic import Builder, synthetic_osm, to_lonlat
from roadmri.network.dedupe import Candidate, merge_candidates
from roadmri.network.ramps import inherit_ids, jaccard
from roadmri.network.sections import check_connectivity
from tests.net_helpers import build_network, diamond_ic, road, straight_mainline, to_osm


def cand(osm_id, name, key, code, x, y, exit_ref=None, route="SYN1", direction=None):
    lon, lat = to_lonlat(x, y)
    return Candidate(
        "node", osm_id, "motorway_junction", name, None, key, code, exit_ref, route, direction, lon, lat, x, y
    )


MERGE = {"IC": 2000, "JCT": 3000, "default": 2000}


def test_dedupe_up_down_pair():
    c = [
        cand(1, "宇都宮IC", "宇都宮|IC", "IC", -15, 0, direction="up"),
        cand(2, "宇都宮ＩＣ", "宇都宮|IC", "IC", 15, 800, direction="down"),
    ]
    g = merge_candidates(c, MERGE, 1000)
    assert len(g) == 1
    rules = {r["rule"] for r in g[0].reasons}
    assert {"same_normalized_name", "carriageway_pair"} <= rules


def test_dedupe_same_name_far_apart_not_merged():
    c = [cand(1, "中央IC", "中央|IC", "IC", 0, 0), cand(2, "中央IC", "中央|IC", "IC", 0, 2500)]
    assert len(merge_candidates(c, MERGE, 1000)) == 2
    # JCT は 3000 m まで統合
    c = [cand(1, "北JCT", "北|JCT", "JCT", 0, 0), cand(2, "北JCT", "北|JCT", "JCT", 0, 2500)]
    assert len(merge_candidates(c, MERGE, 1000)) == 1


def test_dedupe_unnamed_by_exit_number():
    c = [cand(1, None, None, None, -15, 0, exit_ref="7"), cand(2, None, None, None, 15, 600, exit_ref="7")]
    g = merge_candidates(c, MERGE, 1000)
    assert len(g) == 1 and g[0].reasons[0]["rule"] == "same_exit_number_same_route"
    c = [
        cand(1, None, None, None, -15, 0, exit_ref="7"),
        cand(2, None, None, None, 15, 600, exit_ref="7", route="X"),
    ]
    assert len(merge_candidates(c, MERGE, 1000)) == 2


@pytest.fixture(scope="module")
def syn(cfg):
    return build_network(cfg, synthetic_osm())


def _fac(ctx, key):
    return next(f for f in ctx.facs if f.normalized_name == key)


def test_facility_count_and_dedupe(syn):
    keys = sorted(f.normalized_name for f in syn.facs)
    assert keys == sorted(
        [
            "合成トランペット|IC",
            "合成ハーフ|IC",
            "合成ダイヤモンド|IC",
            "合成クローバー|IC",
            "合成|SA",
            "合成|SIC",
            "合成タービン|JCT",
            "合成Y|JCT",
        ]
    )
    # 上下線の出口ノードは 1 施設に統合され、元 OSM ID をすべて保持する
    assert len(_fac(syn, "合成タービン|JCT").members) == 4


@pytest.mark.parametrize(
    ("key", "ftype", "half", "combined"),
    [
        ("合成ダイヤモンド|IC", "IC", False, False),
        ("合成タービン|JCT", "JCT", False, False),
        ("合成|SIC", "SIC", False, False),
        ("合成|SA", "SA", False, False),
        ("合成ハーフ|IC", "IC", True, False),
    ],
)
def test_type_determination(syn, key, ftype, half, combined):
    f = _fac(syn, key)
    assert f.facility_type == ftype
    assert f.is_half_ic is half
    assert f.is_jct_combined is combined


def test_type_ic_jct_combined(cfg):
    """IC＋JCT 複合：本線 2 路線と一般道の両方に接続するランプ群。"""
    from roadmri.ingest.synthetic import E, W, hermite

    b = Builder()
    diamond_ic(b, 0.0, "複合IC", road="R")
    # 同じ IC から別路線（SYN9）へ JCT 連絡
    b.link(hermite((-15.0, 500.0), 1.5708, (-600.0, 1200.0), W), "NB", "NB", True, "S9")
    b.link(hermite((-600.0, 1230.0), E, (-15.0, 900.0), 1.5708), "S9b", "NB", True, "NB")
    from roadmri.ingest.synthetic import _line

    _line(
        b, "S9", (-600.0, 1200.0), (-4000.0, 1200.0), {"highway": "motorway", "ref": "SYN9", "oneway": "yes"}
    )
    _line(
        b, "S9b", (-4000.0, 1230.0), (-600.0, 1230.0), {"highway": "motorway", "ref": "SYN9", "oneway": "yes"}
    )
    road(b, "R", 0.0)
    straight_mainline(b, -3000, 3000)
    ctx = build_network(cfg, to_osm(b))
    f = _fac(ctx, "複合|IC")
    assert f.facility_type == "IC" and f.is_jct_combined


def test_ramp_contraction_trumpet(syn):
    """料金所・分岐・合流を含む論理ランプ縮約。"""
    f = _fac(syn, "合成トランペット|IC")
    rs = [r for r in syn.ramps if r.facility_id == f.facility_id]
    flows = sorted(r.flow for r in rs)
    assert flows == ["entry", "entry", "exit", "exit", "two_way", "two_way"]
    # 料金所の前後で別ランプ
    tolls = [r for r in rs if r.from_ref["kind"] == "toll" or r.to_ref["kind"] == "toll"]
    assert len(tolls) == 2
    assert sum(r.is_loop for r in rs) == 1
    # 端点：本線接続ランプは分流（diverge）／合流（merge）
    assert {r.merge_diverge for r in rs if r.flow == "exit"} == {"diverge"}
    assert {r.merge_diverge for r in rs if r.flow == "entry"} == {"merge"}


def test_ramp_branch_side_left_in_japan(syn):
    f = _fac(syn, "合成ダイヤモンド|IC")
    rs = [r for r in syn.ramps if r.facility_id == f.facility_id]
    assert {r.branch_side for r in rs} == {"left"}
    assert {r.carriageway for r in rs} == {"up", "down"}


def test_ids_deterministic(cfg, syn):
    again = build_network(cfg, synthetic_osm())
    assert [r.ramp_id for r in syn.ramps] == [r.ramp_id for r in again.ramps]
    assert [f.facility_id for f in syn.facs] == [f.facility_id for f in again.facs]
    assert all(
        r.ramp_id.startswith(f"{r.facility_id}-RAMP-") and len(r.ramp_id.split("-")[-1]) == 3
        for r in syn.ramps
    )


def test_ramp_id_inheritance():
    class R:
        def __init__(self, rid, ways):
            self.ramp_id, self.way_ids = rid, ways

    new = [R("A-RAMP-001", [1, 2, 3]), R("A-RAMP-002", [7, 8])]
    old = {"A-RAMP-005": {1, 2, 4}, "A-RAMP-006": {9}}
    assert inherit_ids(new, old) == {"A-RAMP-001": "A-RAMP-005"}
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)


def test_section_distance_straight_10km(cfg):
    b = Builder()
    diamond_ic(b, 1000.0, "南IC", road="RS")
    diamond_ic(b, 11000.0, "北IC", road="RN")
    road(b, "RS", 1000.0)
    road(b, "RN", 11000.0)
    straight_mainline(b, -1000, 13000)
    ctx = build_network(cfg, to_osm(b))
    assert len(ctx.sections) == 2
    for s in ctx.sections:
        assert abs(s.distances["standard"] - 10_000.0) < 1.0
        assert not s.anomaly_flags
        assert s.distances["merge_to_diverge"] == pytest.approx(10_000.0 - 800.0, abs=1.0)
    assert {s.direction for s in ctx.sections} == {"up", "down"}


def test_anomaly_out_of_range_and_name_collision(cfg):
    b = Builder()
    diamond_ic(b, 1000.0, "同名IC", road="R1")
    diamond_ic(b, 6000.0, "同名IC", road="R2")
    road(b, "R1", 1000.0)
    road(b, "R2", 6000.0)
    straight_mainline(b, -1000, 8000)
    lon0, lat0 = to_lonlat(-3000, -2000)
    lon1, lat1 = to_lonlat(3000, 3000)
    ctx = build_network(cfg, to_osm(b), box(lon0, lat0, lon1, lat1))
    flags = {f for s in ctx.sections for f in s.anomaly_flags}
    assert {"OUT_OF_RANGE", "NAME_COLLISION"} <= flags


def test_anomaly_excessive_detour(cfg):
    """U 字の本線（経路長 ÷ 直線距離 > 1.8）。"""
    from roadmri.ingest.synthetic import E, N, S, W, hermite

    b = Builder()
    up = [(-15.0, float(y)) for y in range(-2000, 2001, 100)]
    turn = [
        (1500 - 1515 * math.cos(t * math.pi / 40), 2000 + 1515 * math.sin(t * math.pi / 40))
        for t in range(1, 40)
    ]
    down = [(3015.0, float(y)) for y in range(2000, -2001, -100)]
    b.way(up + turn + down, {"highway": "motorway", "ref": "SYN1", "oneway": "yes"})
    b.way(hermite((-15.0, -400.0), N, (-400.0, 0.0), W), {"highway": "motorway_link", "oneway": "yes"})
    b.way(hermite((3015.0, 400.0), S, (3400.0, 0.0), E), {"highway": "motorway_link", "oneway": "yes"})
    b.way([(-2000.0, 0.0), (-400.0, 0.0)], {"highway": "primary"})
    b.way([(3400.0, 0.0), (5000.0, 0.0)], {"highway": "primary"})
    b.node_tags[b.node(-15.0, -400.0)] = {"highway": "motorway_junction", "name": "西IC"}
    b.node_tags[b.node(3015.0, 400.0)] = {"highway": "motorway_junction", "name": "東IC"}
    ctx = build_network(cfg, to_osm(b))
    secs = [s for s in ctx.sections if "EXCESSIVE_DETOUR" in s.anomaly_flags]
    assert secs and secs[0].distances["standard"] > 1.8 * 3030


def test_connectivity_flags(cfg):
    from roadmri.ingest.synthetic import _line

    b = Builder()
    straight_mainline(b, 0, 5000)
    # 別の本線（不連続）と、一般道で接続された本線
    _line(b, "X", (-15.0, 5030.0), (-15.0, 9000.0), {"highway": "motorway", "ref": "SYN1", "oneway": "yes"})
    _line(b, "ISO", (5000.0, 0.0), (5000.0, 3000.0), {"highway": "motorway", "ref": "SYNX", "oneway": "yes"})
    ctx = build_network(cfg, to_osm(b))
    rg = ctx.rg
    nb_start = b.node(-15.0, 0.0)
    nb_end = b.node(-15.0, 5000.0)
    gap_start = b.node(-15.0, 5030.0)
    iso = b.node(5000.0, 0.0)
    assert check_connectivity(rg, nb_start, nb_end) == []
    assert check_connectivity(rg, nb_end, nb_start) == ["ONEWAY_VIOLATION"]
    assert check_connectivity(rg, nb_end, gap_start) == ["NO_PATH"]
    assert check_connectivity(rg, nb_start, iso) == ["NO_PATH"]
    from roadmri.network.mainline import chain_endpoints_gaps

    gaps = chain_endpoints_gaps(rg, ctx.chains, 50.0)
    assert any(g["distance_m"] == pytest.approx(30.0, abs=0.5) for g in gaps)
    # 一般道で接続すると VIA_NON_MOTORWAY
    b2 = Builder()
    straight_mainline(b2, 0, 5000)
    _line(b2, "X", (-15.0, 5030.0), (-15.0, 9000.0), {"highway": "motorway", "ref": "SYN1", "oneway": "yes"})
    b2.way([(-15.0, 5000.0), (-15.0, 5030.0)], {"highway": "primary"})
    ctx2 = build_network(cfg, to_osm(b2))
    assert check_connectivity(ctx2.rg, b2.node(-15.0, 5000.0), b2.node(-15.0, 5030.0)) == ["VIA_NON_MOTORWAY"]
