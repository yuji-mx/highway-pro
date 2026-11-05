"""合成ネットワーク（テスト・デモ用）。典型形状（トランペット、ダイヤモンド、クローバーリーフ、タービン、Y型、
SA、スマートIC、ハーフIC）を、OSM と同じタグ体系で生成する。座標は局所平面（m）→ WGS84 の簡易変換。"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from roadmri.ingest.osm_pbf import OsmData

LON0, LAT0 = 139.85, 36.55
M_PER_DEG_LAT = 110_950.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))
TS = datetime(2026, 10, 1, tzinfo=UTC)


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    return LON0 + x / M_PER_DEG_LON, LAT0 + y / M_PER_DEG_LAT


def synthetic_dem(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """合成 DEM：南→北に緩く上る地形＋起伏。"""
    x = (np.asarray(lon) - LON0) * M_PER_DEG_LON
    y = (np.asarray(lat) - LAT0) * M_PER_DEG_LAT
    z = 120.0 + 0.004 * y + 18.0 * np.sin(x / 900.0) * np.cos(y / 1300.0) + 6.0 * np.sin(y / 400.0)
    # 交差道路側が低い谷地形（流入ランプが上り坂になる）：クローバー IC とトランペット IC
    for cx, cy, depth, sig in ((0.0, 2000.0, 45.0, 300.0), (-300.0, -10000.0, 30.0, 350.0)):
        z = z - depth * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sig**2))
    return z


def hermite(
    p0: tuple[float, float], h0: float, p1: tuple[float, float], h1: float, n: int = 40, k: float = 1.0
) -> list[tuple[float, float]]:
    """端点・端点方位（rad、x 軸から反時計回り）を満たす 3 次エルミート曲線。"""
    d = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) * k
    t0 = (math.cos(h0) * d, math.sin(h0) * d)
    t1 = (math.cos(h1) * d, math.sin(h1) * d)
    out = []
    for i in range(n + 1):
        t = i / n
        a, b, c, e = 2 * t**3 - 3 * t**2 + 1, t**3 - 2 * t**2 + t, -2 * t**3 + 3 * t**2, t**3 - t**2
        out.append(
            (a * p0[0] + b * t0[0] + c * p1[0] + e * t1[0], a * p0[1] + b * t0[1] + c * p1[1] + e * t1[1])
        )
    return out


def arc(
    center: tuple[float, float], r: float, th0: float, sweep: float, n: int = 60
) -> list[tuple[float, float]]:
    return [
        (center[0] + r * math.cos(th0 + sweep * i / n), center[1] + r * math.sin(th0 + sweep * i / n))
        for i in range(n + 1)
    ]


N, E, S, W = math.pi / 2, 0.0, -math.pi / 2, math.pi


@dataclass
class Builder:
    nodes: dict[tuple[int, int], int] = field(default_factory=dict)
    coords: dict[int, tuple[float, float]] = field(default_factory=dict)
    node_tags: dict[int, dict[str, str]] = field(default_factory=dict)
    ways: list[dict[str, Any]] = field(default_factory=list)
    areas: list[dict[str, Any]] = field(default_factory=list)
    attach: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    next_node: int = 1000
    next_way: int = 5000

    def node(self, x: float, y: float) -> int:
        key = (round(x * 100), round(y * 100))
        if key not in self.nodes:
            self.nodes[key] = self.next_node
            self.coords[self.next_node] = (x, y)
            self.next_node += 1
        return self.nodes[key]

    def way(self, pts: list[tuple[float, float]], tags: dict[str, str]) -> int:
        ids: list[int] = []
        for p in pts:
            nid = self.node(*p)
            if not ids or ids[-1] != nid:
                ids.append(nid)
        wid = self.next_way
        self.next_way += 1
        self.ways.append({"way_id": wid, "nodes": ids, "tags": tags})
        return wid

    def link(
        self,
        pts: list[tuple[float, float]],
        line: str,
        carriage: str,
        at_start: bool,
        end_line: str | None = None,
        end_at_start: bool = False,
        **tags: Any,
    ) -> None:
        """ランプを追加し、始点・終点を接続先の線（本線・一般道）の取付点として登録する。"""
        self.attach.setdefault(line, []).append(pts[0] if at_start else pts[-1])
        if end_line:
            self.attach.setdefault(end_line, []).append(pts[-1] if not end_at_start else pts[0])
        self.way(pts, {"highway": "motorway_link", "oneway": "yes", **tags})


def _line(
    b: Builder,
    key: str,
    p0: tuple[float, float],
    p1: tuple[float, float],
    tags: dict[str, str],
    step: float = 200.0,
    splits: list[tuple[float, float, dict[str, str]]] | None = None,
) -> None:
    """直線（p0→p1）上に取付点と等間隔点を並べ、構造物区間で Way を分割して追加する。"""
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
    ts = set(np.arange(0, L, step).tolist()) | {L}
    for ax, ay in b.attach.get(key, []):
        t = (ax - p0[0]) * ux + (ay - p0[1]) * uy
        if -1 <= t <= L + 1:
            ts.add(round(t, 2))
    cuts: list[tuple[float, float, dict[str, str]]] = sorted(splits or [])
    for t0, t1, _ in cuts:
        ts.update([t0, t1])
    tl = sorted(ts)
    bounds = sorted(
        {0.0, L, *[c for t0, t1, _ in cuts for c in (t0, t1)]} | set(np.arange(0, L, 2000.0).tolist())
    )
    for a, z in itertools.pairwise(bounds):
        seg = [t for t in tl if a - 1e-6 <= t <= z + 1e-6]
        if len(seg) < 2:
            continue
        extra = next((tg for t0, t1, tg in cuts if t0 <= a + 1e-6 and z <= t1 + 1e-6), {})
        b.way([(p0[0] + ux * t, p0[1] + uy * t) for t in seg], {**tags, **extra})


def build_synthetic() -> Builder:
    b = Builder()
    xs = {"NB": -15.0, "SB": 15.0}  # 左側通行：北行きは西側
    jn = 0

    def junction(x: float, y: float, name: str, ref: str | None = None) -> None:
        nonlocal jn
        jn += 1
        tags = {"highway": "motorway_junction", "name": name}
        if ref:
            tags["ref"] = ref
        b.node_tags[b.node(x, y)] = tags

    # ---- 1) トランペット IC（y=-10000、西側に一般道、一般道側に料金所）
    Y = -10000.0
    J = (-300.0, Y)
    b.link(hermite((xs["NB"], Y - 450), N, J, W), "SYN1-NB", "NB", True)  # 北行き流出
    b.link(hermite(J, E, (xs["NB"], Y + 450), N), "SYN1-NB", "NB", False)  # 北行き流入
    c = (210.0, Y + 150)
    loop = arc(c, 60, math.pi, 1.5 * math.pi)  # 反時計回り 270°（南向き→西向き）
    pre = hermite((xs["SB"], Y + 450), S, loop[0], S, 20)
    post = [loop[-1], (60.0, Y + 210), (-60.0, Y + 210)]
    b.link(pre + loop[1:] + post[1:2], "SYN1-SB", "SB", True)  # 南行き流出（ループ）
    b.way([post[1], post[2]], {"highway": "motorway_link", "oneway": "yes", "bridge": "yes", "layer": "1"})
    b.way(hermite(post[2], W, J, W, 20), {"highway": "motorway_link", "oneway": "yes"})
    b.way(hermite(J, E, (-60.0, Y - 120), E, 15), {"highway": "motorway_link", "oneway": "yes"})
    b.way(
        [(-60.0, Y - 120), (60.0, Y - 150)],
        {"highway": "motorway_link", "oneway": "yes", "bridge": "yes", "layer": "1"},
    )
    b.link(hermite((60.0, Y - 150), E, (xs["SB"], Y - 500), S, 25), "SYN1-SB", "SB", False)  # 南行き流入
    toll = (-500.0, Y)
    b.way([J, toll], {"highway": "motorway_link", "oneway": "no"})
    b.way([toll, (-800.0, Y)], {"highway": "motorway_link", "oneway": "no"})
    b.node_tags[b.node(*toll)] = {"barrier": "toll_booth", "name": "合成トランペット料金所"}
    b.attach.setdefault("ROAD-T", []).append((-800.0, Y))
    junction(xs["NB"], Y - 450, "合成トランペットIC", "1")
    junction(xs["SB"], Y + 450, "合成トランペットIC", "1")
    _line(b, "ROAD-T", (-4000.0, Y), (-800.0, Y), {"highway": "primary", "name": "合成県道1号", "ref": "1"})

    # ---- 2) ハーフ IC（y=-7000、北行き流出と南行き流入のみ）
    Y = -7000.0
    b.link(hermite((xs["NB"], Y - 400), N, (-250.0, Y), N), "SYN1-NB", "NB", True, "ROAD-H")
    b.link(hermite((250.0, Y), S, (xs["SB"], Y - 400), S), "ROAD-H", "SB", True, "SYN1-SB")
    junction(xs["NB"], Y - 400, "合成ハーフIC", "2")
    _line(
        b,
        "ROAD-H",
        (-2500.0, Y),
        (2500.0, Y),
        {"highway": "secondary", "name": "合成市道ハーフ線"},
        splits=[(2440.0, 2560.0, {"bridge": "yes", "layer": "1"})],
    )

    # ---- 3) ダイヤモンド IC（y=-4000、交差道路は本線を跨ぐ）
    Y = -4000.0
    b.link(hermite((xs["NB"], Y - 450), N, (-250.0, Y), N), "SYN1-NB", "NB", True, "ROAD-D")
    b.link(hermite((-250.0, Y), N, (xs["NB"], Y + 450), N), "ROAD-D", "NB", True, "SYN1-NB")
    b.link(hermite((xs["SB"], Y + 450), S, (250.0, Y), S), "SYN1-SB", "SB", True, "ROAD-D")
    b.link(hermite((250.0, Y), S, (xs["SB"], Y - 450), S), "ROAD-D", "SB", True, "SYN1-SB")
    junction(xs["NB"], Y - 450, "合成ダイヤモンドIC", "3")
    junction(xs["SB"], Y + 450, "合成ダイヤモンドIC", "3")
    _line(
        b,
        "ROAD-D",
        (-3000.0, Y),
        (3000.0, Y),
        {"highway": "primary", "name": "合成国道2号", "ref": "2"},
        splits=[(2940.0, 3060.0, {"bridge": "yes", "layer": "1"})],
    )

    # ---- 4) クローバーリーフ IC（y=2000、4 ループ＋ 4 直結ランプ）
    Y = 2000.0

    def mirror_rev(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [(x, 2 * Y - y) for x, y in pts][::-1]

    for carriage, sx, heading in (("NB", -1, N), ("SB", 1, S)):
        before = -1 if carriage == "NB" else 1  # 交差道路の手前側（y の符号）
        # 直結流出：本線（手前）→ 交差道路の外側
        direct = hermite((xs[carriage], Y + before * 520.0), heading, (sx * 520.0, Y), heading)
        # ループ流出：手前側の反対象限（本線をはさんだ反対側）で 270° 旋回して交差道路へ
        center = (-sx * 115.0, Y + before * 115.0)
        th0 = math.atan2(0.0, sx)
        # 始点の接線が本線の進行方向と一致する回転向きを選ぶ
        tx, ty = -math.sin(th0), math.cos(th0)
        orient = 1 if (tx * math.cos(heading) + ty * math.sin(heading)) > 0 else -1
        best = (0.0, arc(center, 70.0, th0, orient * 1.5 * math.pi))
        loop = best[1]
        hx = loop[-1][0] - loop[-2][0]
        pre = hermite((xs[carriage], Y + before * 420.0), heading, loop[0], heading, 20)
        road_pt = (loop[-1][0] + math.copysign(80.0, hx), Y)
        post = hermite(
            loop[-1], math.atan2(loop[-1][1] - loop[-2][1], hx), road_pt, 0.0 if hx > 0 else math.pi, 10
        )
        loop_exit = pre + loop[1:] + post[1:]
        b.link(direct, f"SYN1-{carriage}", carriage, True, "ROAD-C")
        b.link(loop_exit, f"SYN1-{carriage}", carriage, True, "ROAD-C")
        # 流入は交差道路の向こう側に鏡映（直結・ループ）
        b.link(mirror_rev(direct), "ROAD-C", carriage, True, f"SYN1-{carriage}")
        b.link(mirror_rev(loop_exit), "ROAD-C", carriage, True, f"SYN1-{carriage}")
    junction(xs["NB"], Y - 520, "合成クローバーIC", "4")
    junction(xs["SB"], Y + 520, "合成クローバーIC", "4")
    _line(
        b,
        "ROAD-C",
        (-3000.0, Y),
        (3000.0, Y),
        {"highway": "primary", "name": "合成国道3号", "ref": "3"},
        splits=[(2940.0, 3060.0, {"bridge": "yes", "layer": "1"})],
    )

    # ---- 5) SA（y=6000、上下線それぞれに休憩施設）
    Y = 6000.0
    for carriage, sx, heading in (("NB", -1, N), ("SB", 1, S)):
        sign = 1 if carriage == "NB" else -1
        a = (xs[carriage], Y - sign * 500)
        p_in = (sx * 160.0, Y - sign * 120)
        p_out = (sx * 160.0, Y + sign * 120)
        z = (xs[carriage], Y + sign * 500)
        b.link(hermite(a, heading, p_in, heading), f"SYN1-{carriage}", carriage, True)
        b.link(hermite(p_out, heading, z, heading), f"SYN1-{carriage}", carriage, False)
        poly = Polygon([(sx * 120, Y - 200), (sx * 300, Y - 200), (sx * 300, Y + 200), (sx * 120, Y + 200)])
        b.areas.append(
            {"kind": "services", "name": f"合成SA（{'下り' if carriage == 'NB' else '上り'}）", "poly": poly}
        )
        junction(*a, f"合成SA（{'下り' if carriage == 'NB' else '上り'}）")

    # ---- 6) スマート IC（y=9000、ETC 専用）
    Y = 9000.0
    etc: dict[str, Any] = {"access": "no", "motorcar:conditional": "yes @ ETC"}
    b.link(hermite((xs["NB"], Y - 350), N, (-220.0, Y), N), "SYN1-NB", "NB", True, "ROAD-S", **etc)
    b.link(hermite((-220.0, Y), N, (xs["NB"], Y + 350), N), "ROAD-S", "NB", True, "SYN1-NB", **etc)
    b.link(hermite((xs["SB"], Y + 350), S, (220.0, Y), S), "SYN1-SB", "SB", True, "ROAD-S", **etc)
    b.link(hermite((220.0, Y), S, (xs["SB"], Y - 350), S), "ROAD-S", "SB", True, "SYN1-SB", **etc)
    junction(xs["NB"], Y - 350, "合成スマートIC", "5-1")
    junction(xs["SB"], Y + 350, "合成スマートIC", "5-1")
    _line(
        b,
        "ROAD-S",
        (-2000.0, Y),
        (2000.0, Y),
        {"highway": "tertiary", "name": "合成市道スマート線"},
        splits=[(1940.0, 2060.0, {"bridge": "yes", "layer": "1"})],
    )

    # ---- 7) タービン JCT（y=14000、SYN2 と立体交差。ループなし）
    Y = 14000.0
    e2 = {"EB": Y + 15.0, "WB": Y - 15.0}  # 東行きは北側
    J2 = [
        ((xs["NB"], Y - 700), N, (-700.0, e2["WB"]), W, "SYN1-NB", "SYN2-WB", "0"),  # 北→西（左折）
        ((xs["SB"], Y + 700), S, (700.0, e2["EB"]), E, "SYN1-SB", "SYN2-EB", "0"),  # 南→東（左折）
        ((-700.0, e2["EB"]), E, (xs["NB"], Y + 700), N, "SYN2-EB", "SYN1-NB", "0"),  # 東→北（左折）
        ((700.0, e2["WB"]), W, (xs["SB"], Y - 700), S, "SYN2-WB", "SYN1-SB", "0"),  # 西→南（左折）
        ((xs["NB"], Y - 900), N, (900.0, e2["EB"]), E, "SYN1-NB", "SYN2-EB", "1"),  # 北→東（右折・旋回）
        ((xs["SB"], Y + 900), S, (-900.0, e2["WB"]), W, "SYN1-SB", "SYN2-WB", "1"),  # 南→西（右折・旋回）
        ((-900.0, e2["EB"]), E, (xs["SB"], Y - 900), S, "SYN2-EB", "SYN1-SB", "1"),  # 東→南（右折・旋回）
        ((900.0, e2["WB"]), W, (xs["NB"], Y + 900), N, "SYN2-WB", "SYN1-NB", "1"),  # 西→北（右折・旋回）
    ]
    for p0, h0, p1, h1, l0, l1, layer in J2:
        tags: dict[str, Any] = {"bridge": "yes", "layer": "1"} if layer == "1" else {}
        b.link(hermite(p0, h0, p1, h1, 50, 1.2 if layer == "1" else 0.9), l0, "", True, l1, **tags)
    junction(xs["NB"], Y - 900, "合成タービンJCT")
    junction(xs["SB"], Y + 900, "合成タービンJCT")
    junction(-900.0, e2["EB"], "合成タービンJCT")
    junction(900.0, e2["WB"], "合成タービンJCT")
    syn2 = {"highway": "motorway", "name": "合成本線2", "ref": "SYN2"}
    _line(
        b,
        "SYN2-EB",
        (-6000.0, e2["EB"]),
        (6000.0, e2["EB"]),
        {**syn2, "oneway": "yes"},
        splits=[(5940.0, 6060.0, {"bridge": "yes", "layer": "2"})],
    )
    _line(
        b,
        "SYN2-WB",
        (6000.0, e2["WB"]),
        (-6000.0, e2["WB"]),
        {**syn2, "oneway": "yes"},
        splits=[(5940.0, 6060.0, {"bridge": "yes", "layer": "2"})],
    )

    # ---- 8) Y型 JCT（y=-13000、SYN3 が南西へ分岐）
    Y = -13000.0
    ang = math.radians(-120)  # 南西方向
    s3_start_sw = (xs["SB"] + 300 * math.cos(ang) + 600, Y - 900)
    s3_dir = (math.cos(ang), math.sin(ang))
    sw_start = (-200.0, Y - 700)  # 南西行き車線の起点
    ne_end = (-230.0, Y - 680)
    b.link(
        hermite((xs["SB"], Y + 300), S, sw_start, ang, 40),
        "SYN1-SB",
        "SB",
        True,
        "SYN3-SW",
        end_at_start=False,
    )
    b.link(hermite(ne_end, ang + math.pi, (xs["NB"], Y + 300), N, 40), "SYN3-NE", "NB", True, "SYN1-NB")
    junction(xs["SB"], Y + 300, "合成YJCT")
    syn3 = {"highway": "motorway", "name": "合成本線3", "ref": "SYN3", "oneway": "yes"}
    far = 6000.0
    _line(b, "SYN3-SW", sw_start, (sw_start[0] + s3_dir[0] * far, sw_start[1] + s3_dir[1] * far), syn3)
    _line(b, "SYN3-NE", (ne_end[0] + s3_dir[0] * far, ne_end[1] + s3_dir[1] * far), ne_end, syn3)
    del s3_start_sw

    # ---- 本線 SYN1（南→北の北行き、北→南の南行き）。タービン部は SYN2 が跨ぐ
    syn1 = {
        "highway": "motorway",
        "name": "合成本線1",
        "ref": "SYN1",
        "oneway": "yes",
        "maxspeed": "100",
        "lanes": "2",
    }
    _line(
        b,
        "SYN1-NB",
        (xs["NB"], -16000.0),
        (xs["NB"], 18000.0),
        syn1,
        splits=[
            (16000.0 + 3900.0, 16000.0 + 4100.0, {"bridge": "yes", "layer": "1"}),
            (16000.0 - 9000.0 - 300.0, 16000.0 - 9000.0 + 200.0, {"tunnel": "yes", "layer": "-1"}),
        ],
    )
    _line(
        b,
        "SYN1-SB",
        (xs["SB"], 18000.0),
        (xs["SB"], -16000.0),
        syn1,
        splits=[(18000.0 + 4000.0 - 4100.0, 18000.0 + 4000.0 - 3900.0, {"bridge": "yes", "layer": "1"})],
    )
    return b


def builder_to_osm(b: Builder) -> OsmData:
    def ll(nid: int) -> tuple[float, float]:
        return to_lonlat(*b.coords[nid])

    ways = []
    for w in b.ways:
        lonlat = [ll(n) for n in w["nodes"]]
        ways.append(
            {
                "way_id": w["way_id"],
                "highway": w["tags"]["highway"],
                "tags": dict(w["tags"]),
                "node_ids": list(w["nodes"]),
                "lons": [p[0] for p in lonlat],
                "lats": [p[1] for p in lonlat],
                "timestamp": TS,
            }
        )
    nodes = []
    for nid, tags in sorted(b.node_tags.items()):
        lo, la = ll(nid)
        kind = tags.get("highway") or tags.get("barrier")
        nodes.append({"node_id": nid, "lon": lo, "lat": la, "tags": tags, "kind": kind, "timestamp": TS})
    areas = []
    for i, a in enumerate(b.areas):
        poly = Polygon([to_lonlat(x, y) for x, y in a["poly"].exterior.coords])
        areas.append(
            {
                "area_id": 900000 + i,
                "kind": a["kind"],
                "tags": {"highway": a["kind"], "name": a["name"]},
                "geometry": poly,
                "timestamp": TS,
            }
        )
    return OsmData(
        ways=pd.DataFrame(
            ways, columns=["way_id", "highway", "tags", "node_ids", "lons", "lats", "timestamp"]
        ),
        nodes=pd.DataFrame(nodes, columns=["node_id", "lon", "lat", "tags", "kind", "timestamp"]),
        areas=pd.DataFrame(areas, columns=["area_id", "kind", "tags", "geometry", "timestamp"]),
        relations=pd.DataFrame(columns=["rel_id", "ref", "name", "way_ids"]),
        meta={"path": "synthetic"},
    )


def synthetic_osm() -> OsmData:
    return builder_to_osm(build_synthetic())


EXPECTED_SHAPES = {
    "合成トランペット|IC": "トランペット型",
    "合成ダイヤモンド|IC": "ダイヤモンド型",
    "合成クローバー|IC": "クローバーリーフ型",
    "合成タービン|JCT": "タービン型",
    "合成Y|JCT": "Y型",
    "合成ハーフ|IC": "ハーフIC",
    "合成|SIC": "スマートIC",
    "合成|SA": "SA/PA",
}


def write_synthetic_fixture(out_dir: Path) -> Path:
    import osmium

    b = build_synthetic()
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "synthetic.osm.pbf"
    if dest.exists():
        dest.unlink()
    w = osmium.SimpleWriter(str(dest))
    area_nodes: list[tuple[int, tuple[float, float]]] = []
    area_ways = []
    nid = 9_000_000
    for i, a in enumerate(b.areas):
        ids = []
        for x, y in list(a["poly"].exterior.coords)[:-1]:
            area_nodes.append((nid, to_lonlat(x, y)))
            ids.append(nid)
            nid += 1
        area_ways.append((900000 + i, [*ids, ids[0]], {"highway": a["kind"], "name": a["name"]}))
    ts = TS.strftime("%Y-%m-%dT%H:%M:%SZ")
    for n, (x, y) in sorted(b.coords.items()):
        lo, la = to_lonlat(x, y)
        w.add_node(
            osmium.osm.mutable.Node(
                id=n, location=(lo, la), tags=b.node_tags.get(n, {}), version=1, timestamp=ts
            )
        )
    for n, (lo, la) in area_nodes:
        w.add_node(osmium.osm.mutable.Node(id=n, location=(lo, la), tags={}, version=1, timestamp=ts))
    for wd in b.ways:
        w.add_way(
            osmium.osm.mutable.Way(
                id=wd["way_id"], nodes=wd["nodes"], tags=wd["tags"], version=1, timestamp=ts
            )
        )
    for wid, ids, tags in area_ways:
        w.add_way(osmium.osm.mutable.Way(id=wid, nodes=ids, tags=tags, version=1, timestamp=ts))
    w.close()
    return dest
