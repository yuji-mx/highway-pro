"""論理ランプの抽出・ID 付与・方向分類（§4.4）。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np

from roadmri.geometry.projection import bearing_deg
from roadmri.network.graph import EdgeRef, RoadGraph
from roadmri.network.junctions import Facility, LinkComponent
from roadmri.units import parse_speed_kmh


@dataclass
class Ramp:
    ramp_id: str
    facility_id: str
    seq: int
    edges: list[EdgeRef]
    nodes: list[int]
    flow: str
    carriageway: str
    branch_side: str | None
    merge_diverge: str | None
    from_ref: dict[str, Any]
    to_ref: dict[str, Any]
    length_m: float
    is_loop: bool
    lanes: int | None
    maxspeed_kmh: int | None
    turn_deg: float
    related_facility_ids: list[str] = field(default_factory=list)
    mainline_route: str | None = None
    two_way: bool = False

    @property
    def way_ids(self) -> list[int]:
        out: list[int] = []
        for e in self.edges:
            if not out or out[-1] != e.data["way_id"]:
                out.append(e.data["way_id"])
        return out


def _terminal(rg: RoadGraph, n: int, comp: LinkComponent) -> dict[str, Any]:
    kinds = {e.data["cls"] for e in rg.incident(n)}
    if n in comp.toll_nodes:
        return {"kind": "toll", "node": n}
    if "mainline" in kinds:
        ml = [e for e in rg.incident(n) if e.data["cls"] == "mainline"]
        return {
            "kind": "mainline",
            "node": n,
            "route_ref": ml[0].data.get("route_ref"),
            "direction": ml[0].data.get("direction"),
            "name": ml[0].data.get("name"),
        }
    if "road" in kinds:
        rd = [e for e in rg.incident(n) if e.data["cls"] == "road"]
        return {
            "kind": "road",
            "node": n,
            "route_ref": rd[0].data.get("ref"),
            "direction": None,
            "name": rd[0].data.get("name"),
        }
    if n in comp.services_nodes:
        return {"kind": "services", "node": n}
    if n in comp.dead_ends:
        return {"kind": "dead_end", "node": n}
    return {"kind": "link", "node": n}


def _parse_int(v: Any) -> int | None:
    try:
        return int(str(v).split(";")[0].split()[0])
    except (ValueError, IndexError):
        return None


def _walk_ramps(rg: RoadGraph, comp: LinkComponent) -> list[list[EdgeRef]]:
    sub = nx.MultiDiGraph()
    for u, v, k, d in rg.g.edges(keys=True, data=True):
        if d["cls"] == "link" and u in comp.nodes and v in comp.nodes:
            sub.add_edge(u, v, key=k)
    und = nx.Graph(sub.to_undirected())

    def interior(n: int) -> bool:
        if n in comp.toll_nodes or n in comp.mainline_nodes or n in comp.road_nodes:
            return False
        if und.degree(n) != 2:
            return False
        return bool(sub.in_degree(n) == sub.out_degree(n))

    chains: list[list[EdgeRef]] = []
    seen: set[str] = set()
    for b in sorted(sub.nodes):
        if interior(b):
            continue
        for _, v, k in sorted(sub.out_edges(b, keys=True), key=lambda t: t[2]):
            if k in seen:
                continue
            path = [EdgeRef(b, v, k, rg.g.edges[b, v, k])]
            seen.add(k)
            prev, cur = b, v
            guard = 0
            while interior(cur) and guard < 10000:
                guard += 1
                nxt = [
                    (cur, w, kk) for _, w, kk in sub.out_edges(cur, keys=True) if w != prev and kk not in seen
                ]
                if not nxt:
                    break
                _, w, kk = sorted(nxt, key=lambda t: t[2])[0]
                path.append(EdgeRef(cur, w, kk, rg.g.edges[cur, w, kk]))
                seen.add(kk)
                prev, cur = cur, w
            chains.append(path)
    return chains


def _reach(sub: nx.DiGraph, start: int, forward: bool, stop: set[int]) -> set[int]:
    seen = {start}
    stack = [start]
    while stack:
        n = stack.pop()
        if n in stop and n != start:
            continue
        nbrs = sub.successors(n) if forward else sub.predecessors(n)
        for m in nbrs:
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return seen


def _side(rg: RoadGraph, node: int, ramp_xy: np.ndarray, at_start: bool) -> str | None:
    ml_in = [e for e in rg.g.in_edges(node, keys=True, data=True) if e[3]["cls"] == "mainline"]
    ml_out = [e for e in rg.g.out_edges(node, keys=True, data=True) if e[3]["cls"] == "mainline"]
    nx_, ny_ = rg.node_xy(node)
    if ml_in:
        xy = ml_in[0][3]["xy"]
        d = np.array([nx_, ny_]) - xy[max(0, len(xy) - 2)]
    elif ml_out:
        xy = ml_out[0][3]["xy"]
        d = xy[min(1, len(xy) - 1)] - np.array([nx_, ny_])
    else:
        return None
    s = np.hypot(*np.diff(ramp_xy, axis=0).T).cumsum() if len(ramp_xy) > 1 else np.zeros(0)
    idx = int(np.searchsorted(s, 20.0)) + 1 if len(s) else 0
    p = ramp_xy[min(idx, len(ramp_xy) - 1)] if at_start else ramp_xy[max(0, len(ramp_xy) - 1 - idx)]
    v = p - np.array([nx_, ny_])
    cross = d[0] * v[1] - d[1] * v[0]
    if abs(cross) < 1e-9:
        return None
    return "left" if cross > 0 else "right"


def _turn_deg(xy: np.ndarray) -> float:
    if len(xy) < 3:
        return 0.0
    d = np.diff(xy, axis=0)
    keep = np.hypot(d[:, 0], d[:, 1]) > 1e-6
    th = np.unwrap(np.arctan2(d[keep, 1], d[keep, 0]))
    return float(np.degrees(np.ptp(th))) if len(th) else 0.0


def extract_ramps(rg: RoadGraph, facs: list[Facility], loop_turn_deg: float) -> list[Ramp]:
    out: list[Ramp] = []
    for f in facs:
        fac_ramps: list[Ramp] = []
        for comp in f.components:
            sub = nx.DiGraph()
            for u, v, _k, d in rg.g.edges(keys=True, data=True):
                if d["cls"] == "link" and u in comp.nodes and v in comp.nodes:
                    sub.add_edge(u, v)
            terminals = comp.mainline_nodes | comp.road_nodes | comp.dead_ends
            chains = _walk_ramps(rg, comp)
            # 双方向の重複（逆向き）を除く
            canon: dict[tuple[frozenset[str], ...], list[EdgeRef]] = {}
            for ch in chains:
                key = (frozenset(e.key.rstrip("r") for e in ch),)
                two_way = all(not e.data["oneway"] for e in ch)
                if key in canon and two_way:
                    if ch[0].u < canon[key][0].u:
                        canon[key] = ch
                    continue
                canon[key if two_way else (frozenset(e.key for e in ch),)] = ch
            for ch in canon.values():
                nodes = [ch[0].u] + [e.v for e in ch]
                xy = rg.edges_xy(ch)
                length = float(sum(e.data["length_m"] for e in ch))
                t0 = _terminal(rg, nodes[0], comp)
                t1 = _terminal(rg, nodes[-1], comp)
                two_way = all(not e.data["oneway"] for e in ch)
                up = _reach(sub, nodes[0], False, terminals | comp.toll_nodes) | {nodes[0]}
                down = _reach(sub, nodes[-1], True, terminals | comp.toll_nodes) | {nodes[-1]}
                up_k = [_terminal(rg, n, comp) for n in up]
                down_k = [_terminal(rg, n, comp) for n in down]
                up_main = {t["route_ref"] for t in up_k if t["kind"] == "mainline"}
                down_main = {t["route_ref"] for t in down_k if t["kind"] == "mainline"}
                up_road = any(t["kind"] in ("road", "toll") for t in up_k)
                down_road = any(t["kind"] in ("road", "toll") for t in down_k)
                svc = any(t["kind"] == "services" for t in up_k + down_k)
                own_toll = t0["kind"] == "toll" or t1["kind"] == "toll"
                if two_way:
                    flow = "two_way"
                elif (
                    own_toll
                    and not (t0["kind"] == "mainline" or t1["kind"] == "mainline")
                    and not (up_main and t0["kind"] != "toll")
                    and not (down_main and t1["kind"] != "toll")
                ):
                    flow = "toll_link"
                elif (svc and f.facility_type in ("SA", "PA")) or (svc and not up_road and not down_road):
                    flow = "facility_link"
                elif up_main and down_road and t1["kind"] != "mainline":
                    flow = "exit"
                elif down_main and up_road and t0["kind"] != "mainline":
                    flow = "entry"
                elif up_main and down_main:
                    flow = (
                        "jct_link"
                        if up_main != down_main or len(up_main | down_main) > 1
                        else "collector_distributor"
                    )
                elif up_main:
                    flow = "exit"
                elif down_main:
                    flow = "entry"
                else:
                    flow = "toll_link" if comp.toll_nodes else "collector_distributor"
                # 接続本線（carriageway）
                ml_term = t0 if t0["kind"] == "mainline" else (t1 if t1["kind"] == "mainline" else None)
                if ml_term is None:
                    cand = [
                        t
                        for t in (up_k if flow in ("exit", "jct_link") else down_k)
                        if t["kind"] == "mainline"
                    ]
                    ml_term = cand[0] if cand else None
                carriageway = (ml_term or {}).get("direction") or "unknown"
                md = None
                if t0["kind"] == "mainline" and t1["kind"] == "mainline":
                    md = "both"
                elif t0["kind"] == "mainline":
                    md = "diverge"
                elif t1["kind"] == "mainline":
                    md = "merge"
                side = None
                if t0["kind"] == "mainline":
                    side = _side(rg, nodes[0], xy, True)
                elif t1["kind"] == "mainline":
                    side = _side(rg, nodes[-1], xy, False)
                lanes = Counter(_parse_int(e.data["tags"].get("lanes")) for e in ch).most_common(1)[0][0]
                maxspeed = Counter(parse_speed_kmh(e.data["tags"].get("maxspeed")) for e in ch).most_common(
                    1
                )[0][0]
                turn = _turn_deg(xy)
                fac_ramps.append(
                    Ramp(
                        ramp_id="",
                        facility_id=f.facility_id,
                        seq=0,
                        edges=ch,
                        nodes=nodes,
                        flow=flow,
                        carriageway=carriageway,
                        branch_side=side,
                        merge_diverge=md,
                        from_ref={k: v for k, v in t0.items() if k != "node"},
                        to_ref={k: v for k, v in t1.items() if k != "node"},
                        length_m=length,
                        is_loop=turn >= loop_turn_deg,
                        lanes=lanes,
                        maxspeed_kmh=maxspeed,
                        turn_deg=turn,
                        related_facility_ids=[f.facility_id],
                        mainline_route=(ml_term or {}).get("route_ref"),
                        two_way=two_way,
                    )
                )

        # 決定的な番号付け：施設代表点からランプ始点への方位角の昇順、同じなら長さの降順
        def sort_key(r: Ramp, f: Facility = f) -> tuple[float, float, int]:
            lo, la = rg.node_lonlat(r.nodes[0])
            return (round(bearing_deg(f.lon, f.lat, lo, la), 6), -round(r.length_m, 3), r.nodes[0])

        fac_ramps.sort(key=sort_key)
        for i, r in enumerate(fac_ramps, start=1):
            r.seq = i
            r.ramp_id = f"{f.facility_id}-RAMP-{i:03d}"
        out.extend(fac_ramps)
    return out


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def inherit_ids(new: list[Ramp], old: dict[str, set[int]], threshold: float = 0.5) -> dict[str, str]:
    """構成 Way ID 集合の Jaccard ≥ threshold で旧 ID を継承する（§4.4.2, §4.18）。新 ID → 旧 ID。"""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for r in new:
        ws = set(r.way_ids)
        best, best_j = None, 0.0
        for oid, ows in old.items():
            if oid in used:
                continue
            j = jaccard(ws, ows)
            if j > best_j:
                best, best_j = oid, j
        if best is not None and best_j >= threshold:
            mapping[r.ramp_id] = best
            used.add(best)
    return mapping
