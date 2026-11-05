"""道路ネットワーク（有向グラフ）。§4.2。

グラフ操作はこのモジュールのインターフェース越しに行い、P4 で rustworkx 等への置換を可能にする。
ノード＝OSM ノード（Way の端点・共有点・タグ付きノード）、エッジ＝その間の Way の区間。
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import networkx as nx
import numpy as np
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from roadmri.elevation.structures import classify_structure, parse_layer
from roadmri.geometry.projection import Projector
from roadmri.ingest.osm_pbf import OsmData

EdgeClass = Literal["mainline", "link", "road"]
LINK_HIGHWAYS = {"motorway_link", "trunk_link"}
ONEWAY_DEFAULT = {"motorway", "motorway_link", "trunk_link"}


def route_ref_of(tags: dict[str, Any], routes_cfg: dict[str, Any]) -> str | None:
    """Way の ref・名称から路線 ref を決める（routes.yaml の別名を適用）。"""
    ref = tags.get("ref")
    name = tags.get("name") or tags.get("name:ja")
    routes = routes_cfg.get("routes", {}) or {}
    if ref:
        tok = str(ref).split(";")[0].strip()
        for key, r in routes.items():
            if tok == key or str(ref) in [str(a) for a in r.get("aliases", [])]:
                return str(key)
        return tok
    if name:
        for key, r in routes.items():
            if name == r.get("name") or name in [str(a) for a in r.get("aliases", [])]:
                return str(key)
        return str(name)
    return None


def edge_class(highway: str, tags: dict[str, Any], routes_cfg: dict[str, Any]) -> EdgeClass:
    if highway == "motorway":
        return "mainline"
    if highway == "trunk":
        trunk_refs = {str(r) for r in routes_cfg.get("trunk_mainline_refs", []) or []}
        ref = str(tags.get("ref", "")).split(";")[0]
        if tags.get("motorroad") == "yes" or (ref and ref in trunk_refs):
            return "mainline"
        return "road"
    if highway in LINK_HIGHWAYS:
        return "link"
    return "road"


def oneway_of(highway: str, tags: dict[str, Any]) -> int:
    """1 = 順方向一方通行、-1 = 逆方向一方通行、0 = 双方向。"""
    ow = str(tags.get("oneway", "")).lower()
    if ow in ("yes", "true", "1"):
        return 1
    if ow == "-1":
        return -1
    if ow in ("no", "false", "0", "reversible", "alternating"):
        return 0
    if tags.get("junction") == "roundabout":
        return 1
    return 1 if highway in ONEWAY_DEFAULT else 0


@dataclass
class EdgeRef:
    u: int
    v: int
    key: str
    data: dict[str, Any]


class RoadGraph:
    def __init__(self, projector: Projector):
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self.proj = projector
        self._mainline_index: tuple[STRtree, list[EdgeRef]] | None = None

    # ---- 構築 -------------------------------------------------------------
    @classmethod
    def build(cls, osm: OsmData, routes_cfg: dict[str, Any], projector: Projector | None = None) -> RoadGraph:
        ways = osm.ways
        if projector is None:
            lon = float(np.mean([np.mean(x) for x in ways["lons"]])) if len(ways) else 139.0
            lat = float(np.mean([np.mean(x) for x in ways["lats"]])) if len(ways) else 36.0
            projector = Projector.for_lonlat(lon, lat)
        rg = cls(projector)
        usage: Counter[int] = Counter()
        for nodes in ways["node_ids"]:
            usage.update(set(nodes))
            usage[nodes[0]] += 1
            usage[nodes[-1]] += 1
        tagged = {int(n): kind for n, kind in zip(osm.nodes["node_id"], osm.nodes["kind"], strict=True)}
        for row in ways.itertuples(index=False):
            tags = dict(row.tags)
            hw = str(row.highway)
            cls_ = edge_class(hw, tags, routes_cfg)
            rref = route_ref_of(tags, routes_cfg) if cls_ == "mainline" else None
            ow = oneway_of(hw, tags)
            nodes = list(row.node_ids)
            lons = list(row.lons)
            lats = list(row.lats)
            for n, lo, la in zip(nodes, lons, lats, strict=True):
                if n not in rg.g:
                    x, y = projector.to_xy(lo, la)
                    rg.g.add_node(n, lon=float(lo), lat=float(la), x=float(x), y=float(y), kind=tagged.get(n))
            cuts = (
                [0]
                + [i for i in range(1, len(nodes) - 1) if usage[nodes[i]] > 1 or nodes[i] in tagged]
                + [len(nodes) - 1]
            )
            for seg, (a, b) in enumerate(itertools.pairwise(cuts)):
                seg_nodes = nodes[a : b + 1]
                seg_lon = lons[a : b + 1]
                seg_lat = lats[a : b + 1]
                if ow == -1:
                    seg_nodes, seg_lon, seg_lat = seg_nodes[::-1], seg_lon[::-1], seg_lat[::-1]
                base = {
                    "way_id": int(row.way_id),
                    "seg": seg,
                    "highway": hw,
                    "cls": cls_,
                    "route_ref": rref,
                    "name": tags.get("name") or tags.get("name:ja"),
                    "ref": tags.get("ref"),
                    "tags": tags,
                    "oneway": ow != 0,
                    "structure": classify_structure(tags),
                    "layer": parse_layer(tags),
                    "timestamp": row.timestamp,
                }
                rg._add_edge(
                    seg_nodes, seg_lon, seg_lat, {**base, "reversed": ow == -1}, f"w{row.way_id}s{seg}"
                )
                if ow == 0:
                    rg._add_edge(
                        seg_nodes[::-1],
                        seg_lon[::-1],
                        seg_lat[::-1],
                        {**base, "reversed": True},
                        f"w{row.way_id}s{seg}r",
                    )
        return rg

    def _add_edge(
        self, nodes: list[int], lons: list[float], lats: list[float], attrs: dict[str, Any], key: str
    ) -> None:
        x, y = self.proj.to_xy(np.asarray(lons), np.asarray(lats))
        xy = np.column_stack([x, y])
        length = float(np.hypot(*np.diff(xy, axis=0).T).sum())
        self.g.add_edge(
            nodes[0],
            nodes[-1],
            key=key,
            nodes=nodes,
            lons=list(lons),
            lats=list(lats),
            xy=xy,
            length_m=length,
            **attrs,
        )

    # ---- 参照 -------------------------------------------------------------
    def edges(self, cls: str | set[str] | None = None) -> Iterator[EdgeRef]:
        want = {cls} if isinstance(cls, str) else cls
        for u, v, k, d in self.g.edges(keys=True, data=True):
            if want is None or d["cls"] in want:
                yield EdgeRef(u, v, k, d)

    def node_xy(self, n: int) -> tuple[float, float]:
        d = self.g.nodes[n]
        return float(d["x"]), float(d["y"])

    def node_lonlat(self, n: int) -> tuple[float, float]:
        d = self.g.nodes[n]
        return float(d["lon"]), float(d["lat"])

    def incident(self, n: int) -> list[EdgeRef]:
        out = [EdgeRef(u, v, k, d) for u, v, k, d in self.g.out_edges(n, keys=True, data=True)]
        out += [EdgeRef(u, v, k, d) for u, v, k, d in self.g.in_edges(n, keys=True, data=True)]
        return out

    def node_classes(self, n: int) -> set[str]:
        return {e.data["cls"] for e in self.incident(n)}

    def subgraph_view(
        self, classes: set[str], route_ref: str | None = None, direction: str | None = None
    ) -> nx.MultiDiGraph:
        def ok(u: int, v: int, k: str) -> bool:
            d = self.g.edges[u, v, k]
            if d["cls"] not in classes:
                return False
            if route_ref is not None and d.get("route_ref") != route_ref:
                return False
            return not (direction is not None and d.get("direction") != direction)

        return nx.subgraph_view(self.g, filter_edge=ok)

    def simple_digraph(self, view: nx.MultiDiGraph) -> nx.DiGraph:
        """多重辺を最短のものに縮約した DiGraph（経路探索用）。"""
        dg = nx.DiGraph()
        for u, v, k, d in view.edges(keys=True, data=True):
            if not dg.has_edge(u, v) or dg[u][v]["length_m"] > d["length_m"]:
                dg.add_edge(u, v, length_m=d["length_m"], key=k)
        return dg

    def path_edges(self, dg: nx.DiGraph, path: list[int]) -> list[EdgeRef]:
        out = []
        for a, b in itertools.pairwise(path):
            k = dg[a][b]["key"]
            out.append(EdgeRef(a, b, k, self.g.edges[a, b, k]))
        return out

    @staticmethod
    def edges_xy(edges: list[EdgeRef]) -> np.ndarray:
        parts = [e.data["xy"] if i == 0 else e.data["xy"][1:] for i, e in enumerate(edges)]
        return np.vstack(parts) if parts else np.zeros((0, 2))

    @staticmethod
    def edges_lonlat(edges: list[EdgeRef]) -> tuple[list[float], list[float]]:
        lons: list[float] = []
        lats: list[float] = []
        for i, e in enumerate(edges):
            lo, la = e.data["lons"], e.data["lats"]
            lons += lo if i == 0 else lo[1:]
            lats += la if i == 0 else la[1:]
        return lons, lats

    def nearest_mainline(
        self, x: float, y: float, route_ref: str | None = None
    ) -> tuple[EdgeRef, float, Point] | None:
        """最寄りの本線エッジと、その上の投影点（距離, 点）を返す。"""
        if self._mainline_index is None:
            refs = list(self.edges("mainline"))
            lines = [LineString(e.data["xy"]) for e in refs]
            self._mainline_index = (STRtree(lines), refs)
        tree, refs = self._mainline_index
        p = Point(x, y)
        cand = tree.query(p.buffer(5000))
        best: tuple[EdgeRef, float, Point] | None = None
        for i in cand:
            e = refs[int(i)]
            if route_ref is not None and e.data.get("route_ref") != route_ref:
                continue
            line = LineString(e.data["xy"])
            d = line.distance(p)
            if best is None or d < best[1]:
                best = (e, float(d), line.interpolate(line.project(p)))
        return best
