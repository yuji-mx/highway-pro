"""米国対応（単位・路線番号・出口番号・方位による方向・英語名・3DEP・投影・国別分類ルール）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from roadmri.classify.engine import classify
from roadmri.geometry.projection import Projector
from roadmri.ingest.synthetic import Builder, builder_to_osm
from roadmri.network.dedupe import Candidate, merge_candidates
from roadmri.network.junctions import LinkComponent, merge_by_shared_components
from roadmri.network.names import NameNormalizer
from roadmri.units import exit_base, normalize_route_ref, parse_speed_kmh, pretty_route, route_number
from tests.net_helpers import build_network


def test_speed_parsing():
    assert parse_speed_kmh("70 mph") == 113
    assert parse_speed_kmh("45 mph") == 72
    assert parse_speed_kmh("100") == 100
    assert parse_speed_kmh("80 km/h") == 80
    assert parse_speed_kmh("none") is None and parse_speed_kmh(None) is None


@pytest.mark.parametrize(
    ("raw", "norm", "pretty", "num"),
    [
        ("I 94", "I 94", "I-94", 94),
        ("I-94", "I 94", "I-94", 94),
        ("I94", "I 94", "I-94", 94),
        ("US 23", "US 23", "US-23", 23),
        ("us-23", "US 23", "US-23", 23),
        ("M 14", "M 14", "M-14", 14),
        ("E4", "E4", "E4", None),
        ("E81", "E81", "E81", None),
    ],
)
def test_route_refs(raw, norm, pretty, num):
    assert normalize_route_ref(raw) == norm
    assert pretty_route(norm) == pretty
    assert route_number(norm) == num


def test_exit_base():
    assert [exit_base(x) for x in ("180A", "180B", "180A-B", "35", None)] == ["180", "180", "180", "35", None]


def test_english_names(cfg):
    nn = NameNormalizer(cfg.name_normalization, cfg.name_aliases)
    assert nn.key("Clyde Rest Area") == "Clyde|PA"
    assert nn.key("Mackinaw Welcome Center") == "Mackinaw|PA"
    assert nn.key("Ohio Turnpike Service Plaza") == "OhioTurnpike|SA"
    assert nn.key("Big Rapids Interchange") == "BigRapids|IC"
    assert nn.key("佐野藤岡ＩＣ") == "佐野藤岡|IC"  # 日本の規則は変わらない


def _cand(osm_id, exit_ref, route, x, y, name=None):
    return Candidate(
        "node", osm_id, "motorway_junction", name, None, None, None, exit_ref, route, None, -83.7, 42.2, x, y
    )


def test_exit_suffix_merge():
    c = [_cand(1, "180A", "I 94", 0, 0), _cand(2, "180B", "I 94", 1200, 0), _cand(3, "181", "I 94", 2600, 0)]
    g = merge_candidates(c, {"default": 2000}, 1500)
    assert sorted(len(x.members) for x in g) == [1, 2]


def test_shared_component_merge():
    """無名の出口どうし：別路線（JCT を両路線から見る）・出口番号なしは統合、同じ路線の別出口は統合しない。"""
    g = merge_candidates(
        [
            _cand(1, "180", "I 94", 0, 0),
            _cand(2, "35", "US 23", 300, 300),
            _cand(3, None, "US 23", 500, 0),
            _cand(4, "181", "I 94", 1600, 0),
        ],
        {"default": 2000},
        1500,
    )
    comp = LinkComponent(
        nodes={1, 2, 3, 4},
        mainline_nodes=set(),
        road_nodes=set(),
        toll_nodes=set(),
        junction_nodes={1, 2, 3, 4},
        dead_ends=set(),
    )
    merged = merge_by_shared_components(g, [comp])
    sizes = sorted(len(x.members) for x in merged)
    assert sizes == [1, 3]
    assert any(r["rule"] == "shared_link_component" for x in merged for r in x.reasons)
    # 名前のある施設どうしは統合しない
    named = [_cand(10, "1", "I 94", 0, 0, "A"), _cand(11, "2", "US 23", 10, 0, "B")]
    for c in named:
        c.norm_key = c.name
    g2 = merge_candidates(named, {"default": 2000}, 1500)
    comp2 = LinkComponent(
        nodes={10, 11},
        mainline_nodes=set(),
        road_nodes=set(),
        toll_nodes=set(),
        junction_nodes={10, 11},
        dead_ends=set(),
    )
    assert len(merge_by_shared_components(g2, [comp2])) == 2


def test_cardinal_directions(cfg):
    b = Builder()
    b.way([(0.0, 0.0), (0.0, 3000.0)], {"highway": "motorway", "ref": "I 75", "oneway": "yes"})  # 奇数＝南北
    b.way([(30.0, 3000.0), (30.0, 0.0)], {"highway": "motorway", "ref": "I 75", "oneway": "yes"})
    b.way(
        [(-3000.0, 5000.0), (3000.0, 5200.0)], {"highway": "motorway", "ref": "I 94", "oneway": "yes"}
    )  # 偶数＝東西
    b.way([(3000.0, 5230.0), (-3000.0, 5030.0)], {"highway": "motorway", "ref": "I 94", "oneway": "yes"})
    ctx = build_network(cfg, builder_to_osm(b))
    from roadmri.network.mainline import assign_directions

    assign_directions(ctx.rg, cfg.routes, "US")
    dirs = {(e.data["route_ref"], e.data["direction"]) for e in ctx.rg.edges("mainline")}
    assert dirs == {("I 75", "north"), ("I 75", "south"), ("I 94", "east"), ("I 94", "west")}
    assign_directions(ctx.rg, cfg.routes, "JP")
    assert {e.data["direction"] for e in ctx.rg.edges("mainline")} <= {"up", "down"}


def test_projection_multi_zone():
    one = Projector.for_bounds(139.7, 36.5, 139.9, 36.7)
    assert one.epsg == 32654
    mi = Projector.for_bounds(-90.42, 41.69, -82.41, 48.31)  # ミシガン州（UTM 15〜17）
    assert isinstance(mi.epsg, str) and "+proj=tmerc" in mi.epsg and "lon_0=-86.415" in mi.epsg
    from pyproj import Geod

    g = Geod(ellps="WGS84")
    for lon in (-90.0, -86.4, -82.6):
        x, y = mi.to_xy(np.array([lon, lon + 0.01]), np.array([45.0, 45.0]))
        _, _, d = g.inv(lon, 45.0, lon + 0.01, 45.0)
        assert abs(float(np.hypot(x[1] - x[0], y[1] - y[0])) - d) / d < 0.0025
    lo, la = mi.to_lonlat(*mi.to_xy(-84.0, 44.0))
    assert float(lo) == pytest.approx(-84.0) and float(la) == pytest.approx(44.0)


def test_country_override_rules(cfg):
    f = {
        "facility_type": "IC",
        "mainline_count": 1,
        "loop_ramp_count": 1,
        "general_road_connections": 1,
        "ramp_count": 4,
        "toll_present": False,
        "toll_on_general_side": False,
        "has_general_road": True,
    }
    us = classify(f, cfg.classification_rules, 0.5, "US")
    jp = classify(f, cfg.classification_rules, 0.5, "JP")
    assert us.shape_class == jp.shape_class == "トランペット型"
    # 北米では料金所の有無を判定に使わない
    assert any("料金所" in r["text_ja"] for r in jp.reasons)
    assert not any("料金所" in r["text_ja"] for r in us.reasons)
    assert any(
        "parclo" in r["text_ja"]
        for r in classify(
            {**f, "loop_ramp_count": 2, "general_road_connections": 2}, cfg.classification_rules, 0.5, "US"
        ).reasons
    )


def test_usgs_3dep_tile_decode(cfg):
    """同梱の 3DEP タイル（z15、NAVD88）を補間して標高が得られる（オフライン）。"""
    from roadmri.ingest.dem import DemSampler, build_providers

    providers = build_providers(
        {**cfg.dem_sources, "local_sources": []},
        "us_fixture",
        {"tile_cache_dirs": [cfg.root / "tests" / "fixtures" / "dem" / "usgs"], "offline": True},
    )
    assert providers[0].adapter == "usgs_3dep_tile" and providers[0].meta.vertical_datum == "NAVD88"
    z, src, _res = DemSampler(providers).sample(np.array([-83.7327, -83.6763]), np.array([42.2372, 42.2248]))
    assert np.all((z > 230) & (z < 300)) and set(src) == {"usgs_3dep_z15"}
    z2, _, _ = DemSampler(providers).sample(np.array([-100.0]), np.array([40.0]))
    assert np.isnan(z2[0])  # キャッシュ外はオフラインでは欠損


def test_country_range(synthetic_db):
    from roadmri.analysis.ranges import range_label, resolve_facilities

    fac = pd.DataFrame(
        {
            "facility_id": ["JP-F-1", "US-F-2"],
            "country": ["JP", "US"],
            "lon": [0, 0],
            "lat": [0, 0],
            "route_refs": [[], []],
            "shape_class": ["a", "b"],
            "admin_code": ["09", "26"],
        }
    )
    assert resolve_facilities({"type": "country", "countries": ["us"]}, fac) == {"US-F-2"}
    assert resolve_facilities({"type": "admin", "admin_codes": ["26"]}, fac) == {"US-F-2"}
    assert range_label({"type": "country", "countries": ["JP", "US"]}) == "国 JP・US"
