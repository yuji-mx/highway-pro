"""範囲統計比較（§4.14）。"""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from roadmri.analysis import stats as st
from roadmri.analysis.metrics import METRICS
from roadmri.analysis.ranges import range_label, resolve_facilities
from roadmri.analysis.search import run_query
from roadmri.config import Config
from roadmri.provenance import attribution_text
from roadmri.store.duckdb_store import Store


def _sets(
    store: Store,
    spec: dict[str, Any],
    fac: pd.DataFrame,
    ramps: pd.DataFrame,
    secs: pd.DataFrame,
    include_toll: bool,
    section_direction: str,
) -> dict[str, set[str]]:
    fids = resolve_facilities(spec, fac, lambda q: run_query(store, q))
    r = ramps[ramps["facility_id"].isin(fids)]
    if not include_toll:
        r = r[r["flow"] != "toll_link"]
    if spec.get("type") == "route":
        s = secs[secs["route_ref"].isin(spec.get("route_refs", []))]
    else:
        s = secs[secs["from_facility_id"].isin(fids) & secs["to_facility_id"].isin(fids)]
    if section_direction != "both" and len(s):
        dirs = set(s["direction"])
        want = (
            section_direction
            if section_direction in dirs
            else ("down" if "down" in dirs else sorted(dirs)[0])
        )
        s = s[s["direction"] == want]
    return {"facility": set(fids), "ramp": set(r["ramp_id"]), "section": set(s["section_id"])}


