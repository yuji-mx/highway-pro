"""画像レポート（§4.17.3）。自己完結 HTML ＋画像フォルダ。PDF は Playwright がある場合のみ。"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from roadmri.analysis.query import FLOW_JA, to_text
from roadmri.config import Config
from roadmri.export.images import bbox_around, dem_attributions, render_mri
from roadmri.export.tabular import provenance
from roadmri.provenance import attribution_text
from roadmri.store.duckdb_store import Store

TEMPLATES = Path(__file__).parent / "templates"


def _profile_png(sp: pd.DataFrame, kind: str) -> bytes:
    fig, ax = plt.subplots(figsize=(7.2, 2.4), dpi=110, facecolor="#0c1015")
    ax.set_facecolor("#0c1015")
    for spn in ax.spines.values():
        spn.set_color("#28323e")
    ax.tick_params(colors="#7d8b9a", labelsize=8)
    s = sp["s_m"].to_numpy()
    if kind == "elevation":
        ax.plot(s, sp["z_dem_raw"], color="#7d8b9a", lw=0.8, label="DEM 生値")
        ax.plot(s, sp["z_corrected"], color="#38e1ff", lw=1.6, label="補正後")
        low = sp["low_confidence"].to_numpy(dtype=bool)
        if low.any():
            ymin, ymax = np.nanmin(sp["z_dem_raw"]), np.nanmax(sp["z_dem_raw"])
            ax.fill_between(s, ymin, ymax, where=low, color="#a98bff", alpha=0.15, label="構造物・低信頼")
        ax.set_ylabel("標高 [m]", color="#7d8b9a", fontsize=8)
    elif kind == "grade":
        ax.plot(s, sp["grade_inst"], color="#4a5663", lw=0.8, label="瞬間（表示用）")
        ax.plot(s, sp["grade_w100"], color="#ffb53d", lw=1.6, label="100 m 窓平均")
        ax.axhline(0, color="#28323e", lw=0.8)
        ax.set_ylabel("勾配 [%]", color="#7d8b9a", fontsize=8)
    else:
        ax.plot(s, sp["curvature"], color="#a98bff", lw=1.4, label="曲率（左正）")
        ax.axhline(0, color="#28323e", lw=0.8)
        ax.set_ylabel("曲率 [1/m]", color="#7d8b9a", fontsize=8)
    ax.set_xlabel("距離 [m]", color="#7d8b9a", fontsize=8)
    ax.legend(fontsize=7, facecolor="#11161d", edgecolor="#28323e", labelcolor="#d7dee7")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="#0c1015")
    plt.close(fig)
    return buf.getvalue()


def build_report(
    store: Store,
    cfg: Config,
    results: pd.DataFrame,
    query: dict[str, Any] | None,
    out: Path | None = None,
    pdf: bool = False,
    progress: Any = None,
) -> Path:
    base = out or cfg.path("exports_dir")
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    work = base / f"report_{ts}"
    img_dir = work / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    dems = dem_attributions(store)
    fac = store.df("SELECT * EXCLUDE (geom, hull) FROM facility").set_index("facility_id")
    items = []
    for i, r in enumerate(results.to_dict("records")):
        rid = r.get("ramp_id")
        fid = r.get("facility_id") or r.get("from_facility_id")
        if fid not in fac.index:
            continue
        f = fac.loc[fid]
        bb = bbox_around(float(f["lon"]), float(f["lat"]), 1200)
        key = rid or fid
        mri = render_mri(
            store,
            bb,
            (1200, 800),
            "png",
            metric="grade",
            highlight_ramps={rid} if rid else None,
            highlight_facility=fid,
            dem_names=dems,
        )
        loc = render_mri(
            store,
            bbox_around(float(f["lon"]), float(f["lat"]), 4000),
            (1200, 800),
            "png",
            background="#11161d",
            labels=True,
            highlight_ramps={rid} if rid else None,
            highlight_facility=fid,
            dem_names=dems,
            layers=["general", "mainline", "ramps", "facilities"],
        )
        files = {"mri": f"{key}_mri.png", "loc": f"{key}_location.png"}
        (img_dir / files["mri"]).write_bytes(mri)
        (img_dir / files["loc"]).write_bytes(loc)
        profiles = {}
        if rid:
            sp = store.df(
                "SELECT * FROM sample_point WHERE element_type='ramp' AND element_id=? ORDER BY seq", [rid]
            )
            for kind in ("elevation", "grade", "curvature"):
                name = f"{key}_{kind}.png"
                (img_dir / name).write_bytes(_profile_png(sp, kind))
                profiles[kind] = name
        flags = store.df(
            "SELECT severity, message_ja FROM quality_flag WHERE target_id IN (?, ?)", [rid or "", fid]
        )
        specs = []
        if rid:
            for k, lab, unit, d in (
                ("flow", "区分", "", None),
                ("length_m", "ランプ長", "m", 0),
                ("min_radius_m", "最小曲率半径", "m", 0),
                ("grade_up_max_w100", "100m 平均上り", "%", 2),
                ("grade_down_max_w100", "100m 平均下り", "%", 2),
                ("elev_diff_m", "標高差", "m", 1),
                ("z_max", "最高標高", "m", 0),
                ("difficulty", "難易度候補値（目安）", "", 1),
                ("dem_quality", "DEM 品質", "", None),
                ("osm_quality", "OSM 品質", "", None),
            ):
                v = r.get(k)
                if k == "flow":
                    v = FLOW_JA.get(str(v), v)
                elif d is not None and v is not None and not pd.isna(v):
                    v = f"{float(v):.{d}f} {unit}".strip()
                specs.append((lab, "—" if v is None or (isinstance(v, float) and pd.isna(v)) else v))
        items.append(
            {
                "index": i + 1,
                "ramp_id": rid,
                "facility_id": fid,
                "facility_name": f["name"],
                "facility_type": f["facility_type"],
                "shape_class": f["shape_class"],
                "lon": float(f["lon"]),
                "lat": float(f["lat"]),
                "files": files,
                "profiles": profiles,
                "specs": specs,
                "flags": flags.to_dict("records"),
            }
        )
        if progress:
            progress((i + 1) / max(1, len(results)), f"{i + 1}/{len(results)}")
    prov = provenance(store)
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    font_b64 = ""
    html = env.get_template("report.html.j2").render(
        title="ROAD MRI 画像レポート",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        query_text=to_text(query) if query else "（指定なし）",
        items=items,
        provenance=json.dumps(prov, ensure_ascii=False, indent=2, default=str),
        attribution=attribution_text([{"attribution": d} for d in dems]),
        font_b64=font_b64,
    )
    (work / "report.html").write_text(html, encoding="utf-8")
    (work / "provenance.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if pdf:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page()
                pg.goto((work / "report.html").resolve().as_uri())
                pg.pdf(path=str(work / "report.pdf"), format="A4", print_background=True)
                b.close()
        except Exception:
            pass
    zpath = base / f"report_{ts}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fpath in sorted(work.rglob("*")):
            if fpath.is_file():
                z.write(fpath, fpath.relative_to(work))
    return zpath
