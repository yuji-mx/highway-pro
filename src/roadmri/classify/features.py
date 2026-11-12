"""形状分類の特徴量（§4.9.3）。"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np

from roadmri.network.graph import RoadGraph
from roadmri.network.junctions import Facility
from roadmri.network.mainline import Chain
from roadmri.network.ramps import Ramp


def _cluster(points: list[tuple[float, float]], radius: float) -> int:
    clusters: list[tuple[float, float]] = []
    for p in points:
        if not any(math.hypot(p[0] - c[0], p[1] - c[1]) <= radius for c in clusters):
            clusters.append(p)
    return len(clusters)


def _route_heading(rg: RoadGraph, f: Facility, route: str) -> float | None:
    best = rg.nearest_mainline(f.x, f.y, route)
    if best is None:
        return None
    xy = best[0].data["xy"]
    d = xy[-1] - xy[0]
    return math.degrees(math.atan2(d[1], d[0]))


def facility_features(
    rg: RoadGraph, f: Facility, ramps: list[Ramp], chains: list[Chain], terminate_radius_m: float = 1500.0
) -> dict[str, Any]:
    main_ramps = [r for r in ramps if r.merge_diverge is not None]
    # 料金所の外側（一般道側）の連絡路は IC 本体の形状に含めない
    loops = [r for r in ramps if r.is_loop and r.flow not in ("toll_link", "two_way")]
    road_nodes = sorted({n for c in f.components for n in c.road_nodes})
    road_pts = [rg.node_xy(n) for n in road_nodes]
    road_names = set()
    for n in road_nodes:
        for e in rg.incident(n):
            if e.data["cls"] == "road":
                road_names.add(e.data.get("ref") or e.data.get("name") or f"way{e.data['way_id']}")
    directions = {
        (r.from_ref.get("route_ref"), r.from_ref.get("direction"))
        for r in ramps
        if r.from_ref.get("kind") == "mainline"
    }
    directions |= {
        (r.to_ref.get("route_ref"), r.to_ref.get("direction"))
        for r in ramps
        if r.to_ref.get("kind") == "mainline"
    }
    layers: set[int] = set()
    bridges = 0
    for r in ramps:
        for e in r.edges:
            layers.add(int(e.data["layer"]))
            if e.data["structure"] in ("bridge", "viaduct"):
                bridges += 1
    for n in {n for c in f.components for n in c.mainline_nodes}:
        for e in rg.incident(n):
            if e.data["cls"] == "mainline":
                layers.add(int(e.data["layer"]))
    toll_general = False
    if f.toll_nodes:
        ml_pts = [rg.node_xy(n) for c in f.components for n in c.mainline_nodes]
        for t in f.toll_nodes:
            tp = rg.node_xy(t)
            d_road = min((math.hypot(tp[0] - p[0], tp[1] - p[1]) for p in road_pts), default=float("inf"))
            d_main = min((math.hypot(tp[0] - p[0], tp[1] - p[1]) for p in ml_pts), default=float("inf"))
            if d_road < d_main:
                toll_general = True
    # 終端する路線（Y・T 型の判定）
    terminating = 0
    for ch in chains:
        if ch.route_ref not in f.route_refs:
            continue
        for n in (ch.nodes[0], ch.nodes[-1]):
            x, y = rg.node_xy(n)
            if math.hypot(x - f.x, y - f.y) <= terminate_radius_m:
                terminating += 1
    angle = None
    if len(f.route_refs) >= 2:
        h = [_route_heading(rg, f, r) for r in f.route_refs[:2]]
        if h[0] is not None and h[1] is not None:
            a = abs((h[0] - h[1] + 180) % 360 - 180)
            angle = min(a, 180 - a)
    sweeping = [r for r in ramps if r.flow == "jct_link" and 60 <= r.turn_deg < 200]
    turn_signs: Counter[str] = Counter()
    for r in ramps:
        xy = rg.edges_xy(r.edges)
        if len(xy) >= 3:
            d = np.diff(xy, axis=0)
            th = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
            turn_signs["left" if th[-1] - th[0] > 0 else "right"] += 1
    flows = Counter(r.flow for r in ramps)
    degree: Counter[str] = Counter()
    for c in f.components:
        for n in c.nodes:
            degree[str(rg.g.degree(n))] += 1
    order = sorted(
        [(r.from_ref.get("direction"), "diverge") for r in ramps if r.merge_diverge in ("diverge", "both")]
        + [(r.to_ref.get("direction"), "merge") for r in ramps if r.merge_diverge in ("merge", "both")],
        key=str,
    )
    return {
        "facility_type": f.facility_type,
        "mainline_count": len(set(f.route_refs)),
        "connection_directions": len(directions),
        "ramp_count": len(main_ramps),
        "logical_ramp_count": len(ramps),
        "loop_ramp_count": len(loops),
        # 料金所がある場合は全交通が料金所を通るため、料金所の位置（150 m でまとめる）を一般道接続点とみなす
        "general_road_connections": (
            _cluster([rg.node_xy(t) for t in f.toll_nodes], 150.0)
            if f.toll_nodes
            else _cluster(road_pts, 150.0)
        ),
        "crossing_road_count": len(road_names),
        "has_general_road": bool(road_pts),
        "toll_present": bool(f.toll_nodes),
        "toll_on_general_side": toll_general,
        "layer_count": len(layers),
        "bridge_ramp_edges": bridges,
        "terminating_routes": terminating,
        "mainline_angle_deg": angle,
        "sweeping_jct_ramps": len(sweeping),
        "jct_link_count": flows.get("jct_link", 0),
        "entry_count": flows.get("entry", 0),
        "exit_count": flows.get("exit", 0),
        "left_turn_ramps": turn_signs.get("left", 0),
        "right_turn_ramps": turn_signs.get("right", 0),
        "is_half_ic": f.is_half_ic,
        "is_smart": f.facility_type == "SIC",
        "is_jct_combined": f.is_jct_combined,
        "degree_distribution": dict(sorted(degree.items())),
        "ramp_connection_order": [f"{d}:{k}" for d, k in order],
        "turn_deg": [round(r.turn_deg, 1) for r in ramps],
        "closed_loop": len(loops) > 0,
    }
