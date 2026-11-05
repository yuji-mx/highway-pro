"""ルート解析（§4.16）。本線＋ JCT 連絡ランプのグラフで最短経路と代替経路（最大 3 本）を求める。"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice, pairwise
from typing import Any

import networkx as nx
import numpy as np

from roadmri.network.graph import EdgeRef, RoadGraph


@dataclass
class RouteResult:
    nodes: list[int]
    edges: list[EdgeRef]
    length_m: float
    xy: np.ndarray
    lons: list[float]
    lats: list[float]
    passed_nodes: list[int] = field(default_factory=list)


def routing_graph(rg: RoadGraph, jct_link_keys: set[str]) -> nx.DiGraph:
    dg = nx.DiGraph()
    for e in rg.edges({"mainline", "link"}):
        if e.data["cls"] == "link" and e.key not in jct_link_keys:
            continue
        if not dg.has_edge(e.u, e.v) or dg[e.u][e.v]["length_m"] > e.data["length_m"]:
            dg.add_edge(e.u, e.v, length_m=e.data["length_m"], key=e.key)
    return dg


def snap_candidates(rg: RoadGraph, dg: nx.DiGraph, lon: float, lat: float, k: int = 8) -> list[int]:
    """最寄りの本線ノード候補（上下線の両方を含むよう近い順に k 個）。"""
    x, y = rg.proj.to_xy(lon, lat)
    nodes = list(dg.nodes)
    if not nodes:
        raise ValueError("経路探索用グラフが空です")
    d = [((rg.node_xy(n)[0] - x) ** 2 + (rg.node_xy(n)[1] - y) ** 2, n) for n in nodes]
    d.sort()
    return [int(n) for _, n in d[:k]]


def snap_to_node(rg: RoadGraph, dg: nx.DiGraph, lon: float, lat: float) -> int:
    return snap_candidates(rg, dg, lon, lat, 1)[0]


def best_pair(dg: nx.DiGraph, srcs: list[int], dsts: list[int]) -> tuple[int, int] | None:
    best: tuple[float, int, int] | None = None
    for a in srcs:
        try:
            dist = nx.single_source_dijkstra_path_length(dg, a, weight="length_m")
        except nx.NodeNotFound:
            continue
        for b in dsts:
            if b in dist and b != a and (best is None or dist[b] < best[0]):
                best = (dist[b], a, b)
    return (best[1], best[2]) if best else None


def overlap(a: list[EdgeRef], b: list[EdgeRef]) -> float:
    la = {e.key: e.data["length_m"] for e in a}
    lb = {e.key for e in b}
    tot = sum(la.values())
    return sum(v for k, v in la.items() if k in lb) / tot if tot > 0 else 1.0


def find_routes(
    rg: RoadGraph,
    dg: nx.DiGraph,
    src: int,
    dst: int,
    via: list[int] | None = None,
    max_alternatives: int = 3,
    max_overlap: float = 0.7,
    candidates: int = 20,
) -> list[RouteResult]:
    stops = [src, *(via or []), dst]
    results: list[RouteResult] = []
    if len(stops) == 2:
        try:
            gen = nx.shortest_simple_paths(dg, src, dst, weight="length_m")
            paths = list(islice(gen, candidates))
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
    else:
        try:
            path: list[int] = [src]
            for a, b in pairwise(stops):
                path += nx.shortest_path(dg, a, b, weight="length_m")[1:]
            paths = [path]
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []
    for p in paths:
        edges = rg.path_edges(dg, p)
        if any(overlap(edges, r.edges) >= max_overlap for r in results):
            continue
        lons, lats = rg.edges_lonlat(edges)
        results.append(
            RouteResult(
                p, edges, float(sum(e.data["length_m"] for e in edges)), rg.edges_xy(edges), lons, lats
            )
        )
        if len(results) >= max_alternatives:
            break
    return results


def summarize(route: RouteResult) -> dict[str, Any]:
    tunnel = sum(e.data["length_m"] for e in route.edges if e.data["structure"] == "tunnel")
    bridge = sum(e.data["length_m"] for e in route.edges if e.data["structure"] in ("bridge", "viaduct"))
    return {
        "length_m": route.length_m,
        "tunnel_len_m": tunnel,
        "bridge_len_m": bridge,
        "edge_count": len(route.edges),
    }
