from types import SimpleNamespace

import networkx as nx
import pytest

from roadmri.network.mainline import chain_endpoints_gaps
from roadmri.pipeline import _edge_at


def test_edge_at_preserves_boundary_selection():
    edges = [SimpleNamespace(data={"length_m": length}) for length in (10.0, 20.0, 5.0)]
    chain = SimpleNamespace(edges=edges, s=[0.0, 10.0, 30.0, 35.0])
    for distance in (-1.0, 0.0, 10.0, 10.0001, 30.0, 35.0, 100.0):
        acc = 0.0
        expected = edges[-1]
        for edge in edges:
            acc += edge.data["length_m"]
            if acc >= distance:
                expected = edge
                break
        assert _edge_at(chain, distance) is expected
    assert _edge_at(SimpleNamespace(edges=[], s=[]), 0.0) is None


def test_gap_index_matches_distance_and_route_rules():
    graph = nx.MultiDiGraph()
    coords = [(0.0, 0.0), (100.0, 0.0), (103.0, 4.0), (200.0, 0.0), (104.0, 0.0), (300.0, 0.0)]
    chains = [SimpleNamespace(chain_id=f"R-north-{i}", nodes=[2 * i, 2 * i + 1]) for i in range(3)]
    graph.add_nodes_from(range(6))
    rg = SimpleNamespace(g=graph, node_xy=lambda n: coords[n])
    result = chain_endpoints_gaps(rg, chains, 5.0)
    assert [(r["node_a"], r["node_b"]) for r in result] == [(1, 2), (1, 4)]
    assert result[0]["distance_m"] == pytest.approx(5.0)
    graph.add_edge(1, 2)
    assert [(r["node_a"], r["node_b"]) for r in chain_endpoints_gaps(rg, chains, 5.0)] == [(1, 4)]
    chains[2].chain_id = "S-north-0"
    assert chain_endpoints_gaps(rg, chains, 5.0) == []
    assert chain_endpoints_gaps(rg, [], 5.0) == []


def test_cardinal_axis_is_calculated_once_per_route(monkeypatch):
    from roadmri.network import mainline

    calls = []

    def axis(rg, route):
        calls.append(route)
        return "NS"

    monkeypatch.setattr(mainline, "route_axis", axis)
    edges = [
        SimpleNamespace(data={"route_ref": "Unnamed", "xy": [(0.0, 0.0), (0.0, 100.0)], "length_m": 100.0})
        for _ in range(100)
    ]
    mainline.assign_cardinal_directions(SimpleNamespace(edges=lambda cls: iter(edges)))
    assert calls == ["Unnamed"]
    assert all(e.data["direction"] == "north" for e in edges)


@pytest.mark.parametrize("start,end", [(0, 10), (3, 25), (10, 30), (30, 10), (12, 12), (0, 100)])
def test_indexed_slice_matches_shapely(start, end):
    import numpy as np
    from shapely.geometry import LineString
    from shapely.ops import substring

    from roadmri.network.mainline import Chain

    line = LineString([(0, 0), (10, 0), (10, 20), (30, 20)])
    chain = Chain("test", "R", "north", [], [], line)
    expected = substring(line, start, end)
    coords = list(expected.coords)
    if expected.geom_type == "Point":
        coords *= 2
    np.testing.assert_allclose(chain.slice_xy(start, end), coords, atol=1e-9)
    assert chain._slice_index is chain._slice_index


def _brute_merge(cands, merge_cfg, exit_d):
    """KD-tree 導入前の総当たり実装（比較用）。"""
    import numpy as np

    from roadmri.network.dedupe import _UF
    from roadmri.units import exit_base

    n = len(cands)
    uf = _UF(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = cands[i], cands[j]
            d = float(np.hypot(a.x - b.x, a.y - b.y))
            if a.norm_key and b.norm_key and a.norm_key == b.norm_key:
                thr = float(merge_cfg.get(a.code or b.code or "default", merge_cfg.get("default", 2000)))
                if d <= thr:
                    uf.union(i, j)
            elif (
                (not a.norm_key or not b.norm_key)
                and a.exit_ref
                and exit_base(a.exit_ref) == exit_base(b.exit_ref)
                and a.route_ref
                and a.route_ref == b.route_ref
                and d <= exit_d
            ):
                uf.union(i, j)
    return sorted(
        sorted(c.osm_id for k, c in enumerate(cands) if uf.find(k) == r)
        for r in {uf.find(k) for k in range(n)}
    )


@pytest.mark.parametrize("seed", range(5))
def test_merge_candidates_matches_bruteforce(seed):
    import numpy as np

    from roadmri.network.dedupe import Candidate, merge_candidates

    rng = np.random.default_rng(seed)
    names = [None, "A|IC", "B|IC", "C|JCT"]
    cands = []
    for i in range(300):
        nk = names[rng.integers(0, 4)]
        cands.append(
            Candidate(
                "node",
                i,
                "motorway_junction",
                nk,
                None,
                nk,
                nk.split("|")[1] if nk else None,
                str(rng.integers(1, 6)) + "AB"[rng.integers(0, 2)],
                ["I 1", "I 2"][rng.integers(0, 2)],
                None,
                0.0,
                0.0,
                float(rng.uniform(0, 20000)),
                float(rng.uniform(0, 20000)),
            )
        )
    cfg = {"IC": 2000, "JCT": 3000, "default": 2000}
    got = sorted(sorted(m.osm_id for m in g.members) for g in merge_candidates(cands, cfg, 1500))
    assert got == _brute_merge(cands, cfg, 1500)


def _chain_key(xs):
    return [
        (
            e.facility.facility_id,
            e.s_rep,
            [(s, r.ramp_id) for s, r in e.diverges],
            [(s, r.ramp_id) for s, r in e.merges],
        )
        for e in xs
    ]


def test_facilities_on_chain_index_matches_full_scan(cfg):
    from roadmri.ingest.synthetic import synthetic_osm
    from roadmri.network.sections import FacilityIndex, RampEndIndex, facilities_on_chain
    from tests.net_helpers import build_network

    ctx = build_network(cfg, synthetic_osm())
    fidx = FacilityIndex(ctx.facs)
    for ch in ctx.chains:
        full = facilities_on_chain(ctx.rg, ch, ctx.facs, ctx.ramps)
        fast = facilities_on_chain(ctx.rg, ch, ctx.facs, ctx.ramps, fidx, RampEndIndex(ctx.ramps))
        assert _chain_key(full) == _chain_key(fast)


@pytest.mark.parametrize(
    "values", [[1.0, 2.0, 4.0], [float("nan"), 3.0, 0.1, 0.2, 0.3], [float("nan")] * 3, [-0.002, 0.004]]
)
def test_nan_stats_match_pandas(values):
    import numpy as np
    import pandas as pd

    from roadmri.pipeline import _nanmax, _nanmean

    a = np.array(values)
    s = pd.Series(values)
    for got, ref in ((_nanmean(a), s.mean()), (_nanmax(np.abs(a)), s.abs().max())):
        assert (np.isnan(got) and np.isnan(ref)) or got == ref