def compare_ranges(store: Store, cfg: Config, req: dict[str, Any]) -> dict[str, Any]:
    sc = cfg.stats
    seed = int(req.get("seed", sc["seed"]))
    n_boot = int(req.get("bootstrap", sc["bootstrap"]))
    min_n = int(sc["min_n_reference"])
    fac = store.df("SELECT * EXCLUDE (geom, hull) FROM facility WHERE coalesce(excluded,false)=false")
    ramps = store.df("SELECT * EXCLUDE (geom) FROM ramp WHERE coalesce(excluded,false)=false")
    secs = store.df("SELECT * EXCLUDE (geom) FROM section WHERE coalesce(excluded,false)=false")
    inc_toll = bool(req.get("include_toll_link", False))
    sdir = str(req.get("section_direction", cfg.analysis["section"]["default_direction_for_stats"]))
    A = _sets(store, req["range_a"], fac, ramps, secs, inc_toll, sdir)
    B = _sets(store, req["range_b"], fac, ramps, secs, inc_toll, sdir)
    overlap = {k: A[k] & B[k] for k in A}
    mode = req.get("overlap", "keep_warn")
    if mode == "exclude_both":
        A = {k: A[k] - overlap[k] for k in A}
        B = {k: B[k] - overlap[k] for k in B}
    elif mode == "keep_in_a":
        B = {k: B[k] - overlap[k] for k in B}
    tables = {"facility": (fac, "facility_id"), "ramp": (ramps, "ramp_id"), "section": (secs, "section_id")}

    def sub(target: str, ids: set[str]) -> pd.DataFrame:
        df, key = tables[target]
        return df[df[key].isin(ids)]

    keys = req.get("metrics") or sc["default_metrics"]
    metrics_out = []
    for k in keys:
        m = METRICS.get(k)
        if m is None:
            continue
        xa = m.getter(sub(m.target, A[m.target]))
        xb = m.getter(sub(m.target, B[m.target]))
        da, db = st.describe(xa), st.describe(xb)
        p, test = st.mann_whitney(xa, xb, int(sc["exact_n_max"]))
        md, ci = st.bootstrap_median_diff(xa, xb, n_boot, seed)
        both = np.concatenate([st.clean(xa), st.clean(xb)])
        edges = st.fd_bins(both, int(sc["hist_max_bins"]))
        ha = np.histogram(st.clean(xa), edges)[0].tolist() if len(st.clean(xa)) else [0] * (len(edges) - 1)
        hb = np.histogram(st.clean(xb), edges)[0].tolist() if len(st.clean(xb)) else [0] * (len(edges) - 1)
        metrics_out.append(
            {
                "key": k,
                "label": m.label,
                "unit": m.unit,
                "target": m.target,
                "digits": m.digits,
                "harsher_if_larger": m.harsher_if_larger,
                "a": da,
                "b": db,
                "median_diff": md,
                "median_diff_ci": ci,
                "cliffs_delta": st.cliffs_delta(xa, xb),
                "cohens_d": st.cohens_d(xa, xb),
                "test": test,
                "p": p,
                "q": None,
                "reference_only": min(da["n"], db["n"]) < min_n,
                "hist": {"edges": edges.tolist(), "a": ha, "b": hb},
                "ecdf": {"a": st.ecdf(xa), "b": st.ecdf(xb)},
            }
        )
    # ---- 割合・密度
    ra, rb = sub("ramp", A["ramp"]), sub("ramp", B["ramp"])
    fa, fb = sub("facility", A["facility"]), sub("facility", B["facility"])
    sa, sb = sub("section", A["section"]), sub("section", B["section"])
    hit_q = req.get("hit_query")
    summary: list[dict[str, Any]] = []

    def ratio(key: str, label: str, ka: int, na: int, kb: int, nb: int) -> None:
        p, test = st.two_proportion_test(ka, na, kb, nb)
        pa, pb = (ka / na if na else None), (kb / nb if nb else None)
        summary.append(
            {
                "key": key,
                "label": label,
                "kind": "ratio",
                "a": pa,
                "b": pb,
                "a_k": ka,
                "a_n": na,
                "b_k": kb,
                "b_n": nb,
                "a_ci": st.wilson_ci(ka, na),
                "b_ci": st.wilson_ci(kb, nb),
                "p": p,
                "test": test,
                "q": None,
                "ratio": (pb / pa) if pa and pb is not None else None,
                "diff": (pb - pa) if pa is not None and pb is not None else None,
                "reference_only": min(na, nb) < min_n,
            }
        )

    ratio(
        "loop_ratio", "ループランプ率", int(ra["is_loop"].sum()), len(ra), int(rb["is_loop"].sum()), len(rb)
    )
    ratio(
        "low_conf_ratio",
        "低信頼分類率",
        int((fa["review_status"] == "needs_review").sum()),
        len(fa),
        int((fb["review_status"] == "needs_review").sum()),
        len(fb),
    )
    ratio(
        "quality_low_ratio",
        "品質「低」率",
        int(ra["quality_level"].isin(["low", "unusable"]).sum()),
        len(ra),
        int(rb["quality_level"].isin(["low", "unusable"]).sum()),
        len(rb),
    )
    ratio(
        "dem_low_ratio",
        "DEM低信頼率",
        int((ra["dem_quality"] == "low").sum()),
        len(ra),
        int((rb["dem_quality"] == "low").sum()),
        len(rb),
    )
    hit_a = hit_b = None
    if hit_q:
        hits = set(
            run_query(store, {**hit_q, "limit": 100000, "range": None}).get(
                {
                    "ramp": "ramp_id",
                    "ramps": "ramp_id",
                    "facility": "facility_id",
                    "facilities": "facility_id",
                    "section": "section_id",
                    "sections": "section_id",
                }[hit_q.get("target", "ramp")],
                pd.Series([]),
            )
        )
        tgt = {"ramps": "ramp", "facilities": "facility", "sections": "section"}.get(
            hit_q.get("target", "ramp"), hit_q.get("target", "ramp")
        )
        hit_a = len(A[tgt] & hits)
        hit_b = len(B[tgt] & hits)
        ratio("hit_ratio", "検索条件の該当率", hit_a, len(A[tgt]), hit_b, len(B[tgt]))
    len_a = float(sa["dist_standard_m"].sum()) / 1000.0
    len_b = float(sb["dist_standard_m"].sum()) / 1000.0
    for key, label, ka, kb, unit_km in (
        ("ramp_density", "ランプ密度（本/10km）", len(ra), len(rb), 10.0),
        ("facility_density", "施設密度（施設/100km）", len(fa), len(fb), 100.0),
    ):
        p = st.poisson_rate_test(ka, len_a, kb, len_b)
        summary.append(
            {
                "key": key,
                "label": label,
                "kind": "density",
                "a": ka / len_a * unit_km if len_a > 0 else None,
                "b": kb / len_b * unit_km if len_b > 0 else None,
                "a_k": ka,
                "b_k": kb,
                "a_len_km": len_a,
                "b_len_km": len_b,
                "p": p,
                "test": "poisson_rate",
                "q": None,
                "reference_only": min(ka, kb) < min_n,
            }
        )
    # ---- カテゴリ
    categorical = []
    for key, label, da_, db_ in (("shape_class", "形状分類の構成", fa, fb), ("flow", "flow の構成", ra, rb)):
        ca = dict(Counter(da_[key].dropna().astype(str)))
        cb = dict(Counter(db_[key].dropna().astype(str)))
        p, test = st.categorical_test(ca, cb, int(sc["fisher_mc_iterations"]), seed)
        categorical.append({"key": key, "label": label, "a": ca, "b": cb, "test": test, "p": p, "q": None})
    # ---- BH 補正（同時に表示する全指標）
    allp = [m["p"] for m in metrics_out] + [s["p"] for s in summary] + [c["p"] for c in categorical]
    qs = st.bh_qvalues(allp)
    i = 0
    for coll in (metrics_out, summary, categorical):
        for item in coll:
            item["q"] = qs[i]
            i += 1
    insights = make_insights(metrics_out, summary, cfg, hit_a, hit_b, req)

    def counts(sets: dict[str, set[str]]) -> dict[str, int]:
        return {k: len(v) for k, v in sets.items()}

    return {
        "a": {
            "label": range_label(req["range_a"]),
            "counts": counts(A),
            "facility_ids": sorted(A["facility"]),
            "section_ids": sorted(A["section"]),
        },
        "b": {
            "label": range_label(req["range_b"]),
            "counts": counts(B),
            "facility_ids": sorted(B["facility"]),
            "section_ids": sorted(B["section"]),
        },
        "overlap": {
            "facilities": len(overlap["facility"]),
            "ramps": len(overlap["ramp"]),
            "sections": len(overlap["section"]),
            "mode": mode,
            "facility_ids": sorted(overlap["facility"]),
        },
        "summary": summary,
        "metrics": metrics_out,
        "categorical": categorical,
        "insights_ja": insights,
        "scatter": {"a": _scatter(ra), "b": _scatter(rb)},
        "notes_ja": _notes(overlap, mode, min_n),
        "provenance": {
            "run": _json_run(store),
            "seed": seed,
            "bootstrap": n_boot,
            "attribution": attribution_text(),
        },
        "run_id": (store.current_run() or {}).get("run_id"),
    }


