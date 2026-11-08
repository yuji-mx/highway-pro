"""本線 carriageway の方向付けとチェーン（線形参照）。§4.2-4。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from shapely.geometry import LineString, Point

from roadmri.network.graph import EdgeRef, RoadGraph


def route_origins(rg: RoadGraph, routes_cfg: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """路線ごとの起点（UTM）。routes.yaml にない路線は、その路線の最西（同値は最南）の端点を起点とする。"""
    out: dict[str, tuple[float, float]] = {}
    routes = routes_cfg.get("routes", {}) or {}
    by_route: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    for e in rg.edges("mainline"):
        r = e.data["route_ref"]
        if r is None:
            continue
        by_route[r].append((e.data["lons"][0], e.data["lats"][0], *rg.node_xy(e.u)))
    for r, pts in by_route.items():
        if r in routes and routes[r].get("origin"):
            o = routes[r]["origin"]
            x, y = rg.proj.to_xy(float(o["lon"]), float(o["lat"]))
            out[r] = (float(x), float(y))
        else:
            p = min(pts, key=lambda t: (round(t[0], 4), t[1]))
            out[r] = (p[2], p[3])
    return out


CARDINAL = ("north", "south", "east", "west")


def route_axis(rg: RoadGraph, route: str) -> str:
    """米国式の路線の軸（"NS" / "EW"）。路線番号が奇数なら南北、偶数なら東西。番号がなければ路線の広がりで決める。"""
    from roadmri.units import route_number

    num = route_number(route)
    if num is not None:
        return "NS" if num % 2 == 1 else "EW"
    xs: list[float] = []
    ys: list[float] = []
    for e in rg.edges("mainline"):
        if e.data["route_ref"] == route:
            xs += [float(e.data["xy"][0][0]), float(e.data["xy"][-1][0])]
            ys += [float(e.data["xy"][0][1]), float(e.data["xy"][-1][1])]
    return "NS" if (max(ys) - min(ys)) >= (max(xs) - min(xs)) else "EW"


def assign_cardinal_directions(rg: RoadGraph) -> None:
    """米国・カナダ：本線の向きを方位（north / south / east / west）で表す（標識の NB/SB/EB/WB に相当）。

    軸方向の移動量がエッジ長の 20 % 未満のエッジ（軸と直交する区間）は前後の同一路線エッジから継承する。
    """
    axes: dict[str, str] = {}
    pending: list[EdgeRef] = []
    for e in rg.edges("mainline"):
        r = e.data["route_ref"]
        if r is None:
            e.data["direction"] = "unknown"
            continue
        ax = axes.setdefault(r, route_axis(rg, r))
        (x0, y0), (x1, y1) = e.data["xy"][0], e.data["xy"][-1]
        d = (y1 - y0) if ax == "NS" else (x1 - x0)
        if abs(d) < 0.2 * max(e.data["length_m"], 1e-6):
            e.data["direction"] = None
            pending.append(e)
        elif ax == "NS":
            e.data["direction"] = "north" if d > 0 else "south"
        else:
            e.data["direction"] = "east" if d > 0 else "west"
    _inherit(rg, pending)
    for e in pending:
        if not e.data.get("direction"):
            (x0, y0), (x1, y1) = e.data["xy"][0], e.data["xy"][-1]
            if axes.get(e.data["route_ref"]) == "NS":
                e.data["direction"] = "north" if y1 >= y0 else "south"
            else:
                e.data["direction"] = "east" if x1 >= x0 else "west"


def _inherit(rg: RoadGraph, pending: list[EdgeRef]) -> None:
    for _ in range(10):
        if not pending:
            break
        still: list[EdgeRef] = []
        for e in pending:
            nb = [
                x
                for x in rg.g.in_edges(e.u, keys=True, data=True)
                if x[3]["cls"] == "mainline"
                and x[3].get("route_ref") == e.data["route_ref"]
                and x[3].get("direction")
            ]
            nb += [
                x
                for x in rg.g.out_edges(e.v, keys=True, data=True)
                if x[3]["cls"] == "mainline"
                and x[3].get("route_ref") == e.data["route_ref"]
                and x[3].get("direction")
            ]
            if nb:
                e.data["direction"] = nb[0][3]["direction"]
            else:
                still.append(e)
        pending[:] = still


def assign_directions(rg: RoadGraph, routes_cfg: dict[str, Any], country: str = "JP") -> None:
    """本線エッジに direction を付与する。

    日本：up/down（起点に近づく向き＝上り）、環状は inner/outer。米国・カナダ：north/south/east/west
    （routes.yaml の direction_mode で国ごとに切り替える）。
    """
    mode = (routes_cfg.get("direction_mode", {}) or {}).get(country, "origin")
    if mode == "cardinal":
        assign_cardinal_directions(rg)
        return
    origins = route_origins(rg, routes_cfg)
    rings = set(routes_cfg.get("ring_routes", []) or [])
    pending: list[EdgeRef] = []
    for e in rg.edges("mainline"):
        r = e.data["route_ref"]
        if r is None or r not in origins:
            e.data["direction"] = "unknown"
            continue
        ox, oy = origins[r]
        x0, y0 = e.data["xy"][0]
        x1, y1 = e.data["xy"][-1]
        d0 = np.hypot(x0 - ox, y0 - oy)
        d1 = np.hypot(x1 - ox, y1 - oy)
        if r in rings:
            cross = (x0 - ox) * (y1 - oy) - (y0 - oy) * (x1 - ox)
            e.data["direction"] = "outer" if cross < 0 else "inner"  # 時計回り＝外回り
            continue
        if abs(d1 - d0) < 0.2 * max(e.data["length_m"], 1e-6):
            e.data["direction"] = None
            pending.append(e)
        else:
            e.data["direction"] = "up" if d1 < d0 else "down"
    # 曖昧なエッジは前後の同一路線エッジから継承する
    for _ in range(10):
        if not pending:
            break
        still: list[EdgeRef] = []
        for e in pending:
            nb = [
                x
                for x in rg.g.in_edges(e.u, keys=True, data=True)
                if x[3]["cls"] == "mainline"
                and x[3].get("route_ref") == e.data["route_ref"]
                and x[3].get("direction")
            ]
            nb += [
                x
                for x in rg.g.out_edges(e.v, keys=True, data=True)
                if x[3]["cls"] == "mainline"
                and x[3].get("route_ref") == e.data["route_ref"]
                and x[3].get("direction")
            ]
            if nb:
                e.data["direction"] = nb[0][3]["direction"]
            else:
                still.append(e)
        pending = still
    for e in pending:
        x0, y0 = e.data["xy"][0]
        x1, y1 = e.data["xy"][-1]
        ox, oy = origins[e.data["route_ref"]]
        e.data["direction"] = "up" if np.hypot(x1 - ox, y1 - oy) <= np.hypot(x0 - ox, y0 - oy) else "down"


@dataclass
class Chain:
    chain_id: str
    route_ref: str
    direction: str
    nodes: list[int]
    s: list[float]
    line: LineString
    node_s: dict[int, float] = field(default_factory=dict)
    edges: list[EdgeRef] = field(default_factory=list)
    lons: list[float] = field(default_factory=list)
    lats: list[float] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return float(self.line.length)

    def s_of_point(self, x: float, y: float) -> float:
        return float(self.line.project(Point(x, y)))


def _longest_path(dg: nx.DiGraph) -> list[int]:
    g = dg.copy()
    while not nx.is_directed_acyclic_graph(g):
        cyc = nx.find_cycle(g)
        u, v = max(cyc, key=lambda e: g[e[0]][e[1]]["length_m"])[:2]
        g.remove_edge(u, v)
    return list(nx.dag_longest_path(g, weight="length_m"))


def build_chains(rg: RoadGraph) -> list[Chain]:
    """路線×方向ごとの本線チェーン（弱連結成分ごと）。チェーン上の弧長 s で線形参照する。"""
    groups: dict[tuple[str, str], list[EdgeRef]] = defaultdict(list)
    for e in rg.edges("mainline"):
        if e.data["route_ref"]:
            groups[(e.data["route_ref"], e.data.get("direction") or "unknown")].append(e)
    chains: list[Chain] = []
    for (route, direction), edges in sorted(groups.items()):
        dg = nx.DiGraph()
        for e in edges:
            if not dg.has_edge(e.u, e.v) or dg[e.u][e.v]["length_m"] > e.data["length_m"]:
                dg.add_edge(e.u, e.v, length_m=e.data["length_m"], key=e.key)
        comps = sorted(
            nx.weakly_connected_components(dg),
            key=lambda c: -sum(dg[u][v]["length_m"] for u, v in dg.subgraph(c).edges),
        )
        for i, comp in enumerate(comps):
            sub = dg.subgraph(comp)
            path = _longest_path(sub)
            if len(path) < 2:
                continue
            pe = rg.path_edges(dg, path)
            xy = rg.edges_xy(pe)
            lons, lats = rg.edges_lonlat(pe)
            line = LineString(xy)
            s_nodes = [0.0]
            for e in pe:
                s_nodes.append(s_nodes[-1] + e.data["length_m"])
            node_s = dict(zip(path, s_nodes, strict=True))
            for n in comp:
                if n not in node_s:
                    node_s[n] = float(line.project(Point(*rg.node_xy(n))))
            ch = Chain(
                f"{route}-{direction}-{i}", route, direction, path, s_nodes, line, node_s, pe, lons, lats
            )
            for e in edges:
                if e.u in comp:
                    e.data["chain_id"] = ch.chain_id
            chains.append(ch)
    return chains


def chain_endpoints_gaps(rg: RoadGraph, chains: list[Chain], gap_m: float) -> list[dict[str, Any]]:
    """本線チェーンの端点どうしが gap_m 以内で未接続 → MAINLINE_GAP 候補。"""
    ends: list[tuple[str, str, int, float, float]] = []
    for ch in chains:
        for n, kind in ((ch.nodes[0], "start"), (ch.nodes[-1], "end")):
            x, y = rg.node_xy(n)
            ends.append((ch.chain_id, kind, n, x, y))
    out = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            a, b = ends[i], ends[j]
            if a[0] == b[0] or a[2] == b[2] or a[1] == b[1]:
                continue
            ca, cb = a[0].rsplit("-", 1)[0], b[0].rsplit("-", 1)[0]  # 同じ路線・同じ方向のチェーンどうしだけ
            if ca != cb:
                continue
            d = float(np.hypot(a[3] - b[3], a[4] - b[4]))
            if d <= gap_m and not rg.g.has_edge(a[2], b[2]) and not rg.g.has_edge(b[2], a[2]):
                out.append(
                    {"chain_a": a[0], "chain_b": b[0], "node_a": a[2], "node_b": b[2], "distance_m": d}
                )
    return out
