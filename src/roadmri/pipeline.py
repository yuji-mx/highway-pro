"""処理パイプライン（§7）。段ごとに中間成果物を保存し、--from-step で途中から再開できる。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import pickle
import time
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from roadmri.analysis.difficulty import composite_indicators, difficulty
from roadmri.analysis.indicators import ElementGeom, ElementResult, analyze_element, element_struct_from_edges
from roadmri.analysis.ranges import range_geometry
from roadmri.classify.engine import classify
from roadmri.classify.features import facility_features
from roadmri.config import Config
from roadmri.geometry.projection import Projector
from roadmri.ingest.boundaries import load_admin
from roadmri.ingest.dem import DemSampler, build_providers
from roadmri.ingest.http import HttpClient
from roadmri.ingest.osm_pbf import OsmData, read_osm
from roadmri.logging import get_logger, log_event
from roadmri.network.dedupe import merge_candidates
from roadmri.network.graph import RoadGraph
from roadmri.network.junctions import (
    Facility,
    assign_components,
    build_candidates,
    determine_types,
    link_components,
    make_facilities,
    merge_by_shared_components,
)
from roadmri.network.mainline import Chain, assign_directions, build_chains, chain_endpoints_gaps
from roadmri.network.names import NameNormalizer
from roadmri.network.ramps import Ramp, extract_ramps
from roadmri.network.sections import Section, build_sections, gap_sections
from roadmri.provenance import file_sha256, make_run_id, provenance_doc
from roadmri.quality.flags import make_flag
from roadmri.quality.levels import dem_quality, level_from_flags, osm_quality_ramp
from roadmri.store.duckdb_store import Store
from roadmri.store.manual_edits import apply_edits, effective_edits
from roadmri.units import parse_speed_kmh

log = get_logger("pipeline")

STEPS = (
    "ingest",
    "network",
    "facilities",
    "ramps",
    "sections",
    "metrics",
    "classify",
    "quality",
    "edits",
    "publish",
)
STEP_ALIASES = {
    "ingest_osm": "ingest",
    "build_network": "network",
    "extract_facilities": "facilities",
    "extract_ramps": "ramps",
    "build_sections": "sections",
    "resample_geometry": "metrics",
    "attach_elevation": "metrics",
    "compute_metrics": "metrics",
    "apply_manual_edits": "edits",
}
LOAD_HIGHWAYS = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
}


@dataclass
class Ctx:
    cfg: Config
    region_id: str
    region: dict[str, Any]
    range_spec: dict[str, Any]
    country: str
    range_geom: BaseGeometry | None = None
    clip_geom: BaseGeometry | None = None
    osm_path: Path | None = None
    osm: OsmData | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    rg: RoadGraph | None = None
    chains: list[Chain] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    facs: list[Facility] = field(default_factory=list)
    ramps: list[Ramp] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    ramp_results: dict[str, ElementResult] = field(default_factory=dict)
    section_results: dict[str, ElementResult] = field(default_factory=dict)
    dem_meta: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    flags: list[dict[str, Any]] = field(default_factory=list)
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    input_hash: str = ""
    edit_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
def region_osm_path(cfg: Config, region: dict[str, Any], source: str | None = None) -> Path:
    pbf = region.get("pbf", {})
    if source == "overpass":
        d = cfg.path("raw_dir") / "osm" / "overpass"
        files = sorted(d.glob(f"*/{region['id']}.osm"))
        if not files:
            raise FileNotFoundError(
                "Overpass の取得データがありません。先に roadmri fetch --source overpass を実行してください"
            )
        return files[-1]
    if pbf.get("local"):
        p = Path(pbf["local"])
        return p if p.is_absolute() else cfg.root / p
    d = cfg.path("raw_dir") / "osm" / "geofabrik"
    name = Path(str(pbf.get("url", ""))).name
    files = sorted(d.glob(f"*/{name}"))
    if not files:
        raise FileNotFoundError(f"OSM データ {name} がありません。先に roadmri fetch を実行してください")
    return files[-1]


def make_dem_sampler(ctx: Ctx, offline: bool | None = None) -> tuple[DemSampler, list[Any]]:
    cfg = ctx.cfg
    dem_cfg = cfg.dem_sources
    caches = [
        Path(p) if Path(p).is_absolute() else cfg.root / p for p in ctx.region.get("dem_cache_dirs", [])
    ]
    caches.append(cfg.path("raw_dir") / "dem" / "tiles")
    off = bool(dem_cfg.get("offline", False)) if offline is None else offline
    http = None if off else HttpClient(cfg.settings)
    dctx = {"tile_cache_dirs": caches, "http": http, "offline": off, "cache_dir": cfg.path("raw_dir") / "dem"}
    providers = build_providers(dem_cfg, str(ctx.region.get("dem", "jp")), dctx)
    return DemSampler(providers), providers


def new_ctx(cfg: Config, region_id: str) -> Ctx:
    region = cfg.region(region_id)
    spec = dict(region["range"])

    def admin_loader(codes: list[str]) -> BaseGeometry | None:
        return load_admin(cfg.path("boundaries_dir"), codes)

    rg = range_geometry({**spec, "buffer_km": 0}, admin_loader)
    clip = range_geometry(spec, admin_loader)
    return Ctx(
        cfg=cfg,
        region_id=str(region.get("id", region_id)),
        region=region,
        range_spec=spec,
        country=str(region.get("country", "JP")),
        range_geom=rg,
        clip_geom=clip,
    )


# ---- 段 1: ingest -----------------------------------------------------------
def step_ingest(ctx: Ctx, source: str | None = None) -> None:
    path = region_osm_path(ctx.cfg, ctx.region, source)
    ctx.osm_path = path
    if ctx.region_id == "synthetic" and not path.exists():
        from roadmri.ingest.synthetic import write_synthetic_fixture

        write_synthetic_fixture(path.parent)
    ctx.osm = read_osm(path, LOAD_HIGHWAYS, ctx.clip_geom)
    sha = file_sha256(path)
    ctx.input_hash = sha
    ts = ctx.osm.max_timestamp
    ctx.sources = [
        {
            "source_id": f"osm:{sha[:12]}",
            "kind": "osm",
            "name": path.name,
            "url": str(ctx.region.get("pbf", {}).get("url") or path),
            "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, UTC),
            "sha256": sha,
            "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors",
            "osm_replication_seq": None,
            "osm_timestamp": ts,
        }
    ]
    log_event(log, "ingest", ways=len(ctx.osm.ways), nodes=len(ctx.osm.nodes), path=str(path))


# ---- 段 2: network ----------------------------------------------------------
def step_network(ctx: Ctx) -> None:
    assert ctx.osm is not None
    proj = None
    if ctx.range_geom is not None:
        proj = Projector.for_bounds(*ctx.range_geom.bounds)
    ctx.rg = RoadGraph.build(ctx.osm, ctx.cfg.routes, proj)
    assign_directions(ctx.rg, ctx.cfg.routes, ctx.country)
    ctx.chains = build_chains(ctx.rg)
    ctx.gaps = chain_endpoints_gaps(ctx.rg, ctx.chains, float(ctx.cfg.analysis["quality"]["mainline_gap_m"]))


# ---- 段 3: facilities -------------------------------------------------------
def _normalizer(cfg: Config) -> NameNormalizer:
    return NameNormalizer(cfg.name_normalization, cfg.name_aliases)


def step_facilities(ctx: Ctx) -> None:
    assert ctx.rg is not None and ctx.osm is not None
    a = ctx.cfg.analysis["facility"]
    nn = _normalizer(ctx.cfg)
    cands = build_candidates(ctx.rg, ctx.osm, nn)
    raw_d: Any = a["merge_exit_ref_distance_m"]
    picked: Any = raw_d.get(ctx.country, raw_d.get("default", 1000)) if isinstance(raw_d, dict) else raw_d
    exit_d = float(picked)
    groups = merge_candidates(cands, a["merge_max_distance_m"], exit_d)
    comps = link_components(ctx.rg, ctx.osm)
    groups = merge_by_shared_components(groups, comps)
    asg = assign_components(ctx.rg, groups, comps, ctx.osm, float(a["ramp_search_radius_m"]), nn)
    codes = ctx.range_spec.get("admin_codes") or []
    ctx.facs = make_facilities(
        ctx.rg, ctx.osm, asg, ctx.country, nn, ctx.range_geom, codes[0] if len(codes) == 1 else None
    )


# ---- 段 4: ramps ------------------------------------------------------------
def step_ramps(ctx: Ctx, progress: Callable[[float, str], None] | None = None) -> None:
    assert ctx.rg is not None
    ctx.ramps = extract_ramps(
        ctx.rg, ctx.facs, float(ctx.cfg.analysis["curvature"]["loop_turn_deg"]), progress
    )
    by: dict[str, list[dict[str, Any]]] = {}
    for r in ctx.ramps:
        by.setdefault(r.facility_id, []).append(
            {"flow": r.flow, "carriageway": r.carriageway, "mainline_route": r.mainline_route}
        )
    determine_types(ctx.facs, by)


# ---- 段 5: sections ---------------------------------------------------------
def step_sections(ctx: Ctx) -> None:
    assert ctx.rg is not None
    ratio = float(ctx.cfg.analysis["quality"]["detour_ratio"])
    secs = build_sections(ctx.rg, ctx.chains, ctx.facs, ctx.ramps, ratio)
    secs += gap_sections(ctx.rg, ctx.chains, ctx.facs, ctx.ramps, ctx.gaps)
    seen: set[str] = set()
    ctx.sections = []
    for s in secs:
        if s.section_id not in seen:
            seen.add(s.section_id)
            ctx.sections.append(s)


# ---- 段 6〜8: metrics（再サンプリング・曲率・標高・勾配・複合指標） -----------------
def accel_lane_length(rg: RoadGraph, merge_node: int, max_len: float = 1500.0) -> float | None:
    """合流点から本線の車線減少点までの距離（OSM の lanes 変化から推定。取れない場合は None）。"""

    def lanes(d: dict[str, Any]) -> int | None:
        try:
            return int(str(d["tags"].get("lanes")).split(";")[0])
        except (TypeError, ValueError):
            return None

    ins = [d for _, _, d in rg.g.in_edges(merge_node, data=True) if d["cls"] == "mainline"]
    outs = [(v, d) for _, v, d in rg.g.out_edges(merge_node, data=True) if d["cls"] == "mainline"]
    if not ins or not outs:
        return None
    lb = lanes(ins[0])
    la = lanes(outs[0][1])
    if lb is None or la is None or la <= lb:
        return None
    dist = 0.0
    node, d = outs[0]
    route = d.get("route_ref")
    while dist < max_len:
        cur = lanes(d)
        if cur is not None and cur <= lb:
            return dist
        dist += float(d["length_m"])
        nxt = [
            (v, dd)
            for _, v, dd in rg.g.out_edges(node, data=True)
            if dd["cls"] == "mainline" and dd.get("route_ref") == route
        ]
        if not nxt:
            return None
        node, d = nxt[0]
    return None


def step_metrics(ctx: Ctx, progress: Callable[[float, str], None] | None = None) -> None:
    assert ctx.rg is not None
    cfg = dict(ctx.cfg.analysis)
    cfg["_composite"] = ctx.cfg.difficulty["composite"]
    sampler, providers = make_dem_sampler(ctx)
    total = max(1, len(ctx.ramps) + len(ctx.sections))
    done = 0
    for r in ctx.ramps:
        xy = ctx.rg.edges_xy(r.edges)
        geom = ElementGeom(
            "ramp",
            r.ramp_id,
            xy,
            element_struct_from_edges(r.edges),
            merge_at_end=r.to_ref.get("kind") == "mainline",
        )
        res = analyze_element(geom, ctx.rg.proj, sampler, cfg)
        res.metrics["accel_lane_len_m"] = (
            accel_lane_length(ctx.rg, r.nodes[-1])
            if r.flow == "entry" and r.to_ref.get("kind") == "mainline"
            else None
        )
        ctx.ramp_results[r.ramp_id] = res
        done += 1
        if progress and done % 20 == 0:
            progress(done / total, f"ランプ解析 {done}/{len(ctx.ramps)}")
    for s in ctx.sections:
        if len(s.geom_xy) < 2:
            continue
        geom = ElementGeom("mainline", s.section_id, s.geom_xy, s.edges_struct, section_id=s.section_id)
        ctx.section_results[s.section_id] = analyze_element(geom, ctx.rg.proj, sampler, cfg)
        done += 1
        if progress and done % 5 == 0:
            progress(done / total, f"区間解析 {done - len(ctx.ramps)}/{len(ctx.sections)}")
    all_src: set[str] = set()
    miss: dict[str, list[float]] = {}
    for res in list(ctx.ramp_results.values()) + list(ctx.section_results.values()):
        all_src.update(res.metrics.get("dem_sources", []))
    for p in providers:
        st = p.stats()
        if st["requested"]:
            miss.setdefault(p.meta.dem_id, []).append(st["missing"] / st["requested"])
    ctx.dem_meta = []
    for p in providers:
        if p.meta.dem_id in all_src:
            m = p.meta
            ctx.dem_meta.append(
                {
                    "dem_id": m.dem_id,
                    "name": m.name,
                    "resolution_m": m.resolution_m,
                    "crs": m.crs,
                    "vertical_datum": m.vertical_datum,
                    "retrieved_at": datetime.now(UTC).replace(microsecond=0),
                    "nominal_error_m": m.nominal_error_m,
                    "missing_ratio": float(np.mean(miss.get(m.dem_id, [0.0]))),
                    "coverage": None,
                    "source_id": m.url or m.dem_id,
                    "attribution": m.attribution,
                }
            )
    ctx.dem_meta.sort(key=lambda d: d["dem_id"])


# ---- 段 9: classify ---------------------------------------------------------
def step_classify(ctx: Ctx) -> None:
    assert ctx.rg is not None
    rmap: dict[str, list[Ramp]] = {}
    for r in ctx.ramps:
        rmap.setdefault(r.facility_id, []).append(r)
    float(ctx.cfg.analysis["classification"]["softmax_temperature"])
    ctx.features = {}
    for f in ctx.facs:
        ctx.features[f.facility_id] = facility_features(ctx.rg, f, rmap.get(f.facility_id, []), ctx.chains)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _geom_wkb(g: BaseGeometry) -> bytes:
    return bytes(wkb.dumps(g, hex=False))


def _line_lonlat(rg: RoadGraph, xy: np.ndarray) -> LineString:
    lon, lat = rg.proj.to_lonlat(xy[:, 0], xy[:, 1])
    return LineString(np.column_stack([lon, lat]))


# ---- テーブル化（classify/quality の結果を含む） -------------------------------------
def build_tables(ctx: Ctx) -> None:
    assert ctx.rg is not None
    rg = ctx.rg
    cfg = ctx.cfg
    rules = cfg.classification_rules
    temp = float(cfg.analysis["classification"]["softmax_temperature"])
    low_thr = float(cfg.analysis["classification"]["low_confidence_threshold"])
    run_id = ctx.run_id
    rmap: dict[str, list[Ramp]] = {}
    for r in ctx.ramps:
        rmap.setdefault(r.facility_id, []).append(r)
    # ---- ramps
    ramp_rows = []
    rwm = []
    for r in ctx.ramps:
        m = ctx.ramp_results[r.ramp_id].metrics if r.ramp_id in ctx.ramp_results else {}
        line = _line_lonlat(rg, rg.edges_xy(r.edges))
        dm = dict(m)
        score, comps = difficulty(dm, r.flow, cfg.difficulty)
        comp = composite_indicators(dm, r.flow, cfg.difficulty)
        dq = dem_quality(_f(m.get("dem_low_conf_ratio")), cfg.analysis)
        oq = osm_quality_ramp(r.lanes, r.maxspeed_kmh, r.carriageway)
        row = {
            "ramp_id": r.ramp_id,
            "facility_id": r.facility_id,
            "related_facility_ids": r.related_facility_ids,
            "seq": r.seq,
            "flow": r.flow,
            "carriageway": r.carriageway,
            "branch_side": r.branch_side,
            "merge_diverge": r.merge_diverge,
            "from_ref": r.from_ref,
            "to_ref": r.to_ref,
            "geom": _geom_wkb(line),
            "length_m": r.length_m,
            "lanes": r.lanes,
            "maxspeed_kmh": r.maxspeed_kmh,
            "is_loop": r.is_loop,
            "osm_quality": oq,
            "dem_quality": dq,
            "drive_data_available": False,
            "excluded": False,
            "flow_auto": r.flow,
            "facility_id_auto": r.facility_id,
            "turn_deg": r.turn_deg,
            "difficulty": score,
            "difficulty_components": comps,
            "composite": comp,
            "curves": ctx.ramp_results[r.ramp_id].curves if r.ramp_id in ctx.ramp_results else [],
            "run_id": run_id,
        }
        for k in (
            "min_radius_m",
            "mean_radius_m",
            "max_curvature",
            "mean_curvature",
            "curvature_rate_max",
            "curve_length_m",
            "straight_length_m",
            "grade_inst_max",
            "grade_inst_min",
            "grade_mean",
            "elev_gain_m",
            "elev_loss_m",
            "elev_diff_m",
            "z_max",
            "z_min",
            "cont_up_len_m",
            "cont_down_len_m",
            "tunnel_len_m",
            "bridge_len_m",
            "viaduct_len_m",
            "accel_lane_len_m",
            "pre_merge_grade",
            "end_grade",
            "dem_low_conf_ratio",
        ):
            row[k] = _f(m.get(k))
        for w in (10, 50, 100, 500):
            row[f"grade_up_max_w{w}"] = _f(m.get(f"grade_up_max_w{w}"))
            row[f"grade_down_max_w{w}"] = _f(m.get(f"grade_down_max_w{w}"))
        ed = _f(m.get("elev_diff_m"))
        row["abs_elev_diff_m"] = abs(ed) if ed is not None else None
        row["window_truncated"] = bool(m.get("window_truncated", False))
        row["curve_count"] = int(m.get("curve_count") or 0)
        ramp_rows.append(row)
        for i, (wid, rev) in enumerate(_way_order(r)):
            rwm.append({"ramp_id": r.ramp_id, "way_id": wid, "order": i, "reversed": rev})
    ramps_df = pd.DataFrame(ramp_rows)
    # ---- facilities
    fac_rows = []
    fmap = []
    cfeat = []
    for f in ctx.facs:
        feats = ctx.features.get(f.facility_id, {})
        cr = classify(feats, rules, temp, f.country)
        rr = ramps_df[ramps_df["facility_id"] == f.facility_id] if len(ramps_df) else ramps_df
        main = rr[rr["merge_diverge"].notna()] if len(rr) else rr
        speeds = []
        for c in f.components:
            for n in c.mainline_nodes:
                for e in rg.incident(n):
                    if e.data["cls"] == "mainline":
                        sp = parse_speed_kmh(e.data["tags"].get("maxspeed"))
                        if sp is not None:
                            speeds.append(sp)
        review = "needs_review" if cr.confidence < low_thr and cr.shape_class != "SA/PA" else "unreviewed"
        fac_rows.append(
            {
                "facility_id": f.facility_id,
                "country": f.country,
                "admin_code": f.admin_code,
                "name": f.name,
                "name_en": f.name_en,
                "normalized_name": f.normalized_name,
                "name_variants": f.name_variants,
                "facility_type": f.facility_type,
                "facility_type_auto": f.facility_type,
                "name_auto": f.name,
                "is_half_ic": f.is_half_ic,
                "is_jct_combined": f.is_jct_combined,
                "parent_facility_id": f.parent_facility_id,
                "exit_number": f.exit_number,
                "route_refs": f.route_refs,
                "destinations": f.destinations,
                "lon": f.lon,
                "lat": f.lat,
                "geom": _geom_wkb(Point(f.lon, f.lat)),
                "hull": _geom_wkb(f.hull) if f.hull is not None else None,
                "ramp_count": int(feats.get("ramp_count", len(main))),
                "loop_ramp_count": int(feats.get("loop_ramp_count", 0)),
                "connection_directions": int(feats.get("connection_directions", 0)),
                "shape_class": cr.shape_class,
                "shape_confidence": cr.confidence,
                "shape_class_auto": cr.shape_class,
                "shape_confidence_auto": cr.confidence,
                "shape_class_2": cr.second,
                "shape_confidence_2": cr.second_confidence,
                "classification_reasons": cr.reasons,
                "unclassifiable_reason": cr.unclassifiable_reason,
                "review_status": review,
                "osm_timestamp": f.osm_timestamp,
                "merge_reason": f.merge_reason,
                "in_range": f.in_range,
                "excluded": False,
                "mean_ramp_length_m": _f(main["length_m"].mean()) if len(main) else None,
                "min_radius_m": _f(rr["min_radius_m"].min()) if len(rr) else None,
                "grade_up_max_w100": _f(rr["grade_up_max_w100"].max()) if len(rr) else None,
                "max_elev_diff_m": _f(rr["abs_elev_diff_m"].max()) if len(rr) else None,
                "design_speed_kmh": float(max(speeds)) if speeds else None,
                "osm_ids": [m.osm_id for m in f.members],
                "comment": None,
                "run_id": run_id,
            }
        )
        for mem in f.members:
            role = {
                "motorway_junction": f"junction_{mem.direction or 'unknown'}",
                "services": "services",
                "rest_area": "rest_area",
                "unnamed": "inferred",
            }.get(mem.kind, mem.kind)
            fmap.append(
                {
                    "facility_id": f.facility_id,
                    "osm_type": mem.osm_type,
                    "osm_id": mem.osm_id,
                    "role": role,
                    "carriageway": mem.direction,
                }
            )
        for t in f.toll_nodes:
            fmap.append(
                {
                    "facility_id": f.facility_id,
                    "osm_type": "node",
                    "osm_id": t,
                    "role": "toll",
                    "carriageway": None,
                }
            )
        cfeat.append(
            {
                "facility_id": f.facility_id,
                "run_id": run_id,
                "features": {**feats, "_scores": cr.scores},
                "classifier_version": rules.get("version"),
            }
        )
    fac_df = pd.DataFrame(fac_rows)
    if len(ramps_df):
        shape = dict(zip(fac_df["facility_id"], fac_df["shape_class"], strict=True))
        ramps_df["shape_class"] = ramps_df["facility_id"].map(shape)
    # ---- sections
    sec_rows = []
    sd_rows = []
    for s in ctx.sections:
        m = ctx.section_results[s.section_id].metrics if s.section_id in ctx.section_results else {}
        line = _line_lonlat(rg, s.geom_xy) if len(s.geom_xy) >= 2 else LineString([(0, 0), (0, 0)])
        std = _f(s.distances.get("standard"))
        gain = _f(m.get("elev_gain_m"))
        sec_rows.append(
            {
                "section_id": s.section_id,
                "route_ref": s.route_ref,
                "from_facility_id": s.from_facility_id,
                "to_facility_id": s.to_facility_id,
                "direction": s.direction,
                "dist_standard_m": std,
                "geom": _geom_wkb(line),
                "way_ids": s.way_ids,
                "grade_mean": _f(m.get("grade_mean")),
                "grade_up_max_w100": _f(m.get("grade_up_max_w100")),
                "grade_down_max_w100": _f(m.get("grade_down_max_w100")),
                "grade_up_max_w1000": _f(m.get("grade_up_max_w1000")),
                "elev_gain_m": gain,
                "elev_loss_m": _f(m.get("elev_loss_m")),
                "z_min": _f(m.get("z_min")),
                "z_max": _f(m.get("z_max")),
                "min_radius_m": _f(m.get("min_radius_m")),
                "tunnel_len_m": s.tunnel_len_m,
                "bridge_len_m": s.bridge_len_m,
                "cont_up_len_m": _f(m.get("cont_up_len_m")),
                "gain_per_km": (gain / (std / 1000.0)) if gain is not None and std and std > 0 else None,
                "tunnel_ratio": (s.tunnel_len_m / std) if std and std > 0 else None,
                "passes_non_motorway": "VIA_NON_MOTORWAY" in s.anomaly_flags,
                "anomaly_flags": sorted(set(s.anomaly_flags)),
                "excluded": False,
                "adopted_definition": "standard",
                "drive_data_available": False,
                "run_id": run_id,
            }
        )
        for d, v in s.distances.items():
            if v is None:
                continue
            p0, p1 = s.distance_points.get(d, (None, None))
            fp = tp = None
            ch = next((c for c in ctx.chains if c.chain_id == s.chain_id), None)
            if ch is not None and p0 is not None and p1 is not None:
                a = ch.line.interpolate(p0)
                b = ch.line.interpolate(p1)
                alo, ala = rg.proj.to_lonlat(a.x, a.y)
                blo, bla = rg.proj.to_lonlat(b.x, b.y)
                fp, tp = _geom_wkb(Point(float(alo), float(ala))), _geom_wkb(Point(float(blo), float(bla)))
            sd_rows.append(
                {
                    "section_id": s.section_id,
                    "definition": d,
                    "distance_m": float(v),
                    "from_point": fp,
                    "to_point": tp,
                    "note": None,
                }
            )
    sec_df = pd.DataFrame(sec_rows)
    # 上下線平均（中心線距離）
    if len(sec_df):
        for r in sec_df.itertuples():
            rev = sec_df[
                (sec_df["from_facility_id"] == r.to_facility_id)
                & (sec_df["to_facility_id"] == r.from_facility_id)
                & (sec_df["route_ref"] == r.route_ref)
            ]
            if len(rev) and r.dist_standard_m is not None:
                sd_rows.append(
                    {
                        "section_id": r.section_id,
                        "definition": "centerline_avg",
                        "distance_m": float((r.dist_standard_m + rev.iloc[0]["dist_standard_m"]) / 2),
                        "from_point": None,
                        "to_point": None,
                        "note": "上下線の standard の平均",
                    }
                )
    samples = [res.samples for res in ctx.ramp_results.values()] + [
        res.samples for res in ctx.section_results.values()
    ]
    sp = pd.concat(samples, ignore_index=True) if samples else pd.DataFrame()
    ctx.tables = {
        "facility": fac_df,
        "facility_osm_map": pd.DataFrame(fmap),
        "ramp": ramps_df,
        "ramp_way_map": pd.DataFrame(rwm),
        "section": sec_df,
        "section_distance": pd.DataFrame(sd_rows),
        "sample_point": sp,
        "classification_feature": pd.DataFrame(cfeat),
    }


def _way_order(r: Ramp) -> list[tuple[int, bool]]:
    out: list[tuple[int, bool]] = []
    for e in r.edges:
        if not out or out[-1][0] != e.data["way_id"]:
            out.append((int(e.data["way_id"]), bool(e.data.get("reversed", False))))
    return out


# ---- 段 10: quality ---------------------------------------------------------
def step_quality(ctx: Ctx) -> None:
    build_tables(ctx)
    cfg = ctx.cfg.analysis
    q = cfg["quality"]
    run_id = ctx.run_id
    flags: list[dict[str, Any]] = []
    fac = ctx.tables["facility"]
    ramps = ctx.tables["ramp"]
    secs = ctx.tables["section"]
    fobj = {f.facility_id: f for f in ctx.facs}
    # ランプ
    rlevels = []
    for r in ramps.itertuples():
        fl = []
        g100 = max(r.grade_up_max_w100 or 0.0, r.grade_down_max_w100 or 0.0)
        if g100 > q["extreme_grade_pct"]:
            fl.append(make_flag("ramp", r.ramp_id, "EXTREME_GRADE", run_id, {"g100": g100}))
        if (
            r.min_radius_m is not None
            and not pd.isna(r.min_radius_m)
            and r.min_radius_m < q["extreme_radius_m"]
        ):
            fl.append(
                make_flag("ramp", r.ramp_id, "EXTREME_CURVATURE", run_id, {"min_radius_m": r.min_radius_m})
            )
        res = ctx.ramp_results.get(r.ramp_id)
        if res is not None and res.metrics.get("dem_missing_ratio", 0) > 0:
            fl.append(
                make_flag(
                    "ramp",
                    r.ramp_id,
                    "DEM_MISSING",
                    run_id,
                    {"missing_ratio": res.metrics["dem_missing_ratio"]},
                )
            )
        if (r.tunnel_len_m or 0) > 0:
            fl.append(make_flag("ramp", r.ramp_id, "TUNNEL_DEM", run_id, {"tunnel_len_m": r.tunnel_len_m}))
        if res is not None and ((r.bridge_len_m or 0) + (r.viaduct_len_m or 0)) > 0:
            pi = res.profile_internal
            diff = np.nanmax(np.abs(pi["z_raw"] - pi["z_c"])) if np.any(~np.isnan(pi["z_raw"])) else 0
            if diff > 5:
                fl.append(
                    make_flag(
                        "ramp", r.ramp_id, "ELEVATED_GROUND_MISMATCH", run_id, {"max_diff_m": float(diff)}
                    )
                )
        if r.carriageway == "unknown" and r.flow in ("entry", "exit"):
            fl.append(make_flag("ramp", r.ramp_id, "CARRIAGEWAY_MISCONNECT", run_id))
        all_missing = res is None or res.metrics.get("dem_missing_ratio", 1.0) >= 1.0
        lvl = level_from_flags(
            fl,
            unusable=(r.length_m or 0) < 1 or all_missing,
            low=(r.dem_low_conf_ratio or 0) > q["low_conf_dem_ratio_low_quality"],
            partial=r.osm_quality != "high",
        )
        rlevels.append(lvl)
        flags += fl
    if len(ramps):
        ramps["quality_level"] = rlevels
    # 施設
    names = fac["normalized_name"].value_counts().to_dict() if len(fac) else {}
    flevels = []
    for r in fac.itertuples():
        fl = [
            make_flag(
                "facility",
                r.facility_id,
                x["code"],
                run_id,
                x.get("detail"),
                x.get("message_ja"),
                x.get("severity"),
            )
            for x in fobj[r.facility_id].flags
        ]
        if not r.normalized_name:
            fl.append(make_flag("facility", r.facility_id, "NAME_MISSING", run_id))
        if r.facility_type in ("IC", "SIC") and not r.exit_number:
            fl.append(make_flag("facility", r.facility_id, "EXIT_NUMBER_MISSING", run_id))
        nramps = int((ramps["facility_id"] == r.facility_id).sum()) if len(ramps) else 0
        if nramps == 0:
            fl.append(make_flag("facility", r.facility_id, "RAMP_MISSING", run_id))
        if r.normalized_name and names.get(r.normalized_name, 0) > 1:
            fl.append(make_flag("facility", r.facility_id, "DUPLICATE_FACILITY", run_id))
        if (
            r.shape_confidence < float(cfg["classification"]["low_confidence_threshold"])
            and r.shape_class != "SA/PA"
        ):
            fl.append(
                make_flag(
                    "facility",
                    r.facility_id,
                    "LOW_CLASSIFICATION_CONFIDENCE",
                    run_id,
                    {"confidence": r.shape_confidence},
                )
            )
        rr = ramps[ramps["facility_id"] == r.facility_id] if len(ramps) else ramps
        dem_low = float((rr["dem_quality"] == "low").mean()) if len(rr) else 0.0
        lvl = level_from_flags(
            fl,
            unusable=nramps == 0 and r.facility_type not in ("SA", "PA"),
            low=r.review_status == "needs_review" or dem_low > q["low_conf_dem_ratio_low_quality"],
            partial=not r.exit_number and r.facility_type in ("IC", "SIC"),
        )
        flevels.append(lvl)
        flags += fl
    if len(fac):
        fac["quality_level"] = flevels
    # 区間
    slevels = []
    from roadmri.network.anomalies import severity

    for r in secs.itertuples():
        fl = [make_flag("section", r.section_id, c, run_id, severity=severity(c)) for c in r.anomaly_flags]
        if r.dist_standard_m is None or r.dist_standard_m <= 0 or r.dist_standard_m > 100_000:
            fl.append(
                make_flag(
                    "section", r.section_id, "UNNATURAL_DISTANCE", run_id, {"distance_m": r.dist_standard_m}
                )
            )
        g = max(r.grade_up_max_w100 or 0, r.grade_down_max_w100 or 0)
        if g > q["extreme_grade_pct"]:
            fl.append(make_flag("section", r.section_id, "EXTREME_GRADE", run_id, {"g100": g}))
        slevels.append(level_from_flags(fl, unusable="NO_PATH" in r.anomaly_flags))
        flags += fl
    if len(secs):
        secs["quality_level"] = slevels
    for gp in ctx.gaps:
        flags.append(make_flag("route", gp["chain_a"], "NETWORK_GAP", run_id, gp))
    ctx.flags = flags


# ---- 段 11: edits -----------------------------------------------------------
def step_edits(ctx: Ctx, store: Store | None) -> None:
    if store is None:
        ctx.resolutions = []
        return
    edits = effective_edits(store)
    ramp_ways: dict[str, set[int]] = {}
    for r in ctx.ramps:
        ramp_ways[r.ramp_id] = {int(e.data["way_id"]) for e in r.edges}
    ctx.resolutions = apply_edits(edits, ctx.tables, ramp_ways, ctx.run_id)
    fac = ctx.tables["facility"]
    if len(fac) and len(ctx.tables["ramp"]):
        shape = dict(zip(fac["facility_id"], fac["shape_class"], strict=True))
        ctx.tables["ramp"]["shape_class"] = ctx.tables["ramp"]["facility_id"].map(shape)
    for res in ctx.resolutions:
        if res["status"] == "orphaned":
            ctx.flags.append(
                make_flag(
                    "edit",
                    res["edit_id"],
                    "ORPHANED_EDIT",
                    ctx.run_id,
                    {},
                    "対象が見つからない手動修正（孤立修正）です。確認キューで再割当てしてください",
                    "warn",
                )
            )


# ---- 段 12: publish ---------------------------------------------------------
def display_features(ctx: Ctx) -> pd.DataFrame:
    assert ctx.rg is not None
    rg = ctx.rg
    rows: list[dict[str, Any]] = []
    sp = ctx.tables.get("sample_point", pd.DataFrame())
    ms = sp[sp["element_type"] == "mainline"] if len(sp) else sp
    tree = cKDTree(ms[["x", "y"]].to_numpy()) if len(ms) else None
    # 行ごとの pandas アクセスは遅いので、列を NumPy 配列にしておく
    ms_cols = (
        {c: ms[c].to_numpy(dtype=float) for c in ("grade_w100", "z_corrected", "curvature")}
        if len(ms)
        else {}
    )
    ms_low = ms["low_confidence"].to_numpy(dtype=bool) if len(ms) else np.zeros(0, dtype=bool)

    def add(layer: str, fid: str, props: dict[str, Any], g: BaseGeometry) -> None:
        b = g.bounds
        rows.append(
            {
                "layer": layer,
                "feature_id": fid,
                "props": props,
                "geom": _geom_wkb(g),
                "minx": b[0],
                "miny": b[1],
                "maxx": b[2],
                "maxy": b[3],
            }
        )

    # 本線：チェーンを 100 m ごとに分割し、最寄りの区間サンプル点の指標を付ける
    for ch in ctx.chains:
        L = ch.line.length
        n = max(1, math.ceil(L / 100.0))
        for i in range(n):
            a, b = L * i / n, L * (i + 1) / n
            if L == 0:
                continue
            xy = ch.slice_xy(a, b)
            seg = LineString(xy)
            mid = seg.interpolate(0.5, normalized=True)
            e = _edge_at(ch, (a + b) / 2)
            props: dict[str, Any] = {
                "route_ref": ch.route_ref,
                "direction": ch.direction,
                "structure": e.data["structure"] if e else "none",
                "maxspeed": parse_speed_kmh(e.data["tags"].get("maxspeed")) if e else None,
                "lanes": _intval(e.data["tags"].get("lanes")) if e else None,
                "name": e.data.get("name") if e else None,
            }
            if tree is not None:
                d, j = tree.query([mid.x, mid.y])
                if d < 60:
                    k = int(j)
                    props.update(
                        {
                            "grade": _f(ms_cols["grade_w100"][k]),
                            "z": _f(ms_cols["z_corrected"][k]),
                            "curvature": _f(ms_cols["curvature"][k]),
                            "low_conf": bool(ms_low[k]),
                        }
                    )
            add("mainline", f"{ch.chain_id}:{i}", props, _line_lonlat(rg, xy))
    # ランプ：サンプル点 4 点（約 20 m）ごとに分割
    ramps = ctx.tables["ramp"]
    rinfo = ramps.set_index("ramp_id").to_dict("index") if len(ramps) else {}
    rs = sp[sp["element_type"] == "ramp"] if len(sp) else sp
    for rid, grp in rs.groupby("element_id", sort=True):
        info = rinfo.get(rid, {})
        g = grp.sort_values("seq")
        lon, lat = g["lon"].to_numpy(), g["lat"].to_numpy()
        gw = g["grade_w100"].to_numpy(dtype=float)
        zc = g["z_corrected"].to_numpy(dtype=float)
        kv = np.abs(g["curvature"].to_numpy(dtype=float))
        low = g["low_confidence"].to_numpy(dtype=bool)
        for i in range(0, max(1, len(g) - 1), 4):
            j = min(len(g) - 1, i + 4)
            if j <= i:
                continue
            sl = slice(i, j + 1)
            props = {
                "ramp_id": rid,
                "facility_id": info.get("facility_id"),
                "flow": info.get("flow"),
                "grade": _f(_nanmean(gw[sl])),
                "z": _f(_nanmean(zc[sl])),
                "curvature": _f(_nanmax(kv[sl])),
                "quality": info.get("quality_level"),
                "dem_quality": info.get("dem_quality"),
                "lanes": info.get("lanes"),
                "maxspeed": info.get("maxspeed_kmh"),
                "shape_class": info.get("shape_class"),
                "length_m": info.get("length_m"),
                "low_conf": bool(low[sl].any()),
            }
            add("ramps", f"{rid}:{i}", props, LineString(np.column_stack([lon[i : j + 1], lat[i : j + 1]])))
    # 一般道（表示用に簡略化）
    for e in rg.edges("road"):
        if e.key.endswith("r"):
            continue
        line = LineString(np.column_stack([e.data["lons"], e.data["lats"]])).simplify(0.00002)
        add("general", e.key, {"highway": e.data["highway"], "name": e.data.get("name")}, line)
    # 構造物
    for e in rg.edges({"mainline", "link"}):
        if e.data["structure"] in ("bridge", "viaduct", "tunnel") and not e.key.endswith("r"):
            add(
                "structures",
                e.key,
                {"kind": e.data["structure"], "cls": e.data["cls"]},
                LineString(np.column_stack([e.data["lons"], e.data["lats"]])),
            )
    fac = ctx.tables["facility"]
    for r in fac.itertuples():
        add(
            "facilities",
            r.facility_id,
            {
                "facility_id": r.facility_id,
                "name": r.name,
                "facility_type": r.facility_type,
                "shape_class": r.shape_class,
                "confidence": r.shape_confidence,
                "quality": r.quality_level,
                "review_status": r.review_status,
                "in_range": bool(r.in_range),
            },
            Point(r.lon, r.lat),
        )
    for f in ctx.facs:
        for t in f.toll_nodes:
            lo, la = rg.node_lonlat(t)
            add("tolls", f"toll:{t}", {"facility_id": f.facility_id}, Point(lo, la))
    return pd.DataFrame(rows)


def _nanmean(a: np.ndarray) -> float:
    """pandas の Series.mean（NaN を除いた平均、すべて NaN なら NaN）と同じ値。"""
    ok = ~np.isnan(a)
    return float(a[ok].sum() / ok.sum()) if ok.any() else float("nan")


def _nanmax(a: np.ndarray) -> float:
    ok = ~np.isnan(a)
    return float(a[ok].max()) if ok.any() else float("nan")


def _edge_at(ch: Chain, s: float) -> Any:
    if not ch.edges:
        return None
    index = max(0, bisect_left(ch.s, s) - 1)
    return ch.edges[min(index, len(ch.edges) - 1)]


def _intval(v: Any) -> int | None:
    try:
        return int(str(v).split(";")[0].split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def step_publish(
    ctx: Ctx, store: Store | None, started: float, disp: pd.DataFrame | None = None
) -> dict[str, Any]:
    cfg = ctx.cfg
    if disp is None:
        disp = display_features(ctx)
    ctx.tables["display_feature"] = disp
    ctx.tables["quality_flag"] = pd.DataFrame(ctx.flags)
    ctx.tables["dataset_source"] = pd.DataFrame(ctx.sources)
    ctx.tables["dem_dataset"] = pd.DataFrame(ctx.dem_meta)
    prov = provenance_doc(
        cfg,
        ctx.run_id,
        ctx.range_spec,
        _jsonable(ctx.sources),
        _jsonable(ctx.dem_meta),
        len(ctx.edit_ids),
        ctx.edit_ids[-1] if ctx.edit_ids else None,
    )
    run_row = {
        "run_id": ctx.run_id,
        "created_at": datetime.now(UTC),
        "git_commit": prov["git_commit"],
        "range_spec": ctx.range_spec,
        "params": prov["params"],
        "config_hash": cfg.config_hash,
        "classifier_version": cfg.classification_rules.get("version"),
        "manual_edit_count": len(ctx.edit_ids),
        "last_edit_id": ctx.edit_ids[-1] if ctx.edit_ids else None,
        "status": "succeeded",
        "duration_s": time.time() - started,
        "region_id": ctx.region_id,
        "provenance": prov,
    }
    if store is not None:
        for t, df in ctx.tables.items():
            store.replace_table(t, df)
        store.replace_table(
            "manual_edit_resolution",
            pd.DataFrame(ctx.resolutions, columns=["edit_id", "run_id", "status", "resolved_target_id"]),
        )
        store.append("run_metadata", [run_row])
    out = cfg.path("processed_db").parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    try:
        from roadmri.export.tiles import write_layers

        write_layers(disp, cfg.path("tiles_dir"))
    except Exception as e:
        log_event(log, "tiles_failed", error=str(e))
    return run_row


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


# ---------------------------------------------------------------------------
def _interim(cfg: Config, region_id: str) -> Path:
    return cfg.path("interim_dir") / region_id


def save_state(ctx: Ctx, step: str) -> None:
    d = _interim(ctx.cfg, ctx.region_id)
    d.mkdir(parents=True, exist_ok=True)
    if ctx.rg is not None:
        ctx.rg._mainline_index = None
    cfg = ctx.cfg
    ctx.cfg = None  # type: ignore[assignment]
    try:
        with open(d / f"{step}.pkl", "wb") as f:
            pickle.dump(ctx, f, protocol=pickle.HIGHEST_PROTOCOL)
    finally:
        ctx.cfg = cfg
    for name, df in ctx.tables.items():
        if name in ("facility", "ramp", "section") and len(df):
            with contextlib.suppress(Exception):
                df.drop(columns=[c for c in ("geom", "hull") if c in df.columns]).astype(
                    {c: str for c in df.columns if df[c].dtype == object}
                ).to_parquet(d / f"{name}.parquet")


def load_state(cfg: Config, region_id: str, step: str) -> Ctx:
    p = _interim(cfg, region_id) / f"{step}.pkl"
    if not p.exists():
        raise FileNotFoundError(f"中間成果物がありません: {p}（--from-step より前の段から実行してください）")
    with open(p, "rb") as f:
        ctx: Ctx = pickle.load(f)
    ctx.cfg = cfg
    return ctx


def split_regions(region_id: str) -> list[str]:
    """ "tochigi,michigan" / "tochigi+michigan" → 地域 ID のリスト（複数地域を 1 つの DB に公開する）。"""
    return [r.strip() for r in region_id.replace("+", ",").split(",") if r.strip()]


def run_pipeline(
    cfg: Config,
    region_id: str,
    store: Store | None = None,
    from_step: str | None = None,
    to_step: str | None = None,
    source: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    regions = split_regions(region_id)
    if len(regions) > 1:
        return run_multi(cfg, regions, store, from_step, to_step, source, progress)
    region_id = regions[0]
    started = time.time()
    fs = STEP_ALIASES.get(from_step or "ingest", from_step or "ingest")
    ts = STEP_ALIASES.get(to_step or "publish", to_step or "publish")
    if fs not in STEPS or ts not in STEPS:
        raise ValueError(f"段の指定が不正です（{', '.join(STEPS)}）")
    i0, i1 = STEPS.index(fs), STEPS.index(ts)
    ctx = load_state(cfg, region_id, STEPS[i0 - 1]) if i0 > 0 else new_ctx(cfg, region_id)
    edits = effective_edits(store) if store is not None else []
    ctx.edit_ids = sorted(e["edit_id"] for e in edits)
    for i in range(i0, i1 + 1):
        step = STEPS[i]
        t = time.time()
        sub_progress = (lambda p, m, i=i: progress((i + p) / len(STEPS), m)) if progress else None
        log_event(log, "step_start", region=region_id, step=step)
        if progress:
            progress(i / len(STEPS), f"{step} 実行中")
        if step == "ingest":
            step_ingest(ctx, source)
        elif step == "network":
            step_network(ctx)
        elif step == "facilities":
            step_facilities(ctx)
        elif step == "ramps":
            step_ramps(ctx, sub_progress)
        elif step == "sections":
            step_sections(ctx)
        elif step == "metrics":
            step_metrics(ctx, sub_progress)
        elif step == "classify":
            step_classify(ctx)
        elif step == "quality":
            dem_ids = ",".join(d["dem_id"] for d in ctx.dem_meta)
            edit_digest = hashlib.sha256(",".join(ctx.edit_ids).encode()).hexdigest()
            ctx.run_id = make_run_id(cfg.config_hash, ctx.input_hash + dem_ids + edit_digest, ctx.range_spec)
            step_quality(ctx)
        elif step == "edits":
            step_edits(ctx, store)
        elif step == "publish":
            row = step_publish(ctx, store, started)
            log_event(log, "step", step=step, seconds=round(time.time() - t, 2))
            return {"run_id": ctx.run_id, "counts": counts(ctx), "duration_s": row["duration_s"]}
        log_event(log, "step", step=step, seconds=round(time.time() - t, 2))
        checkpoint_start = time.time()
        save_state(ctx, step)
        log_event(
            log, "checkpoint", region=region_id, step=step, seconds=round(time.time() - checkpoint_start, 2)
        )
    return {
        "run_id": ctx.run_id,
        "counts": counts(ctx),
        "duration_s": time.time() - started,
        "stopped_at": ts,
    }


def counts(ctx: Ctx) -> dict[str, int]:
    return {
        "facilities": len(ctx.facs),
        "ramps": len(ctx.ramps),
        "sections": len(ctx.sections),
        "flags": len(ctx.flags),
    }


def run_multi(
    cfg: Config,
    regions: list[str],
    store: Store | None,
    from_step: str | None = None,
    to_step: str | None = None,
    source: str | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """複数地域（例：日本の関東と米国のミシガン州）を地域ごとに quality 段まで処理し、手動修正の適用と公開は
    まとめて行う。施設 ID は国コードで始まるので衝突しない。日本・北米を横断した検索・統計比較に使う。"""
    started = time.time()
    ts = STEP_ALIASES.get(to_step or "publish", to_step or "publish")
    last = "quality" if STEPS.index(ts) >= STEPS.index("quality") else ts
    ctxs: list[Ctx] = []
    for k, rid in enumerate(regions):
        sub_progress = (
            (lambda p, m, k=k, rid=rid: progress((k + p) / (len(regions) + 1), f"{rid}: {m}"))
            if progress
            else None
        )
        run_pipeline(cfg, rid, store, from_step, last, source, sub_progress)
        ctxs.append(load_state(cfg, rid, last))
    if STEPS.index(ts) < STEPS.index("edits"):
        return {"run_id": None, "regions": regions, "counts": _sum_counts(ctxs), "stopped_at": ts}
    joined = "+".join(regions)
    run_id = make_run_id(cfg.config_hash, "".join(c.run_id for c in ctxs), {"regions": regions})
    edits = effective_edits(store) if store is not None else []
    edit_ids = sorted(e["edit_id"] for e in edits)
    # 手動修正：どの地域でも解決できなかったものだけを孤立とする
    status: dict[str, dict[str, Any]] = {}
    for c in ctxs:
        c.edit_ids = edit_ids
        ramp_ways = {r.ramp_id: {int(e.data["way_id"]) for e in r.edges} for r in c.ramps}
        for res in apply_edits(edits, c.tables, ramp_ways, run_id):
            if res["status"] == "active" or res["edit_id"] not in status:
                status[res["edit_id"]] = res
        fac = c.tables["facility"]
        if len(fac) and len(c.tables["ramp"]):
            shape = dict(zip(fac["facility_id"], fac["shape_class"], strict=True))
            c.tables["ramp"]["shape_class"] = c.tables["ramp"]["facility_id"].map(shape)
    combo = Ctx(
        cfg=cfg,
        region_id=joined,
        region={"id": joined},
        country="MULTI",
        range_spec={"type": "union", "members": [c.range_spec for c in ctxs], "label": " + ".join(regions)},
    )
    combo.run_id = run_id
    combo.edit_ids = edit_ids
    combo.resolutions = list(status.values())
    combo.flags = [f for c in ctxs for f in c.flags]
    for res in combo.resolutions:
        if res["status"] == "orphaned":
            combo.flags.append(
                make_flag(
                    "edit",
                    res["edit_id"],
                    "ORPHANED_EDIT",
                    run_id,
                    {},
                    "対象が見つからない手動修正（孤立修正）です。確認キューで再割当てしてください",
                    "warn",
                )
            )
    for f in combo.flags:
        f["run_id"] = run_id
    names = set().union(*[set(c.tables) for c in ctxs])
    for name in names:
        dfs = [c.tables[name] for c in ctxs if name in c.tables and len(c.tables[name])]
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        if "run_id" in df.columns:
            df["run_id"] = run_id
        combo.tables[name] = df
    combo.sources = [s for c in ctxs for s in c.sources]
    dems: dict[str, dict[str, Any]] = {}
    for c in ctxs:
        for d in c.dem_meta:
            dems.setdefault(d["dem_id"], d)
    combo.dem_meta = list(dems.values())
    combo.facs = [f for c in ctxs for f in c.facs]
    combo.ramps = [r for c in ctxs for r in c.ramps]
    combo.sections = [s for c in ctxs for s in c.sections]
    disp = pd.concat([display_features(c) for c in ctxs], ignore_index=True)
    if progress:
        progress(len(regions) / (len(regions) + 1), "publish 実行中")
    row = step_publish(combo, store, started, disp)
    return {"run_id": run_id, "regions": regions, "counts": counts(combo), "duration_s": row["duration_s"]}


def _sum_counts(ctxs: list[Ctx]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in ctxs:
        for k, v in counts(c).items():
            out[k] = out.get(k, 0) + v
    return out
