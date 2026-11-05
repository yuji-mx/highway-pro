"""IC・JCT・SA/PA 施設の抽出と種別判定（§4.3）。"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
from shapely.geometry import MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from roadmri.ingest.osm_pbf import OsmData
from roadmri.network.dedupe import Candidate, Group
from roadmri.network.graph import RoadGraph
from roadmri.network.names import NameNormalizer, type_from_code

_B32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lon: float, lat: float, precision: int = 7) -> str:
    lat_r, lon_r = [-90.0, 90.0], [-180.0, 180.0]
    out: list[str] = []
    bits, bit, even = 0, 0, True
    while len(out) < precision:
        rng, val = (lon_r, lon) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val >= mid:
            bits = (bits << 1) | 1
            rng[0] = mid
        else:
            bits <<= 1
            rng[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_B32[bits])
            bits, bit = 0, 0
    return "".join(out)


@dataclass
class LinkComponent:
    nodes: set[int]
    mainline_nodes: set[int]
    road_nodes: set[int]
    toll_nodes: set[int]
    junction_nodes: set[int]
    dead_ends: set[int]
    services_nodes: set[int] = field(default_factory=set)
    etc_only: bool = False
    smart_name: bool = False

    @property
    def attach_xy(self) -> np.ndarray:
        return np.zeros((0, 2))


@dataclass
class Facility:
    facility_id: str
    country: str
    name: str | None
    name_en: str | None
    normalized_name: str | None
    name_variants: list[str]
    name_code: str | None
    facility_type: str
    lon: float
    lat: float
    x: float
    y: float
    route_refs: list[str]
    exit_number: str | None
    members: list[Candidate]
    components: list[LinkComponent]
    toll_nodes: list[int]
    area_ids: list[int]
    merge_reason: list[dict[str, Any]]
    is_half_ic: bool = False
    is_jct_combined: bool = False
    parent_facility_id: str | None = None
    destinations: dict[str, list[str]] = field(default_factory=dict)
    in_range: bool = True
    admin_code: str | None = None
    osm_timestamp: Any = None
    flags: list[dict[str, Any]] = field(default_factory=list)
    hull: BaseGeometry | None = None
    rep_x: float = 0.0
    rep_y: float = 0.0

    @property
    def primary_route(self) -> str | None:
        return self.route_refs[0] if self.route_refs else None


def _mainline_attrs(rg: RoadGraph, n: int) -> tuple[str | None, str | None]:
    for e in rg.incident(n):
        if e.data["cls"] == "mainline":
            return e.data.get("route_ref"), e.data.get("direction")
    return None, None


def build_candidates(rg: RoadGraph, osm: OsmData, nn: NameNormalizer) -> list[Candidate]:
    cands: list[Candidate] = []
    for row in osm.nodes.itertuples(index=False):
        if row.kind not in ("motorway_junction", "services", "rest_area"):
            continue
        tags = dict(row.tags)
        name = tags.get("name") or tags.get("name:ja")
        norm = nn.normalize(name)
        route, direction = _mainline_attrs(rg, int(row.node_id)) if row.node_id in rg.g else (None, None)
        if row.kind == "motorway_junction" and row.node_id not in rg.g:
            continue
        x, y = rg.proj.to_xy(row.lon, row.lat)
        code = norm.code if norm else None
        if code is None and row.kind in ("services", "rest_area"):
            code = "SA" if row.kind == "services" else "PA"
        cands.append(
            Candidate(
                "node",
                int(row.node_id),
                str(row.kind),
                name,
                tags.get("name:en"),
                norm.key if norm else None,
                code,
                tags.get("ref") or tags.get("junction:ref"),
                route,
                direction,
                float(row.lon),
                float(row.lat),
                float(x),
                float(y),
                tags,
                row.timestamp,
            )
        )
    for row in osm.areas.itertuples(index=False):
        tags = dict(row.tags)
        name = tags.get("name") or tags.get("name:ja")
        norm = nn.normalize(name)
        c = row.geometry.centroid
        x, y = rg.proj.to_xy(c.x, c.y)
        code = norm.code if norm and norm.code else ("SA" if row.kind == "services" else "PA")
        key = norm.key if norm else None
        if norm and not norm.code:
            key = f"{norm.stem}|{code}"
        cands.append(
            Candidate(
                "way",
                int(row.area_id),
                str(row.kind),
                name,
                tags.get("name:en"),
                key,
                code,
                None,
                None,
                None,
                float(c.x),
                float(c.y),
                float(x),
                float(y),
                tags,
                row.timestamp,
            )
        )
    return cands


def link_components(rg: RoadGraph, osm: OsmData) -> list[LinkComponent]:
    ug = nx.Graph()
    for e in rg.edges("link"):
        ug.add_edge(e.u, e.v)
    area_polys = [rg_poly for rg_poly in _area_polys_xy(rg, osm)]
    comps: list[LinkComponent] = []
    for comp in nx.connected_components(ug):
        ml, rd, toll, junc, dead, svc = set(), set(), set(), set(), set(), set()
        etc = False
        smart = False
        for n in comp:
            classes = rg.node_classes(n)
            if "mainline" in classes:
                ml.add(n)
            if "road" in classes:
                rd.add(n)
            kind = rg.g.nodes[n].get("kind")
            if kind == "toll_booth":
                toll.add(n)
            if kind == "motorway_junction":
                junc.add(n)
            if ug.degree(n) == 1 and not ({"mainline", "road"} & classes):
                dead.add(n)
                p = Point(*rg.node_xy(n))
                if any(poly.distance(p) <= 30 for poly in area_polys):
                    svc.add(n)
        for u, v in ug.subgraph(comp).edges:
            for _k, d in (rg.g.get_edge_data(u, v) or {}).items():
                t = d["tags"]
                cond = str(t.get("motorcar:conditional", "")) + str(t.get("access", ""))
                if "ETC" in cond.upper() or "etc" in cond:
                    etc = True
                nm = str(t.get("name") or "")
                if "スマート" in nm:
                    smart = True
        if not ml:
            continue
        comps.append(LinkComponent(set(comp), ml, rd, toll, junc, dead, svc, etc, smart))
    return sorted(comps, key=lambda c: min(c.nodes))


def _area_polys_xy(rg: RoadGraph, osm: OsmData) -> list[BaseGeometry]:
    out = []
    for poly in osm.areas["geometry"] if len(osm.areas) else []:
        out.append(shp_transform(lambda x, y, z=None: rg.proj.to_xy(x, y), poly))
    return out


def _comp_xy(rg: RoadGraph, nodes: set[int]) -> np.ndarray:
    return np.array([rg.node_xy(n) for n in sorted(nodes)])


def assign_components(
    rg: RoadGraph,
    groups: list[Group],
    comps: list[LinkComponent],
    osm: OsmData,
    search_radius_m: float,
    nn: NameNormalizer,
) -> list[tuple[Group, list[LinkComponent]]]:
    """リンク連結成分を施設グループへ割り当てる。割り当て先のない成分は名称不明の施設とする。"""
    assigned: dict[int, list[LinkComponent]] = {i: [] for i in range(len(groups))}
    area_owner: dict[int, int] = {}
    polys_xy = _area_polys_xy(rg, osm)
    area_ids = list(osm.areas["area_id"]) if len(osm.areas) else []
    for gi, g in enumerate(groups):
        for m in g.members:
            if m.osm_type == "way":
                area_owner[m.osm_id] = gi
    junction_owner = {m.osm_id: gi for gi, g in enumerate(groups) for m in g.members if m.osm_type == "node"}
    leftovers: list[LinkComponent] = []
    for c in comps:
        owners = sorted({junction_owner[n] for n in c.junction_nodes if n in junction_owner})
        if not owners and c.services_nodes:
            for n in c.services_nodes:
                p = Point(*rg.node_xy(n))
                for aid, poly in zip(area_ids, polys_xy, strict=True):
                    if poly.distance(p) <= 30 and aid in area_owner:
                        owners.append(area_owner[aid])
        attach = _comp_xy(rg, c.mainline_nodes)
        if owners:
            if len(owners) > 1:
                owners.sort(key=lambda gi: _min_dist(attach, groups[gi]))
            assigned[owners[0]].append(c)
            continue
        best, best_d = None, float("inf")
        for gi, g in enumerate(groups):
            d = _min_dist(attach, g)
            if d < best_d:
                best, best_d = gi, d
        if best is not None and best_d <= search_radius_m * 0.6:
            assigned[best].append(c)
        else:
            leftovers.append(c)
    # ランプも本線上の出口ノードも持たない候補（道の駅など、高速道路に接続しない施設）は除外する
    out = []
    for i in range(len(groups)):
        on_mainline = any(m.osm_type == "node" and m.kind == "motorway_junction" for m in groups[i].members)
        if assigned[i] or on_mainline:
            out.append((groups[i], assigned[i]))
    # 割り当て先のない成分：互いに近い（1 km 以内）ものをまとめて名称不明の施設にする
    used = [False] * len(leftovers)
    for i, c in enumerate(leftovers):
        if used[i]:
            continue
        bundle = [c]
        used[i] = True
        ci = _comp_xy(rg, c.mainline_nodes).mean(axis=0)
        for j in range(i + 1, len(leftovers)):
            if used[j]:
                continue
            cj = _comp_xy(rg, leftovers[j].mainline_nodes).mean(axis=0)
            if np.hypot(*(ci - cj)) <= 1000:
                bundle.append(leftovers[j])
                used[j] = True
        name = None
        xy = np.vstack([_comp_xy(rg, b.mainline_nodes) for b in bundle]).mean(axis=0)
        lon, lat = rg.proj.to_lonlat(xy[0], xy[1])
        norm = nn.normalize(name)
        cand = Candidate(
            "node",
            -min(min(b.nodes) for b in bundle),
            "unnamed",
            name,
            None,
            norm.key if norm else None,
            norm.code if norm else None,
            None,
            None,
            None,
            float(lon),
            float(lat),
            float(xy[0]),
            float(xy[1]),
            {},
            None,
        )
        out.append((Group(members=[cand], reasons=[{"rule": "unassigned_link_component"}]), bundle))
    return out


def _min_dist(xy: np.ndarray, g: Group) -> float:
    pts = np.array([[m.x, m.y] for m in g.members])
    if len(xy) == 0 or len(pts) == 0:
        return float("inf")
    d = np.hypot(xy[:, None, 0] - pts[None, :, 0], xy[:, None, 1] - pts[None, :, 1])
    return float(d.min())


def facility_id(country: str, normalized_name: str, route: str | None) -> str:
    h = hashlib.sha1(f"{country}|{normalized_name}|{route or ''}".encode()).hexdigest()[:8]
    return f"{country}-F-{h}"


def make_facilities(
    rg: RoadGraph,
    osm: OsmData,
    assigned: list[tuple[Group, list[LinkComponent]]],
    country: str,
    nn: NameNormalizer,
    range_geom: BaseGeometry | None,
    admin_code: str | None,
) -> list[Facility]:
    facs: list[Facility] = []
    toll_names: dict[int, str] = {}
    for row in osm.nodes.itertuples(index=False):
        if row.kind == "toll_booth":
            nm = dict(row.tags).get("name")
            if nm:
                toll_names[int(row.node_id)] = nm
    for g, comps in assigned:
        names = [m.name for m in g.members if m.name]
        # 代表名：最も多い正規化名称の、最も短い表記
        keys = Counter(m.norm_key for m in g.members if m.norm_key)
        norm_key = keys.most_common(1)[0][0] if keys else None
        if not norm_key:
            for c in comps:
                for t in sorted(c.toll_nodes):
                    if t in toll_names:
                        k = nn.key(toll_names[t])
                        if k:
                            norm_key = k
                            names.append(toll_names[t])
                            break
                if norm_key:
                    break
        code = norm_key.split("|")[1] if norm_key and "|" in norm_key else None
        display = None
        if norm_key:
            stem = norm_key.split("|")[0]
            display = stem + ({"SIC": "スマートIC"}.get(code or "", code or ""))
        name_en = next((m.name_en for m in g.members if m.name_en), None)
        routes = Counter(m.route_ref for m in g.members if m.route_ref)
        attach_routes: Counter[str] = Counter()
        for c in comps:
            for n in c.mainline_nodes:
                r, _ = _mainline_attrs(rg, n)
                if r:
                    attach_routes[r] += 1
        primary = (
            routes.most_common(1)[0][0]
            if routes
            else (attach_routes.most_common(1)[0][0] if attach_routes else None)
        )
        route_refs = ([primary] if primary else []) + sorted(r for r in attach_routes if r != primary)
        pts = [(m.x, m.y) for m in g.members]
        tolls = sorted(set().union(*[c.toll_nodes for c in comps])) if comps else []
        pts += [rg.node_xy(t) for t in tolls]
        cx, cy = np.mean([p[0] for p in pts]), np.mean([p[1] for p in pts])
        rep = rg.nearest_mainline(float(cx), float(cy), primary)
        rx, ry = (rep[2].x, rep[2].y) if rep else (float(cx), float(cy))
        lon, lat = rg.proj.to_lonlat(rx, ry)
        lon_f, lat_f = float(lon), float(lat)
        exit_nums = Counter(m.exit_ref for m in g.members if m.exit_ref)
        nkey = norm_key or f"UNNAMED@{geohash(lon_f, lat_f)}"
        hull_pts = [rg.node_lonlat(n) for c in comps for n in c.nodes] + [(m.lon, m.lat) for m in g.members]
        hull = MultiPoint(hull_pts).convex_hull if hull_pts else Point(lon_f, lat_f)
        ts = [m.timestamp for m in g.members if m.timestamp is not None]
        fac = Facility(
            facility_id=facility_id(country, nkey, primary),
            country=country,
            name=display or (names[0] if names else None),
            name_en=name_en,
            normalized_name=norm_key,
            name_variants=sorted(set(names)),
            name_code=code,
            facility_type=type_from_code(code) or "IC",
            lon=lon_f,
            lat=lat_f,
            x=float(rx),
            y=float(ry),
            route_refs=route_refs,
            exit_number=exit_nums.most_common(1)[0][0] if exit_nums else None,
            members=g.members,
            components=comps,
            toll_nodes=tolls,
            area_ids=[m.osm_id for m in g.members if m.osm_type == "way"],
            merge_reason=g.reasons,
            in_range=bool(range_geom.contains(Point(lon_f, lat_f))) if range_geom is not None else True,
            osm_timestamp=max(ts) if ts else None,
            hull=hull,
            rep_x=float(rx),
            rep_y=float(ry),
        )
        fac.admin_code = admin_code if fac.in_range else None
        dest: dict[str, list[str]] = {}
        for c in comps:
            for n in c.mainline_nodes:
                for e in rg.g.out_edges(n, keys=True, data=True):
                    d = e[3]
                    if d["cls"] == "link":
                        _, direction = _mainline_attrs(rg, n)
                        dv = d["tags"].get("destination") or d["tags"].get("exit_to")
                        if dv:
                            lst = dest.setdefault(direction or "unknown", [])
                            for part in str(dv).split(";"):
                                if part and part not in lst:
                                    lst.append(part)
        fac.destinations = dest
        facs.append(fac)
    # facility_id の衝突 → geohash7 を付加
    cnt = Counter(f.facility_id for f in facs)
    for f in facs:
        if cnt[f.facility_id] > 1:
            f.facility_id = f"{f.facility_id}-{geohash(f.lon, f.lat)}"
    return sorted(facs, key=lambda f: f.facility_id)


def determine_types(facs: list[Facility], ramps_by_fac: dict[str, list[dict[str, Any]]]) -> None:
    """接続ネットワークから種別を判定し、名称と矛盾すれば TYPE_NAME_MISMATCH を立てる（§4.3.2）。"""
    for f in facs:
        ramps = ramps_by_fac.get(f.facility_id, [])
        has_road = any(c.road_nodes for c in f.components) or bool(f.toll_nodes)
        services = any(c.services_nodes for c in f.components) or bool(f.area_ids)
        other_main = [r for r in f.route_refs[1:]]
        smart = any(c.etc_only or c.smart_name for c in f.components) or f.name_code == "SIC"
        if f.name_code in ("SA", "PA") or (services and not has_road and not other_main):
            net_type = (
                f.name_code
                if f.name_code in ("SA", "PA")
                else ("SA" if any(m.kind == "services" for m in f.members) else "PA")
            )
            if smart and has_road:
                net_type = "SIC"
        elif smart and has_road:
            net_type = "SIC"
        elif has_road and other_main:
            net_type = "IC"
            f.is_jct_combined = True
        elif has_road:
            net_type = "IC"
        elif other_main:
            net_type = "JCT"
        else:
            net_type = type_from_code(f.name_code) or "IC"
        name_type = type_from_code(f.name_code)
        if (
            name_type
            and name_type != net_type
            and not (name_type == "SIC" and net_type in ("IC", "SA", "PA"))
        ):
            f.flags.append(
                {
                    "code": "TYPE_NAME_MISMATCH",
                    "severity": "warn",
                    "message_ja": f"名称上の種別（{name_type}）とネットワーク判定（{net_type}）が一致しません",
                    "detail": {"name_type": name_type, "network_type": net_type},
                }
            )
        f.facility_type = net_type
        if net_type in ("IC", "SIC") and f.primary_route:
            pr = f.primary_route
            have = {
                (r["flow"], r["carriageway"])
                for r in ramps
                if r["flow"] in ("entry", "exit") and r.get("mainline_route") == pr
            }
            dirs = {d for _, d in have if d in ("up", "down", "inner", "outer")}
            complete = all((fl, d) in have for fl in ("entry", "exit") for d in dirs) and len(dirs) >= 2
            f.is_half_ic = bool(have) and not complete
