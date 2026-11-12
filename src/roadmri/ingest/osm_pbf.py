"""OSM 読込（PBF / OSM XML。pyosmium）。§4.1。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import osmium
import pandas as pd
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

KEEP_TAGS = (
    "highway",
    "name",
    "name:ja",
    "name:en",
    "ref",
    "int_ref",
    "oneway",
    "lanes",
    "maxspeed",
    "destination",
    "destination:ref",
    "destination:street",
    "junction:ref",
    "bridge",
    "tunnel",
    "layer",
    "covered",
    "cutting",
    "embankment",
    "toll",
    "access",
    "motorcar:conditional",
    "motorroad",
    "exit_to",
    "barrier",
    "junction",
    "lanes:forward",
    "lanes:backward",
    "turn:lanes",
    "placement",
    "bridge:name",
    "tunnel:name",
)
MAIN_HIGHWAYS = {"motorway", "motorway_link", "trunk", "trunk_link"}
AREA_HIGHWAYS = {"services", "rest_area"}


@dataclass
class OsmData:
    ways: pd.DataFrame
    nodes: pd.DataFrame
    areas: pd.DataFrame
    relations: pd.DataFrame
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def max_timestamp(self) -> datetime | None:
        ts = [
            t
            for t in [
                self.ways["timestamp"].max() if len(self.ways) else None,
                self.nodes["timestamp"].max() if len(self.nodes) else None,
            ]
            if t is not None and not pd.isna(t)
        ]
        return max(ts) if ts else None


def _tags(t: Any) -> dict[str, str]:
    return {k: v for k, v in ((tg.k, tg.v) for tg in t) if k in KEEP_TAGS or k.startswith("destination")}


def _ts(o: Any) -> datetime | None:
    try:
        t: datetime = o.timestamp
        return t.replace(tzinfo=UTC) if t.tzinfo is None else t
    except (AttributeError, ValueError):
        return None


class _Reader(osmium.SimpleHandler):
    def __init__(self, highways: set[str], clip: BaseGeometry | None):
        super().__init__()
        self.highways = highways
        self.clip = prep(clip) if clip is not None else None
        self.clip_bounds = clip.bounds if clip is not None else None
        self.ways: dict[int, dict[str, Any]] = {}
        self.nodes: dict[int, dict[str, Any]] = {}
        self.areas: dict[int, dict[str, Any]] = {}
        self.rels: dict[int, dict[str, Any]] = {}

    def _inside(self, lon: float, lat: float) -> bool:
        if self.clip is None:
            return True
        b = self.clip_bounds
        assert b is not None
        if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
            return False
        from shapely.geometry import Point

        return bool(self.clip.contains(Point(lon, lat)))

    def node(self, n: Any) -> None:
        hw = n.tags.get("highway")
        barrier = n.tags.get("barrier")
        if hw in ("motorway_junction", "services", "rest_area") or barrier == "toll_booth":
            if not n.location.valid():
                return
            lon, lat = n.location.lon, n.location.lat
            if not self._inside(lon, lat):
                return
            self.nodes[n.id] = {
                "node_id": n.id,
                "lon": lon,
                "lat": lat,
                "tags": _tags(n.tags),
                "kind": hw or barrier,
                "timestamp": _ts(n),
            }

    def way(self, w: Any) -> None:
        hw = w.tags.get("highway")
        if hw is None:
            return
        if hw in AREA_HIGHWAYS:
            try:
                coords = [(nd.lon, nd.lat) for nd in w.nodes]
            except osmium.InvalidLocationError:
                return
            if len(coords) >= 4 and coords[0] == coords[-1]:
                poly = Polygon(coords)
                c = poly.centroid
                if self._inside(c.x, c.y):
                    self.areas[w.id] = {
                        "area_id": w.id,
                        "kind": hw,
                        "tags": _tags(w.tags),
                        "geometry": poly,
                        "timestamp": _ts(w),
                    }
            return
        if hw not in self.highways:
            return
        try:
            refs = [nd.ref for nd in w.nodes]
            lons = [nd.lon for nd in w.nodes]
            lats = [nd.lat for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        if len(refs) < 2:
            return
        if self.clip is not None and not any(self._inside(a, b) for a, b in zip(lons, lats, strict=True)):
            return
        self.ways[w.id] = {
            "way_id": w.id,
            "highway": hw,
            "tags": _tags(w.tags),
            "node_ids": refs,
            "lons": lons,
            "lats": lats,
            "timestamp": _ts(w),
        }

    def relation(self, r: Any) -> None:
        if r.tags.get("type") == "route" and r.tags.get("route") == "road":
            ways = [m.ref for m in r.members if m.type == "w"]
            self.rels[r.id] = {
                "rel_id": r.id,
                "ref": r.tags.get("ref"),
                "name": r.tags.get("name"),
                "way_ids": ways,
            }


def read_osm(path: Path, highways: set[str] | None = None, clip: BaseGeometry | None = None) -> OsmData:
    """PBF または OSM XML を読み込み、必要なオブジェクトだけを返す。"""
    hw = set(highways or MAIN_HIGHWAYS)
    r = _Reader(hw, clip)
    r.apply_file(str(path), locations=True)
    rel_df = pd.DataFrame(list(r.rels.values()), columns=["rel_id", "ref", "name", "way_ids"])
    ways = pd.DataFrame(
        sorted(r.ways.values(), key=lambda d: d["way_id"]),
        columns=["way_id", "highway", "tags", "node_ids", "lons", "lats", "timestamp"],
    )
    # ルートリレーションによる ref・路線名の補完
    if len(rel_df) and len(ways):
        rel_ref: dict[int, tuple[str | None, str | None]] = {}
        for rr in rel_df.itertuples(index=False):
            ref = rr.ref if isinstance(rr.ref, str) and rr.ref else None
            name = rr.name if isinstance(rr.name, str) and rr.name else None
            if ref is None:
                continue
            for wid in rr.way_ids:
                rel_ref.setdefault(int(wid), (ref, name))
        updated_tags = []
        for row in ways.itertuples(index=False):
            t = row.tags
            if "ref" not in t and row.way_id in rel_ref and rel_ref[row.way_id][0]:
                t = {**t, "ref": rel_ref[row.way_id][0], "_ref_from_relation": "yes"}
            updated_tags.append(t)
        ways["tags"] = updated_tags
    nodes = pd.DataFrame(
        sorted(r.nodes.values(), key=lambda d: d["node_id"]),
        columns=["node_id", "lon", "lat", "tags", "kind", "timestamp"],
    )
    areas = pd.DataFrame(
        sorted(r.areas.values(), key=lambda d: d["area_id"]),
        columns=["area_id", "kind", "tags", "geometry", "timestamp"],
    )
    meta = {"path": str(path)}
    return OsmData(ways=ways, nodes=nodes, areas=areas, relations=rel_df, meta=meta)


def bbox_polygon(bbox: list[float]) -> Polygon:
    return box(bbox[0], bbox[1], bbox[2], bbox[3])


def osmium_extract(src: Path, dest: Path, bbox: list[float]) -> Path:
    """osmium-tool があれば範囲抽出する。なければ（Windows 等）元ファイルを返し、read_osm の clip で範囲を絞る。"""
    import shutil
    import subprocess

    exe = shutil.which("osmium")
    if not exe:
        return src
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [exe, "extract", "-b", ",".join(str(v) for v in bbox), str(src), "-o", str(dest), "--overwrite"],
        check=True,
    )
    return dest
