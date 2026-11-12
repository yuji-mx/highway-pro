"""IC/JCT 間区間と距離定義（§4.5）。"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np

from roadmri.network.graph import RoadGraph
from roadmri.network.junctions import Facility
from roadmri.network.mainline import Chain
from roadmri.network.ramps import Ramp

DEFINITIONS = (
    "standard",
    "diverge_to_diverge",
    "merge_to_diverge",
    "facility_point",
    "toll_to_toll",
    "ramp_end_to_ramp_end",
    "mainline_connection",
)


@dataclass
class FacilityOnChain:
    facility: Facility
    diverges: list[tuple[float, Ramp]]
    merges: list[tuple[float, Ramp]]
    s_facility_point: float

    @property
    def conn(self) -> list[float]:
        return sorted([s for s, _ in self.diverges] + [s for s, _ in self.merges])

    @property
    def first_diverge(self) -> float | None:
        return min((s for s, _ in self.diverges), default=None)

    @property
    def last_merge(self) -> float | None:
        return max((s for s, _ in self.merges), default=None)

    @property
    def s_rep(self) -> float:
        fd, lm = self.first_diverge, self.last_merge
        if fd is not None and lm is not None:
            return (fd + lm) / 2.0
        c = self.conn
        if c:
            return (c[0] + c[-1]) / 2.0
        return self.s_facility_point


@dataclass
class Section:
    section_id: str
    route_ref: str
    from_facility_id: str
    to_facility_id: str
    direction: str
    chain_id: str | None
    s_from: float
    s_to: float
    distances: dict[str, float | None]
    distance_points: dict[str, tuple[float | None, float | None]]
    geom_xy: np.ndarray
    way_ids: list[int]
    anomaly_flags: list[str] = field(default_factory=list)
    anomaly_detail: list[dict[str, Any]] = field(default_factory=list)
    passes_non_motorway: bool = False
    tunnel_len_m: float = 0.0
    bridge_len_m: float = 0.0
    edges_struct: list[tuple[float, float, str, int]] = field(default_factory=list)

    @property
    def dist_standard_m(self) -> float | None:
        return self.distances.get("standard")


class FacilityIndex:
    """区間生成で施設を引くための索引（施設 ID → 施設、出口ノード → 施設の順位と構成員の順位）。"""

    def __init__(self, facs: list[Facility]):
        self.facs = facs
        self.by_id = {f.facility_id: f for f in facs}
        self.by_node: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for fi, f in enumerate(facs):
            for mi, m in enumerate(f.members):
                if m.osm_type == "node":
                    self.by_node[m.osm_id].append((fi, mi))


class RampEndIndex:
    """ランプの始点・終点ノード → ランプの位置（元のリスト順）。チェーンごとの全ランプ走査を避ける。"""

    def __init__(self, ramps: list[Ramp]):
        self.ramps = ramps
        self.by_node: dict[int, list[int]] = defaultdict(list)
        for i, r in enumerate(ramps):
            self.by_node[r.nodes[0]].append(i)
            if r.nodes[-1] != r.nodes[0]:
                self.by_node[r.nodes[-1]].append(i)

    def touching(self, nodes: set[int]) -> list[Ramp]:
        hit = {i for n in nodes & self.by_node.keys() for i in self.by_node[n]}
        return [self.ramps[i] for i in sorted(hit)]


def facilities_on_chain(
    rg: RoadGraph,
    chain: Chain,
    facs: list[Facility],
    ramps: list[Ramp],
    index: FacilityIndex | None = None,
    ramp_index: RampEndIndex | None = None,
) -> list[FacilityOnChain]:
    nodes = set(chain.node_s)
    by_fac: dict[str, FacilityOnChain] = {}
    idx = index if index is not None else FacilityIndex(facs)
    fac_by_id = idx.by_id
    # チェーン上に端点を持つランプだけを、元のリスト順で処理する（同順位の扱いを変えないため）
    candidates = ramp_index.touching(nodes) if ramp_index is not None else ramps
    for r in candidates:
        f = fac_by_id[r.facility_id]
        start, end = r.nodes[0], r.nodes[-1]
        ent = by_fac.get(f.facility_id)
        if (
            start in nodes
            and r.from_ref.get("kind") == "mainline"
            and r.from_ref.get("route_ref") == chain.route_ref
        ):
            ent = ent or by_fac.setdefault(
                f.facility_id, FacilityOnChain(f, [], [], chain.s_of_point(f.x, f.y))
            )
            ent.diverges.append((chain.node_s[start], r))
        if (
            end in nodes
            and r.to_ref.get("kind") == "mainline"
            and r.to_ref.get("route_ref") == chain.route_ref
        ):
            ent = ent or by_fac.setdefault(
                f.facility_id, FacilityOnChain(f, [], [], chain.s_of_point(f.x, f.y))
            )
            ent.merges.append((chain.node_s[end], r))
    # ランプを持たないが本線上に出口ノードがある施設（施設ごとに、チェーン上にある最初の構成員を使う）
    first: dict[int, int] = {}
    for n in nodes & idx.by_node.keys():
        for fi, mi in idx.by_node[n]:
            if fi not in first or mi < first[fi]:
                first[fi] = mi
    for fi in sorted(first):
        f = idx.facs[fi]
        if f.facility_id not in by_fac:
            m = f.members[first[fi]]
            by_fac[f.facility_id] = FacilityOnChain(f, [], [], chain.node_s[m.osm_id])
    return sorted(by_fac.values(), key=lambda e: (e.s_rep, e.facility.facility_id))


def _chain_slice(chain: Chain, s0: float, s1: float) -> np.ndarray:
    return chain.slice_xy(max(0.0, s0), min(chain.line.length, s1))


def _ways_and_struct(
    chain: Chain, s0: float, s1: float
) -> tuple[list[int], list[tuple[float, float, str, int]]]:
    ways: list[int] = []
    struct: list[tuple[float, float, str, int]] = []
    acc = 0.0
    for e in chain.edges:
        a, b = acc, acc + e.data["length_m"]
        acc = b
        if b <= s0 or a >= s1:
            continue
        if not ways or ways[-1] != e.data["way_id"]:
            ways.append(e.data["way_id"])
        lo, hi = max(a, s0) - s0, min(b, s1) - s0
        struct.append((lo, hi, e.data["structure"], int(e.data["layer"])))
    return ways, struct


def _link_path_len(rg: RoadGraph, a: int, b: int) -> float | None:
    view = rg.subgraph_view({"link"})
    try:
        return float(nx.shortest_path_length(view, a, b, weight="length_m"))
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def build_sections(
    rg: RoadGraph, chains: list[Chain], facs: list[Facility], ramps: list[Ramp], detour_ratio: float
) -> list[Section]:
    sections: list[Section] = []
    names: dict[str, int] = {}
    for f in facs:
        if f.normalized_name:
            names[f.normalized_name] = names.get(f.normalized_name, 0) + 1
    fidx = FacilityIndex(facs)
    rindex: dict[str, RampEndIndex] = {}
    ramps_by_route: dict[str, list[Ramp]] = defaultdict(list)
    for r in ramps:
        for route in {r.from_ref.get("route_ref"), r.to_ref.get("route_ref")}:
            if route is not None:
                ramps_by_route[route].append(r)
    for ch in chains:
        if ch.route_ref not in rindex:
            rindex[ch.route_ref] = RampEndIndex(ramps_by_route[ch.route_ref])
        on = facilities_on_chain(rg, ch, facs, ramps_by_route[ch.route_ref], fidx, rindex[ch.route_ref])
        for a, b in itertools.pairwise(on):
            fa, fb = a.facility, b.facility
            d: dict[str, float | None] = dict.fromkeys(DEFINITIONS)
            pts: dict[str, tuple[float | None, float | None]] = {}
            d["standard"] = b.s_rep - a.s_rep
            pts["standard"] = (a.s_rep, b.s_rep)
            if a.first_diverge is not None and b.first_diverge is not None:
                d["diverge_to_diverge"] = b.first_diverge - a.first_diverge
                pts["diverge_to_diverge"] = (a.first_diverge, b.first_diverge)
            if a.last_merge is not None and b.first_diverge is not None:
                d["merge_to_diverge"] = b.first_diverge - a.last_merge
                pts["merge_to_diverge"] = (a.last_merge, b.first_diverge)
            d["facility_point"] = b.s_facility_point - a.s_facility_point
            pts["facility_point"] = (a.s_facility_point, b.s_facility_point)
            if a.conn and b.conn:
                d["mainline_connection"] = b.conn[0] - a.conn[0]
                pts["mainline_connection"] = (a.conn[0], b.conn[0])
            if a.last_merge is not None and b.first_diverge is not None:
                entry = max(a.merges, key=lambda t: t[0])[1]
                exit_ = min(b.diverges, key=lambda t: t[0])[1]
                d["ramp_end_to_ramp_end"] = (b.first_diverge - a.last_merge) + entry.length_m + exit_.length_m
                pts["ramp_end_to_ramp_end"] = (a.last_merge, b.first_diverge)
                if fa.toll_nodes and fb.toll_nodes:
                    la = min(
                        (
                            x
                            for t in fa.toll_nodes
                            if (x := _link_path_len(rg, t, entry.nodes[-1])) is not None
                        ),
                        default=None,
                    )
                    lb = min(
                        (
                            x
                            for t in fb.toll_nodes
                            if (x := _link_path_len(rg, exit_.nodes[0], t)) is not None
                        ),
                        default=None,
                    )
                    if la is not None and lb is not None:
                        d["toll_to_toll"] = la + (b.first_diverge - a.last_merge) + lb
                        pts["toll_to_toll"] = (a.last_merge, b.first_diverge)
            s0, s1 = sorted((a.s_rep, b.s_rep))
            ways, struct = _ways_and_struct(ch, s0, s1)
            sec = Section(
                section_id=f"{ch.route_ref}-{fa.facility_id}-{fb.facility_id}-{ch.direction}",
                route_ref=ch.route_ref,
                from_facility_id=fa.facility_id,
                to_facility_id=fb.facility_id,
                direction=ch.direction,
                chain_id=ch.chain_id,
                s_from=a.s_rep,
                s_to=b.s_rep,
                distances=d,
                distance_points=pts,
                geom_xy=_chain_slice(ch, s0, s1),
                way_ids=ways,
                edges_struct=struct,
            )
            sec.tunnel_len_m = float(sum(hi - lo for lo, hi, st, _ in struct if st == "tunnel"))
            sec.bridge_len_m = float(sum(hi - lo for lo, hi, st, _ in struct if st in ("bridge", "viaduct")))
            straight = float(np.hypot(fa.x - fb.x, fa.y - fb.y))
            std = d["standard"] or 0.0
            if straight > 0 and std / straight > detour_ratio:
                sec.anomaly_flags.append("EXCESSIVE_DETOUR")
                sec.anomaly_detail.append({"code": "EXCESSIVE_DETOUR", "ratio": round(std / straight, 3)})
            if not fa.in_range or not fb.in_range:
                sec.anomaly_flags.append("OUT_OF_RANGE")
            for f in (fa, fb):
                if f.normalized_name and names.get(f.normalized_name, 0) > 1:
                    sec.anomaly_flags.append("NAME_COLLISION")
                    break
            sections.append(sec)
    return sections


class ConnectivityCache:
    """経路異常判定用のグラフ（本線だけの有向グラフとその無向ビュー）を一度だけ作る。

    rg.subgraph_view は辺を訪れるたびに Python のフィルタ関数を呼ぶため、判定を繰り返すと遅い。
    """

    def __init__(self, rg: RoadGraph):
        self.ml = nx.DiGraph()
        for e in rg.edges("mainline"):
            self.ml.add_edge(e.u, e.v)
        self.ml_und = self.ml.to_undirected(as_view=True)


def check_connectivity(
    rg: RoadGraph, a_node: int, b_node: int, cache: ConnectivityCache | None = None
) -> list[str]:
    """§4.5.5 の経路異常判定（本線→無向→全道路の順に到達性を確認）。"""
    c = cache if cache is not None else ConnectivityCache(rg)

    def has(g: Any, a: int, b: int) -> bool:
        return a == b or (a in g and b in g and bool(nx.has_path(g, a, b)))

    if has(c.ml, a_node, b_node):
        return []
    if has(c.ml_und, a_node, b_node):
        return ["ONEWAY_VIOLATION"]
    if has(rg.g, a_node, b_node):  # 全道路（本線・ランプ・一般道）＝グラフ全体
        return ["VIA_NON_MOTORWAY"]
    return ["NO_PATH"]


def gap_sections(
    rg: RoadGraph, chains: list[Chain], facs: list[Facility], ramps: list[Ramp], gaps: list[dict[str, Any]]
) -> list[Section]:
    """同一路線・同一方向で本線チェーンが分断している場合に、分断をまたぐ区間を異常付きで生成する。"""
    out: list[Section] = []
    groups: dict[tuple[str, str], list[Chain]] = {}
    for c in chains:
        groups.setdefault((c.route_ref, c.direction), []).append(c)
    gap_pairs = {(g["chain_a"], g["chain_b"]) for g in gaps} | {(g["chain_b"], g["chain_a"]) for g in gaps}
    fidx = FacilityIndex(facs)
    conn: ConnectivityCache | None = None
    ramps_by_route: dict[str, list[Ramp]] = defaultdict(list)
    for r in ramps:
        for route in {r.from_ref.get("route_ref"), r.to_ref.get("route_ref")}:
            if route is not None:
                ramps_by_route[route].append(r)
    for (route, direction), cs in groups.items():
        if len(cs) < 2:
            continue
        ri = RampEndIndex(ramps_by_route[route])
        ons = {c.chain_id: facilities_on_chain(rg, c, facs, ramps_by_route[route], fidx, ri) for c in cs}
        ends = {c.chain_id: (np.array(rg.node_xy(c.nodes[-1])), np.array(rg.node_xy(c.nodes[0]))) for c in cs}
        for ca in cs:
            for cb in cs:
                if ca is cb or not ons[ca.chain_id] or not ons[cb.chain_id]:
                    continue
                ea = ends[ca.chain_id][0]
                sb = ends[cb.chain_id][1]
                if np.hypot(*(ea - sb)) > 5000:
                    continue
                last, first = ons[ca.chain_id][-1], ons[cb.chain_id][0]
                fa, fb = last.facility, first.facility
                conn = conn or ConnectivityCache(rg)
                flags = check_connectivity(rg, ca.nodes[-1], cb.nodes[0], conn)
                if (ca.chain_id, cb.chain_id) in gap_pairs:
                    flags.append("MAINLINE_GAP")
                if not flags:
                    flags = ["NETWORK_DISCONTINUITY"]
                dist = (ca.length_m - last.s_rep) + float(np.hypot(*(ea - sb))) + first.s_rep
                xy = np.vstack([_chain_slice(ca, last.s_rep, ca.length_m), _chain_slice(cb, 0, first.s_rep)])
                sec = Section(
                    section_id=f"{route}-{fa.facility_id}-{fb.facility_id}-{direction}",
                    route_ref=route,
                    from_facility_id=fa.facility_id,
                    to_facility_id=fb.facility_id,
                    direction=direction,
                    chain_id=None,
                    s_from=last.s_rep,
                    s_to=first.s_rep,
                    distances={**dict.fromkeys(DEFINITIONS), "standard": dist},
                    distance_points={},
                    geom_xy=xy,
                    way_ids=[],
                    anomaly_flags=flags,
                )
                out.append(sec)
    return out