def _scatter(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for r in df.itertuples():
        if r.min_radius_m is None or pd.isna(r.min_radius_m):
            continue
        out.append(
            {
                "ramp_id": r.ramp_id,
                "facility_id": r.facility_id,
                "x": float(r.min_radius_m),
                "y": float(r.grade_up_max_w100)
                if r.grade_up_max_w100 is not None and not pd.isna(r.grade_up_max_w100)
                else None,
            }
        )
    return out


def _json_run(store: Store) -> dict[str, Any]:
    r = store.current_run() or {}
    return {"run_id": r.get("run_id"), "config_hash": r.get("config_hash")}


def _notes(overlap: dict[str, set[str]], mode: str, min_n: int) -> list[str]:
    notes = [
        "連続量は両側 Mann–Whitney U 検定（n≤20 かつ同順位なしは正確検定、それ以外は連続性補正付き正規近似）、"
        "効果量は Cliff's δ（主）と Cohen's d（参考）、中央値差はブートストラップ 95%CI。p 値は BH 法で補正した q 値を併記。",
        f"n<{min_n} の指標は参考値として扱い、主な差異の自動抽出から除外しています。",
    ]
    if any(overlap.values()) and mode == "keep_warn":
        notes.append(
            f"範囲 A と B に重複があります（施設 {len(overlap['facility'])}、ランプ {len(overlap['ramp'])}）。"
            "重複を含めたまま比較しているため、標本の独立性の仮定が崩れています。"
        )
    return notes


def _fmt(v: float | None, digits: int) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def make_insights(
    metrics: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    cfg: Config,
    hit_a: int | None,
    hit_b: int | None,
    req: dict[str, Any],
) -> list[str]:
    sc = cfg.stats
    thr = float(sc["insight_min_abs_delta"])
    qthr = float(sc["fdr_q"])
    cand = [
        m
        for m in metrics
        if not m["reference_only"]
        and m["cliffs_delta"] is not None
        and (abs(m["cliffs_delta"]) >= thr or (m["q"] is not None and m["q"] < qthr))
    ]
    cand.sort(key=lambda m: -abs(m["cliffs_delta"]))
    out = []
    for m in cand[: int(sc["insight_max"])]:
        d = m["digits"]
        ci = m["median_diff_ci"] or [None, None]
        unit = m["unit"] if m["unit"] not in ("", "本") else ""
        out.append(
            f"{m['label']}：中央値 A {_fmt(m['a']['median'], d)}{unit} / B {_fmt(m['b']['median'], d)}{unit}"
            f"（B−A {_fmt(m['median_diff'], d)}{unit}、95%CI [{_fmt(ci[0], d)}, {_fmt(ci[1], d)}]、"
            f"δ={m['cliffs_delta']:.2f}、q={_fmt(m['q'], 2)}）"
        )
    if not out:
        out.append("効果量が中程度以上（|δ|≥0.33）または q<0.10 の指標はありませんでした。")
    hit = next((s for s in summary if s["key"] == "hit_ratio"), None)
    if hit is not None:
        pa = "—" if hit["a"] is None else f"{hit['a'] * 100:.1f}%"
        pb = "—" if hit["b"] is None else f"{hit['b'] * 100:.1f}%"
        out.append(
            f"現在の検索条件の該当率：A {pa}（{hit['a_k']}/{hit['a_n']}）/ B {pb}（{hit['b_k']}/{hit['b_n']}）"
            f"、q={_fmt(hit['q'], 2)}"
        )
    else:
        out.append("現在の検索条件の該当率：検索条件が未設定のため算出していません。")
    return out


def stats_table(res: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for m in res["metrics"]:
        rows.append(
            {
                "kind": "metric",
                "key": m["key"],
                "label": m["label"],
                "unit": m["unit"],
                "n_a": m["a"]["n"],
                "n_b": m["b"]["n"],
                "mean_a": m["a"]["mean"],
                "mean_b": m["b"]["mean"],
                "sd_a": m["a"]["sd"],
                "sd_b": m["b"]["sd"],
                "median_a": m["a"]["median"],
                "median_b": m["b"]["median"],
                "q1_a": m["a"]["q1"],
                "q3_a": m["a"]["q3"],
                "q1_b": m["b"]["q1"],
                "q3_b": m["b"]["q3"],
                "p10_a": m["a"]["p10"],
                "p90_a": m["a"]["p90"],
                "p10_b": m["b"]["p10"],
                "p90_b": m["b"]["p90"],
                "min_a": m["a"]["min"],
                "max_a": m["a"]["max"],
                "min_b": m["b"]["min"],
                "max_b": m["b"]["max"],
                "median_diff": m["median_diff"],
                "ci_low": (m["median_diff_ci"] or [None, None])[0],
                "ci_high": (m["median_diff_ci"] or [None, None])[1],
                "cliffs_delta": m["cliffs_delta"],
                "cohens_d": m["cohens_d"],
                "test": m["test"],
                "p": m["p"],
                "q": m["q"],
                "reference_only": m["reference_only"],
            }
        )
    for s in res["summary"]:
        rows.append(
            {
                "kind": s["kind"],
                "key": s["key"],
                "label": s["label"],
                "mean_a": s["a"],
                "mean_b": s["b"],
                "ci_low": None,
                "ci_high": None,
                "test": s.get("test"),
                "p": s["p"],
                "q": s["q"],
                "reference_only": s.get("reference_only"),
            }
        )
    for c in res["categorical"]:
        rows.append(
            {
                "kind": "categorical",
                "key": c["key"],
                "label": c["label"],
                "test": c["test"],
                "p": c["p"],
                "q": c["q"],
            }
        )
    df = pd.DataFrame(rows)
    df["range_a"] = res["a"]["label"]
    df["range_b"] = res["b"]["label"]
    df["run_id"] = res.get("run_id")
    return df
