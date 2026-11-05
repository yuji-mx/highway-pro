"""道路 MRI 画像（Matplotlib、§4.17.2）。背景地図なしの発光表現。PNG / JPEG / SVG。"""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.collections import LineCollection
from shapely import wkb

from roadmri.config import Config

_FONT = Path(__file__).parent / "fonts" / "NotoSansJP-Regular.ttf"
if _FONT.exists():
    font_manager.fontManager.addfont(str(_FONT))
    matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=str(_FONT)).get_name()
import itertools

from roadmri.provenance import attribution_text
from roadmri.store.duckdb_store import Store

COLORS = {
    "mainline": "#eef3f9",
    "ramp": "#38e1ff",
    "jct": "#ffd84a",
    "general": (110 / 255, 125 / 255, 140 / 255, 0.30),
    "target": "#ff7a2e",
    "IC": "#ffb53d",
    "SIC": "#ffb53d",
    "JCT": "#a98bff",
    "SA": "#47e08f",
    "PA": "#47e08f",
    "bridge": (169 / 255, 139 / 255, 1.0, 0.55),
    "tunnel": (170 / 255, 180 / 255, 190 / 255, 0.9),
}
GRADE_STOPS = [
    (-8, (60, 120, 255)),
    (-4, (56, 225, 255)),
    (0, (255, 255, 255)),
    (3, (255, 181, 61)),
    (5.5, (255, 122, 46)),
    (8, (255, 60, 60)),
]
METRICS = ("road_type", "grade", "elevation", "radius", "quality")


def _interp_color(stops: list[tuple[float, tuple[int, int, int]]], v: float) -> tuple[float, float, float]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return (0.6, 0.6, 0.6)
    if v <= stops[0][0]:
        c = stops[0][1]
    elif v >= stops[-1][0]:
        c = stops[-1][1]
    else:
        for (a, ca), (b, cb) in itertools.pairwise(stops):
            if a <= v <= b:
                t = (v - a) / (b - a)
                c = tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))  # type: ignore[assignment]
                break
    return (c[0] / 255, c[1] / 255, c[2] / 255)


def color_for(metric: str, props: dict[str, Any], layer: str, zrange: tuple[float, float]) -> Any:
    if metric == "grade" and props.get("grade") is not None:
        return _interp_color(GRADE_STOPS, float(props["grade"]))
    if metric == "elevation" and props.get("z") is not None:
        t = (float(props["z"]) - zrange[0]) / max(1e-6, zrange[1] - zrange[0])
        stops = [(0, (120, 60, 200)), (0.35, (60, 120, 255)), (0.7, (56, 225, 255)), (1, (255, 255, 255))]
        return _interp_color(stops, t)
    if metric == "radius" and props.get("curvature"):
        k = abs(float(props["curvature"]))
        r = 1 / k if k > 0 else 3000
        t = (math.log10(max(30, min(3000, r))) - math.log10(30)) / 2
        return _interp_color([(0, (255, 60, 60)), (0.5, (255, 181, 61)), (1, (255, 255, 255))], t)
    if metric == "quality":
        return {"high": "#47e08f", "medium": "#ffb53d", "low": "#ff5b4f"}.get(
            str(props.get("quality")), "#7d8b9a"
        )
    if layer == "ramps":
        return COLORS["jct"] if props.get("flow") == "jct_link" else COLORS["ramp"]
    return COLORS["mainline"]


def load_display(
    store: Store, bbox: tuple[float, float, float, float] | None, layers: list[str] | None = None
) -> pd.DataFrame:
    sql = "SELECT layer, feature_id, props, geom FROM display_feature WHERE TRUE"
    params: list[Any] = []
    if bbox:
        sql += " AND maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ?"
        params += [bbox[0], bbox[2], bbox[1], bbox[3]]
    if layers:
        sql += f" AND layer IN ({', '.join('?' for _ in layers)})"
        params += layers
    return store.df(sql, params)


