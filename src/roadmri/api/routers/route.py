from __future__ import annotations

import json
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from roadmri.api.deps import AppState, state
from roadmri.api.schemas.models import RouteRequest
from roadmri.api.util import py, records

router = APIRouter(tags=["route"])


def _route(s: AppState, body: RouteRequest) -> dict[str, Any]:
    from roadmri.analysis.indicators import ElementGeom, analyze_element
    from roadmri.network.routing import best_pair, find_routes, routing_graph, snap_candidates, summarize
    from roadmri.pipeline import make_dem_sampler

    ctx = s.network(body.from_facility_id)
    rg = ctx.rg
    jct_keys = {e.key for r in ctx.ramps if r.flow == "jct_link" for e in r.edges}
    dg = routing_graph(rg, jct_keys)
    fac = {f.facility_id: f for f in ctx.facs}

    def node_for(fid: str | None, pt: list[float] | None, first: bool) -> list[int]:
        if fid:
            if fid not in fac:
                raise HTTPException(404, f"施設が見つかりません: {fid}")
            f = fac[fid]
            lon, lat = f.lon, f.lat
        elif pt:
            lon, lat = pt
        else:
            raise HTTPException(422, "起点・終点を施設または座標で指定してください")
        return snap_candidates(rg, dg, lon, lat)

    pair = best_pair(
        dg,
        node_for(body.from_facility_id, body.from_point, True),
        node_for(body.to_facility_id, body.to_point, False),
    )
    if pair is None:
        raise HTTPException(404, "本線ネットワーク上で経路が見つかりません（NO_PATH）")
    src, dst = pair
    via = [node_for(v, None, False)[0] for v in body.via_facility_ids]
    acfg = dict(s.cfg.analysis)
    routes = find_routes(
        rg, dg, src, dst, via, int(acfg["route"]["max_alternatives"]), float(acfg["route"]["max_overlap"])
    )
    if not routes:
        raise HTTPException(404, "本線ネットワーク上で経路が見つかりません（NO_PATH）")
    acfg["_composite"] = s.cfg.difficulty["composite"]
    sampler, _ = make_dem_sampler(ctx, offline=True)
    conn: dict[int, list[str]] = {}
    for r in ctx.ramps:
        for n in (r.nodes[0], r.nodes[-1]):
            conn.setdefault(n, []).append(r.facility_id)
    out = []
    for i, rt in enumerate(routes):
        geom = ElementGeom("mainline", f"route-{i}", rt.xy, [(0, 0, "none", 0)])
        acc = 0.0
        struct = []
        for e in rt.edges:
            struct.append((acc, acc + e.data["length_m"], e.data["structure"], int(e.data["layer"])))
            acc += e.data["length_m"]
        geom.struct = struct
        res = analyze_element(geom, rg.proj, sampler, acfg)
        passed: list[str] = []
        pos: list[float] = []
        acc = 0.0
        for e in rt.edges:
            for fid in conn.get(e.u, []):
                if not passed or passed[-1] != fid:
                    passed.append(fid)
                    pos.append(acc)
            acc += e.data["length_m"]
        legs = [
            {"from": passed[k], "to": passed[k + 1], "distance_m": pos[k + 1] - pos[k]}
            for k in range(len(passed) - 1)
        ]
        ramps_on = sorted(
            {
                r.ramp_id
                for r in ctx.ramps
                if r.flow == "jct_link" and {e.key for e in r.edges} & {e.key for e in rt.edges}
            }
        )
        m = res.metrics
        out.append(
            {
                "index": i,
                **summarize(rt),
                "max_grade_up_w100": m.get("grade_up_max_w100"),
                "max_grade_down_w100": m.get("grade_down_max_w100"),
                "elev_gain_m": m.get("elev_gain_m"),
                "min_radius_m": m.get("min_radius_m"),
                "passed_facilities": [
                    {"facility_id": f, "name": fac[f].name, "s_m": p}
                    for f, p in zip(passed, pos, strict=True)
                ],
                "legs": legs,
                "ramps": ramps_on,
                "geometry": {
                    "type": "LineString",
                    "coordinates": np.column_stack([rt.lons, rt.lats]).tolist(),
                },
                "profile": records(
                    res.samples[
                        [
                            "s_m",
                            "lon",
                            "lat",
                            "z_dem_raw",
                            "z_corrected",
                            "grade_w100",
                            "curvature",
                            "structure",
                        ]
                    ]
                ),
            }
        )
    return {"routes": py(out)}


@router.post("/route")
def route(body: RouteRequest, format: str = "json", s: AppState = Depends(state)) -> Any:
    res = _route(s, body)
    if format == "geojson":
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": r["geometry"],
                    "properties": {
                        k: r[k]
                        for k in ("index", "length_m", "max_grade_up_w100", "elev_gain_m", "min_radius_m")
                    },
                }
                for r in res["routes"]
            ],
        }
        return Response(json.dumps(fc, ensure_ascii=False), media_type="application/geo+json")
    if format == "csv":
        import pandas as pd

        rows = []
        for r in res["routes"]:
            for p in r["profile"]:
                rows.append({"route_index": r["index"], **p})
        return Response(pd.DataFrame(rows).to_csv(index=False), media_type="text/csv; charset=utf-8")
    return res
