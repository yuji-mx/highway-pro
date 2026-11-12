"""ネットワーク系テストの補助：合成 OSM から施設・ランプ・区間までを構築する。"""

from __future__ import annotations

from typing import Any

from shapely.geometry.base import BaseGeometry

from roadmri.ingest.osm_pbf import OsmData
from roadmri.ingest.synthetic import Builder, N, S, builder_to_osm, hermite
from roadmri.pipeline import Ctx, step_facilities, step_network, step_ramps, step_sections


def build_network(cfg: Any, osm: OsmData, range_geom: BaseGeometry | None = None) -> Ctx:
    ctx = Ctx(
        cfg=cfg,
        region_id="test",
        region={"id": "test", "dem": "synthetic"},
        range_spec={"type": "bbox", "bbox": [139, 36, 141, 37]},
        country="JP",
        range_geom=range_geom,
    )
    ctx.osm = osm
    step_network(ctx)
    step_facilities(ctx)
    step_ramps(ctx)
    step_sections(ctx)
    return ctx


MAIN = {"highway": "motorway", "name": "試験本線", "ref": "SYN1", "oneway": "yes"}


def diamond_ic(
    b: Builder,
    y: float,
    name: str,
    x_nb: float = -15.0,
    x_sb: float = 15.0,
    half: bool = False,
    etc: bool = False,
    road: str | None = None,
) -> str:
    """y に北行き・南行き両方向のダイヤモンド型 IC（half=True で北行き流出・南行き流入のみ）。"""
    key = road or f"ROAD-{name}"
    tags = {"access": "no", "motorcar:conditional": "yes @ ETC"} if etc else {}
    b.link(hermite((x_nb, y - 400), N, (-250.0, y), N), "NB", "NB", True, key, **tags)
    b.link(hermite((250.0, y), S, (x_sb, y - 400), S), key, "SB", True, "SB", **tags)
    if not half:
        b.link(hermite((-250.0, y), N, (x_nb, y + 400), N), key, "NB", True, "NB", **tags)
        b.link(hermite((x_sb, y + 400), S, (250.0, y), S), "SB", "SB", True, key, **tags)
    b.node_tags[b.node(x_nb, y - 400)] = {"highway": "motorway_junction", "name": name, "ref": "1"}
    if not half:
        b.node_tags[b.node(x_sb, y + 400)] = {"highway": "motorway_junction", "name": name, "ref": "1"}
    return key


def straight_mainline(
    b: Builder,
    y0: float,
    y1: float,
    x_nb: float = -15.0,
    x_sb: float = 15.0,
    tags: dict[str, str] | None = None,
) -> None:
    from roadmri.ingest.synthetic import _line

    t = {**MAIN, **(tags or {})}
    _line(b, "NB", (x_nb, y0), (x_nb, y1), t)
    _line(b, "SB", (x_sb, y1), (x_sb, y0), t)


def road(b: Builder, key: str, y: float, x0: float = -2000.0, x1: float = 2000.0) -> None:
    from roadmri.ingest.synthetic import _line

    _line(
        b,
        key,
        (x0, y),
        (x1, y),
        {"highway": "primary", "name": key},
        splits=[(abs(x0) - 60, abs(x0) + 60, {"bridge": "yes", "layer": "1"})],
    )


def to_osm(b: Builder) -> OsmData:
    return builder_to_osm(b)