def bbox_around(lon: float, lat: float, radius_m: float) -> tuple[float, float, float, float]:
    dlat = radius_m / 110_950
    dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def render_mri(
    store: Store,
    bbox: tuple[float, float, float, float],
    size: tuple[int, int] = (1920, 1080),
    fmt: str = "png",
    background: str = "#000000",
    line_scale: float = 1.0,
    metric: str = "road_type",
    labels: bool = True,
    highlight_ramps: set[str] | None = None,
    highlight_facility: str | None = None,
    layers: list[str] | None = None,
    dem_names: list[str] | None = None,
    scale_m_per_px: float | None = None,
) -> bytes:
    """表示範囲の道路 MRI 画像を描画してバイト列で返す。scale_m_per_px 指定時は bbox の中心から範囲を決める。"""
    w, h = size
    lon0 = (bbox[0] + bbox[2]) / 2
    lat0 = (bbox[1] + bbox[3]) / 2
    kx = 111_320 * math.cos(math.radians(lat0))
    ky = 110_950
    if scale_m_per_px:
        hw, hh = w * scale_m_per_px / 2, h * scale_m_per_px / 2
    else:
        hw = (bbox[2] - bbox[0]) * kx / 2
        hh = (bbox[3] - bbox[1]) * ky / 2
        # アスペクト比を画像に合わせる
        if hw / hh > w / h:
            hh = hw * h / w
        else:
            hw = hh * w / h
    view = (lon0 - hw / kx, lat0 - hh / ky, lon0 + hw / kx, lat0 + hh / ky)
    df = load_display(store, view, layers)
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor=background)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_facecolor(background)
    ax.set_xlim(-hw, hw)
    ax.set_ylim(-hh, hh)
    ax.set_aspect("equal")
    ax.axis("off")
    lw0 = line_scale * max(0.6, min(w, h) / 900)

    def proj(coords: np.ndarray) -> np.ndarray:
        return np.column_stack([(coords[:, 0] - lon0) * kx, (coords[:, 1] - lat0) * ky])

    feats: dict[str, list[tuple[np.ndarray, dict[str, Any]]]] = {}
    points: list[tuple[str, float, float, dict[str, Any]]] = []
    zs = []
    for r in df.itertuples():
        g = wkb.loads(bytes(r.geom))
        props = json.loads(r.props) if isinstance(r.props, str) else (r.props or {})
        if g.geom_type == "Point":
            points.append((r.layer, *proj(np.array([[g.x, g.y]]))[0], props))
            continue
        feats.setdefault(r.layer, []).append((proj(np.asarray(g.coords)), props))
        if props.get("z") is not None:
            zs.append(float(props["z"]))
    zr = (float(np.percentile(zs, 2)), float(np.percentile(zs, 98))) if zs else (30.0, 550.0)
    for layer, glow in (("general", False), ("structures", False), ("mainline", True), ("ramps", True)):
        items = feats.get(layer, [])
        if not items:
            continue
        segs = [c for c, _ in items]
        if layer == "general":
            ax.add_collection(LineCollection(segs, colors=[COLORS["general"]], linewidths=lw0 * 1.0))
            continue
        if layer == "structures":
            cols = [COLORS["tunnel"] if p.get("kind") == "tunnel" else COLORS["bridge"] for _, p in items]
            styles = ["--" if p.get("kind") == "tunnel" else "-" for _, p in items]
            ax.add_collection(LineCollection(segs, colors=cols, linewidths=lw0 * 4, linestyles=styles))
            continue
        cols = [color_for(metric, p, layer, zr) for _, p in items]
        base = 1.2 if layer == "mainline" else 1.0
        if glow:
            for wmul, alpha in ((7, 0.10), (3, 0.22)):
                ax.add_collection(
                    LineCollection(segs, colors=cols, linewidths=lw0 * base * wmul, alpha=alpha)
                )
        ax.add_collection(LineCollection(segs, colors=cols, linewidths=lw0 * base, alpha=1.0))
    if highlight_ramps:
        hs = [c for c, p in feats.get("ramps", []) if p.get("ramp_id") in highlight_ramps]
        if hs:
            for hmul, halpha in ((7.0, 0.15), (3.0, 0.35), (1.4, 1.0)):
                ax.add_collection(
                    LineCollection(
                        hs, colors=COLORS["target"], linewidths=lw0 * 2.2 * hmul / 1.4, alpha=halpha
                    )
                )
    for layer, x, y, p in points:
        if layer == "facilities":
            col: Any = COLORS.get(str(p.get("facility_type")), COLORS["IC"])
            sel = p.get("facility_id") == highlight_facility
            for msize, a in ((260.0, 0.08), (90.0, 0.2), (22.0, 1.0)):
                ax.scatter(
                    [x],
                    [y],
                    s=msize * lw0,
                    color=col if not sel or a < 1 else "#ffffff",
                    alpha=a,
                    linewidths=0,
                )
            if labels and p.get("name"):
                ax.text(
                    x,
                    y + 0.012 * hh * 2,
                    str(p["name"]),
                    color="#c9b8ff" if p.get("facility_type") == "JCT" else "#aebccb",
                    fontsize=max(7, 11 * min(w, h) / 1080),
                    ha="center",
                    va="bottom",
                    path_effects=[_stroke()],
                )
        elif layer == "tolls":
            ax.scatter([x], [y], s=12 * lw0, color=str(COLORS["IC"]), marker="s", linewidths=0)
    # 縮尺バー・出典
    bar = _nice(hw * 0.4)
    ax.plot([-hw * 0.92, -hw * 0.92 + bar], [-hh * 0.9, -hh * 0.9], color="#d7dee7", lw=2)
    ax.text(-hw * 0.92, -hh * 0.86, f"{bar:,.0f} m", color="#d7dee7", fontsize=9)
    attr = attribution_text([{"attribution": d} for d in (dem_names or [])])
    ax.text(hw * 0.98, -hh * 0.96, attr, color="#7d8b9a", fontsize=8, ha="right", va="bottom")
    buf = io.BytesIO()
    fmt2 = "jpeg" if fmt in ("jpg", "jpeg") else fmt
    fig.savefig(buf, format=fmt2, facecolor=background, dpi=dpi)
    plt.close(fig)
    return buf.getvalue()


