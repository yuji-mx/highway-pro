"""検証用 CLI（roadmri accel-photo・accel-eval）の本体。serve からは呼ばない（ADR-0020）。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point

from roadmri.config import Config
from roadmri.validation.accel_eval import (
    Site,
    Thresholds,
    check_classification,
    report_markdown,
    score_site,
    sites_from_json,
    sites_from_study,
    summarize,
)


def _provenance(cfg: Config, kind: str, inputs: dict[str, Any]) -> dict[str, Any]:
    from roadmri.provenance import git_commit

    blob = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256((cfg.config_hash + blob).encode()).hexdigest()[:16]
    return {
        "run_id": f"{kind}-{digest}",
        "provenance": {
            "kind": kind,
            "created_at": datetime.now(UTC).isoformat(),
            "git_commit": git_commit(cfg.root),
            "config_hash": cfg.config_hash,
            "settings": cfg.settings.get("validation", {}),
            "inputs": inputs,
            "adopted_to_production": False,  # 本番の自動値には採用しない
        },
    }


# ---- accel-photo -------------------------------------------------------------------------------------------


def _ramp_context(cfg: Config, store: Any, ramp_id: str) -> tuple[Any, Any, dict[str, Any]]:
    from roadmri.pipeline import load_state, split_regions

    row = store.one(
        "SELECT r.*, f.country FROM ramp r LEFT JOIN facility f USING (facility_id) WHERE r.ramp_id=?",
        [ramp_id],
    )
    if row is None:
        raise LookupError(f"ランプが見つかりません: {ramp_id}")
    run = store.current_run()
    for region in split_regions(run["region_id"]):
        ctx = load_state(cfg, region, "sections")
        ramp = next((r for r in ctx.ramps if r.ramp_id == ramp_id), None)
        if ramp is not None:
            return ctx, ramp, row
    raise LookupError(f"中間成果物にランプがありません: {ramp_id}（roadmri build を実行してください）")


def photo_candidates(
    cfg: Config, store: Any, ramps: list[tuple[str | None, str]], offline: bool
) -> dict[str, Any]:
    """ramps：[(地点コード, ramp_id)]。各ランプの補正候補と、画像端での延長の記録。"""
    from roadmri.ingest.http import HttpClient
    from roadmri.network.accel_lane import _main_edges, lane_path, lanes_of
    from roadmri.validation.accel_photo import TileMosaic, _stations, detect

    pc = cfg.settings["validation"]["accel_photo"]
    sources = {**(cfg.settings.get("imagery", {}).get("sources") or {}), **(pc.get("sources") or {})}
    http = HttpClient(cfg.settings, offline=offline)
    cache = cfg.path("cache_dir") / "imagery"
    lane_w = cfg.analysis["accel_lane"]["lane_width_m"]
    search0 = float(cfg.analysis["accel_lane"]["max_search_m"])
    rows: list[dict[str, Any]] = []
    for code, rid in ramps:
        ctx, ramp, row = _ramp_context(cfg, store, rid)
        country = row.get("country")
        src_id = pc["imagery"].get(country or "", pc["imagery"]["default"])
        spec = sources[src_id]
        g, proj = ctx.rg.g, ctx.rg.proj
        ramp_xy = ctx.rg.edges_xy(ramp.edges)
        merge_s = float(_stations(np.asarray(ramp_xy, dtype=float))[-1])
        down = _main_edges(g, ramp.nodes[-1], None, upstream=False)
        lanes = lanes_of(down[0][2]) if down else None
        w = float(lane_w.get(country or "", lane_w["default"]))
        main_half = lanes * w / 2.0 if lanes else None
        zoom = min(int(pc["zoom"]), int(spec["max_zoom"]))
        downstream = float(pc["downstream_m"])
        tries: list[dict[str, Any]] = []
        cand = None
        for attempt in range(int(pc["max_extensions"]) + 1):
            path = lane_path(g, ramp_xy, ramp.nodes[-1], max(search0, downstream))
            line = LineString(path)
            baseline = None
            if row.get("accel_lane_status") == "measured" and row.get("accel_start_lon") is not None:
                sx, sy = proj.to_xy(float(row["accel_start_lon"]), float(row["accel_start_lat"]))
                ex, ey = proj.to_xy(float(row["accel_end_lon"]), float(row["accel_end_lat"]))
                baseline = [float(line.project(Point(sx, sy))), float(line.project(Point(ex, ey)))]
            mosaic = TileMosaic(http, str(spec["url"]), zoom, cache, src_id)
            cand = detect(
                np.asarray(path, dtype=float),
                proj.to_lonlat,
                mosaic.sample,
                rid,
                country,
                merge_s,
                baseline,
                pc,
                main_half,
            )
            tries.append(
                {
                    "downstream_m": downstream,
                    "zoom": zoom,
                    "tiles_fetched": mosaic.fetched,
                    "held": cand.held or None,
                }
            )
            if not any(d.detail.get("extend") for d in (cand.start, cand.end)) or attempt == int(
                pc["max_extensions"]
            ):
                break
            # 画像端：下流へ延ばし、元の解像度（取得元の最大ズーム）で取り直す
            downstream += float(pc["extend_m"])
            zoom = int(spec["max_zoom"])
        assert cand is not None
        d = cand.as_dict()
        d.update(
            code=code,
            imagery={"source": src_id, "attribution": spec.get("attribution")},
            attempts=tries,
            current={
                "status": row.get("accel_lane_status"),
                "reason": row.get("accel_lane_reason"),
                "length_m": row.get("accel_lane_len_m"),
            },
        )
        rows.append(d)
    return {**_provenance(cfg, "accel-photo", {"ramps": ramps, "offline": offline}), "predictions": rows}


# ---- accel-eval --------------------------------------------------------------------------------------------


def _app_values(store: Any, code_to_ramp: dict[str, str]) -> dict[str, dict[str, Any]]:
    out = {}
    for code, rid in code_to_ramp.items():
        r = store.one(
            "SELECT accel_lane_status, accel_lane_reason, accel_lane_len_m FROM ramp WHERE ramp_id=?", [rid]
        )
        if r is None:
            continue
        from roadmri.network.accel_lane import status_of

        st = r["accel_lane_status"]
        out[code] = {
            "ramp_id": rid,
            "status": "measured" if st == "measured" else status_of(r["accel_lane_reason"]),
            "reason": r["accel_lane_reason"],
            "length_m": r["accel_lane_len_m"] if st == "measured" else None,
        }
    return out


def evaluate(
    cfg: Config,
    sites_json: Path | None,
    study: Path | None,
    method: str,
    photo: Path | None,
    store: Any | None,
) -> tuple[dict[str, Any], str]:
    """採点。sites_json（地点の一覧）・study（data/validation/accel_regional）・photo（accel-photo の出力）のいずれか。"""
    th = Thresholds.from_settings(cfg.settings)
    sites: list[Site] = []
    refs: dict[str, dict[str, Any]] = {}
    if sites_json:
        sites += sites_from_json(json.loads(sites_json.read_text(encoding="utf-8")))
    if study:
        sites += sites_from_study(study, method)
        p = study / "references.json"
        refs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if photo:
        data = json.loads(photo.read_text(encoding="utf-8"))
        ref_path = photo.parent / "references.json"
        prefs = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else {}
        for r in data["predictions"]:
            ref = prefs.get(r.get("code") or "", {})
            if not ref:
                continue
            sites.append(
                Site(
                    r["code"], r["ramp_id"], r.get("country"), str(ref.get("type")), ref.get("start"), ref.get("end"),
                    None, r.get("baseline"), r.get("adjusted"), evidence={},
                    held_reason=",".join(f"{k}:{v}" for k, v in (r.get("held") or {}).items()) or None,
                )
            )  # fmt: skip
        refs = {**refs, **prefs}
    results = [score_site(s, th) for s in sites]
    summary = summarize(results, th.endpoint_m)
    classification: list[dict[str, Any]] = []
    if store is not None:
        code_to_ramp = {s.code: s.ramp_id for s in sites if s.ramp_id}
        manifest = (study / "manifest.json") if study else None
        if manifest and manifest.exists():
            for m in json.loads(manifest.read_text(encoding="utf-8")):
                if isinstance(m, dict) and m.get("code") and m.get("ramp_id"):
                    code_to_ramp.setdefault(m["code"], m["ramp_id"])
        types = {s.code: {"type": s.ref_type} for s in sites}
        classification = check_classification({**types, **refs}, _app_values(store, code_to_ramp))
    inputs = {
        "sites_json": str(sites_json) if sites_json else None,
        "study": str(study) if study else None,
        "method": method,
        "photo": str(photo) if photo else None,
    }
    doc = {
        **_provenance(cfg, "accel-eval", inputs),
        "summary": summary,
        "results": results,
        "classification": classification,
    }
    return doc, report_markdown(results, summary, classification)