def _stroke() -> Any:
    from matplotlib import patheffects

    return patheffects.withStroke(linewidth=2.5, foreground="black")


def _nice(v: float) -> float:
    e = float(10 ** math.floor(math.log10(max(v, 1))))
    for m in (5, 2, 1):
        if m * e <= v:
            return m * e
    return e


def dem_attributions(store: Store) -> list[str]:
    try:
        return [str(a) for a in store.df("SELECT DISTINCT attribution FROM dem_dataset")["attribution"] if a]
    except Exception:
        return []


def batch_images(
    store: Store,
    cfg: Config,
    results: pd.DataFrame,
    fmt: str,
    size: tuple[int, int],
    metric: str,
    out: Path | None = None,
    radius_m: float = 1500.0,
    progress: Any = None,
) -> Path:
    base = out or cfg.path("exports_dir")
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    zpath = base / f"images_{ts}.zip"
    dems = dem_attributions(store)
    fac = store.df("SELECT facility_id, lon, lat FROM facility").set_index("facility_id")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, r in enumerate(results.to_dict("records")):
            fid = r.get("facility_id") or r.get("from_facility_id")
            if fid not in fac.index:
                continue
            lon, lat = fac.loc[fid, "lon"], fac.loc[fid, "lat"]
            rid = r.get("ramp_id")
            data = render_mri(
                store,
                bbox_around(float(lon), float(lat), radius_m),
                size,
                fmt,
                metric=metric,
                highlight_ramps={rid} if rid else None,
                highlight_facility=fid,
                dem_names=dems,
            )
            name = f"{fid}_{rid}_{metric}.{fmt}" if rid else f"{fid}_{metric}.{fmt}"
            z.writestr(name, data)
            if progress:
                progress((i + 1) / max(1, len(results)), f"{i + 1}/{len(results)}")
        from roadmri.export.tabular import provenance

        z.writestr(
            "provenance.json", json.dumps(provenance(store), ensure_ascii=False, indent=2, default=str)
        )
    return zpath
