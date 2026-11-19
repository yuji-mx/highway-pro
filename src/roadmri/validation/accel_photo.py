"""航空写真による加速車線の端点の補正候補（検証専用。ADR-0020）。

本番の自動値（地図データによる暫定値）には採用しない。`roadmri accel-photo` で検証結果として出力し、
`roadmri accel-eval` で写真の判読と現行値と同じ地点で比べる。`roadmri serve` は画像を取得しない。

手順（data/validation/accel_regional/REPORT.md の地域別補正・両側追跡を、このリポジトリの部品で組み直したもの）：

1. 道筋（ランプ→合流ノード→下流の本線。`lane_path`）に沿って航空写真を「まっすぐに伸ばした帯」にする。
   行＝測点（m）、列＝進入側を正とする横方向の位置（m）。
2. 進入側は、合流ノードの手前でランプが本線のどちら側にあるか（幾何）で決める。決まらないときだけ国の既定。
3. 白線を横方向の top-hat（周囲の中央値より明るい細い線）で取り出す。
4. 日本：始点は現行値、終点は下流から本線の外縁をたどり、外側に車線幅が現れる最も下流の点。
   米国：始点は本線との境の線が実線から破線に変わる点、終点は境の線と外縁の間の幅が消える点。
5. 次の場合は候補を出さずに保留：端点の付近が影・画像なし、隣のランプ・別の合流線が外側に並ぶ、
   同程度の候補が離れて複数ある、画像端で延長しても見つからない（延長は元の解像度で取り直す）。
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter

FloatArray = NDArray[np.float64]
# (経度, 緯度) の配列 → (明るさ 0〜255, 画像がある画素か)
Sampler = Callable[[FloatArray, FloatArray], tuple[FloatArray, NDArray[np.bool_]]]


# ---- 進入側 ------------------------------------------------------------------------------------------------


def entry_side(
    path_xy: FloatArray, merge_s: float, country: str | None, cfg: dict[str, Any]
) -> tuple[str, str]:
    """進入側（"left"・"right"）と決め方（"geometry"・"country"）。

    合流ノードでの本線の進行方向に対して、合流ノードより上流のランプの点が左右どちらにあるか。
    ランプが本線とほぼ重なって左右が決まらないときだけ、国の既定（日本は左、米国は右）を使う。
    """
    s = _stations(path_xy)
    m = _at(path_xy, s, merge_s)
    ahead = _at(path_xy, s, min(merge_s + 30.0, float(s[-1])))
    t = ahead - m
    votes = []
    for back in (40.0, 80.0, 120.0):
        if merge_s - back < 0:
            continue
        p = _at(path_xy, s, merge_s - back) - m
        # 本線の延長線（上流側）からの横ずれ。上流のランプ点は本線の手前にあるので -t 方向と比べる
        cross = float(t[0] * p[1] - t[1] * p[0])
        if abs(cross) / max(float(np.hypot(*t)), 1e-9) >= 1.0:
            votes.append(1 if cross > 0 else -1)
    if votes and abs(sum(votes)) == len(votes):
        return ("left" if votes[0] > 0 else "right"), "geometry"
    fb = cfg.get("fallback_country_side", {})
    return str(fb.get(country or "", fb.get("default", "right"))), "country"


# ---- 帯（まっすぐに伸ばした航空写真） -------------------------------------------------------------------------


def _stations(xy: FloatArray) -> FloatArray:
    return np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])


def _at(xy: FloatArray, s: FloatArray, v: float) -> FloatArray:
    return np.array([np.interp(v, s, xy[:, 0]), np.interp(v, s, xy[:, 1])])


@dataclass
class Strip:
    """行＝測点、列＝進入側を正とする横方向の位置。gray は明るさ、valid は画像がある画素。"""

    s: FloatArray  # 測点（m、道筋の始まりから）
    u: FloatArray  # 横方向（m、進入側が正）
    gray: FloatArray
    valid: NDArray[np.bool_]


def make_strip(
    path_xy: FloatArray,
    to_lonlat: Callable[[FloatArray, FloatArray], tuple[FloatArray, FloatArray]],
    sampler: Sampler,
    side: str,
    s_from: float,
    s_to: float,
    cfg: dict[str, Any],
) -> Strip:
    s_all = _stations(path_xy)
    step, du, hw = float(cfg["step_m"]), float(cfg["lateral_px_m"]), float(cfg["half_width_m"])
    s = np.arange(max(0.0, s_from), min(float(s_all[-1]), s_to) + 1e-9, step)
    u = np.arange(-hw, hw + 1e-9, du)
    x = np.interp(s, s_all, path_xy[:, 0])
    y = np.interp(s, s_all, path_xy[:, 1])
    # 進行方向（前後 5 m の差分で滑らかに）と左の法線
    ds = 5.0
    tx = np.interp(s + ds, s_all, path_xy[:, 0]) - np.interp(s - ds, s_all, path_xy[:, 0])
    ty = np.interp(s + ds, s_all, path_xy[:, 1]) - np.interp(s - ds, s_all, path_xy[:, 1])
    n = np.hypot(tx, ty)
    n[n == 0] = 1.0
    nx, ny = -ty / n, tx / n
    sign = 1.0 if side == "left" else -1.0
    px = x[:, None] + sign * u[None, :] * nx[:, None]
    py = y[:, None] + sign * u[None, :] * ny[:, None]
    lon, lat = to_lonlat(px.ravel(), py.ravel())
    g, v = sampler(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return Strip(s, u, g.reshape(px.shape), v.reshape(px.shape))


# ---- 白線 -----------------------------------------------------------------------------------------------------


def marks(strip: Strip, cfg: dict[str, Any]) -> NDArray[np.bool_]:
    """白線らしい画素：横方向の周囲（background_m）の中央値より contrast_min 以上明るく、幅が線の範囲。"""
    mc = cfg["mark"]
    du = float(strip.u[1] - strip.u[0]) if len(strip.u) > 1 else 1.0
    k = max(3, round(float(mc["background_m"]) / du) | 1)
    bg = median_filter(strip.gray, size=(1, k), mode="nearest")
    m: NDArray[np.bool_] = np.asarray(
        (strip.gray - bg >= float(mc["contrast_min"])) & strip.valid, dtype=bool
    )
    # 幅が広すぎる明るい塊（路面標示の文字・ゼブラの帯の横断方向・明るい舗装）は線として扱わない
    max_px = max(1, round(float(mc["max_width_m"]) / du))
    out = m.copy()
    for i in range(m.shape[0]):
        row = m[i]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1), strict=True):
            if b - a > max_px:
                out[i, a:b] = False
    return out


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist(), strict=True))


# ---- 結果 -----------------------------------------------------------------------------------------------------


@dataclass
class Detection:
    s: float | None  # 測点（m）。None は見つからない・保留
    held: str | None = None  # 保留の理由（MANUAL_PENDING_REASONS と同じコード＋ ambiguous・lost）
    detail: dict[str, Any] = field(default_factory=dict)


def _visibility(
    strip: Strip, i0: int, i1: int, u0: float, u1: float, cfg: dict[str, Any]
) -> tuple[float, str]:
    """範囲内の有効な画素の割合と、足りないときの理由（画像なし・影）。"""
    rows = slice(max(0, i0), min(len(strip.s), i1))
    cols = (strip.u >= u0) & (strip.u <= u1)
    v = strip.valid[rows][:, cols]
    g = strip.gray[rows][:, cols]
    if v.size == 0:
        return 0.0, "out_of_image"
    has = float(v.mean())
    lit = v & (g > float(cfg["hold"]["shadow_max"]))
    ratio = float(lit.mean())
    return ratio, ("out_of_image" if has < float(cfg["hold"]["visible_min"]) else "shadow")


def outer_edge(
    strip: Strip, mk: NDArray[np.bool_], cfg: dict[str, Any], main_half_m: float | None = None
) -> tuple[FloatArray, float | None]:
    """本線の外縁（最下流の baseline_m で最も途切れない線）と、そこから上流へたどった外縁の位置（なければ nan）。

    外縁は 1 測点ごとに tolerance_m 以内の白線へ移り、候補が複数あれば最も外側を選ぶ（テーパーで外へ開く線を追う）。
    路肩・防護柵などの離れた明るい点には飛び移らない。
    """
    e = cfg["edge"]
    step = float(strip.s[1] - strip.s[0]) if len(strip.s) > 1 else 1.0
    nb = max(3, round(float(e["baseline_m"]) / step))
    tol = float(e["tolerance_m"])
    lo, hi = (
        (0.0, float(e["search_m"])) if main_half_m is None else (main_half_m - 2 * tol, main_half_m + 2 * tol)
    )
    band = (strip.u >= lo) & (strip.u <= hi)
    outer = np.full(len(strip.s), np.nan)
    if not band.any():
        return outer, None
    cov = mk[-nb:][:, band].mean(axis=0)
    # 実線の外縁：途切れない（被覆率が高い）列。同程度なら外側
    best = float(cov.max())
    if best < float(cfg["dash"]["solid_min"]) * 0.6:
        return outer, None  # 最下流でも外縁が途切れている（テーパーの途中で切れている・見えない）
    cols = np.flatnonzero(band)[cov >= best - 0.1]
    base = float(strip.u[cols].max())
    jump = max(tol, float(e["max_jump_m"]) * step) / 2.0
    lim = base + float(e["search_m"])
    prev = base
    for i in range(len(strip.s) - 1, -1, -1):
        sel = mk[i] & (strip.u >= max(base - tol, prev - jump)) & (strip.u <= min(lim, prev + jump))
        idx = np.flatnonzero(sel)
        if idx.size:
            outer[i] = float(strip.u[idx].max())
            prev = outer[i]
    return outer, base


def end_by_edge(
    strip: Strip,
    mk: NDArray[np.bool_],
    cfg: dict[str, Any],
    s_min: float,
    need_inner: bool,
    main_half_m: float | None = None,
) -> Detection:
    """下流から外縁をたどり、外側の車線幅（外縁−本線の外縁）が min_width_m 以上で run_m 続く最も下流の点。

    need_inner：米国の方式。本線との境の線（本線の外縁の位置）が、終点の上流側で破線として見えていること。
    """
    e, h = cfg["edge"], cfg["hold"]
    step = float(strip.s[1] - strip.s[0]) if len(strip.s) > 1 else 1.0
    outer, base = outer_edge(strip, mk, cfg, main_half_m)
    if base is None:
        nb = round(float(e["baseline_m"]) / step)
        ratio, why = _visibility(strip, len(strip.s) - nb, len(strip.s), -2.0, 8.0, cfg)
        if ratio < float(h["visible_min"]):
            return Detection(None, why, {"reason": "本線の外縁が見えない"})
        # 下流の端でも外縁が定まらない：画像端として下流へ延ばす
        return Detection(None, "out_of_image", {"reason": "下流の端で本線の外縁が定まらない", "extend": True})
    if main_half_m is not None and base > main_half_m + float(e["min_width_m"]) * 1.5:
        # 下流の端でも本線の外側に車線が残っている：終点は画像の先。車線追加とは決めずに延ばす
        return Detection(
            None, "out_of_image", {"reason": "下流の端まで車線が続く", "base_u": base, "extend": True}
        )
    width = outer - base
    fin = np.isfinite(width)
    has = fin & (np.nan_to_num(width, nan=-1.0) >= float(e["min_width_m"]))
    run = max(2, round(float(e["run_m"]) / step))

    def lane_window(i: int) -> bool:
        # 外縁を見失った測点（nan）は数えず、見えている測点の大半で車線幅があれば「車線あり」
        w = slice(i - run + 1, i + 1)
        n = int(fin[w].sum())
        return n >= 0.3 * run and int(has[w].sum()) >= 0.8 * n

    cands: list[int] = []
    i = len(has) - 1
    while i >= run:
        if has[i] and lane_window(i):
            cands.append(i)
            # 同じ車線の区間を飛ばす（見失った測点も同じ区間として扱う）
            j = i
            while j > 0 and (has[j - 1] or not fin[j - 1]):
                j -= 1
            i = j - 1
        else:
            i -= 1
    cands = [c for c in cands if strip.s[c] >= s_min]
    if not cands:
        return Detection(
            None, "lost", {"reason": "外側に車線幅が現れない（車線が続く・消失点が範囲外）", "base_u": base}
        )
    best = cands[0]
    s_end = float(strip.s[best])
    detail: dict[str, Any] = {"base_u": base, "candidates_s": [float(strip.s[c]) for c in cands]}
    # テーパーの終わり：幅が min_width_m を下回る手前の傾きで、幅が 0 になる点まで延ばす
    seg = slice(max(0, best - run), best + 1)
    ws, ss = width[seg], strip.s[seg]
    ok = np.isfinite(ws)
    if ok.sum() >= 3:
        slope = float(np.polyfit(ss[ok], ws[ok], 1)[0])
        if slope < 0:
            s_end = min(s_end + float(width[best]) / -slope, s_end + run * step)
    detail["taper_end_s"] = s_end
    # 画像端：下流の端に近すぎる（下流側の車線のない区間が確認できていない）
    if float(strip.s[-1]) - s_end < float(h["edge_margin_m"]):
        return Detection(None, "out_of_image", {**detail, "extend": True})
    # 見通し：端点の前後
    win = round(float(h["window_m"]) / step)
    ratio, why = _visibility(strip, best - win, best + win, base - 1.0, base + float(e["search_m"]), cfg)
    detail["visible_ratio"] = ratio
    if ratio < float(h["visible_min"]):
        return Detection(None, why, detail)
    # 隣のランプ・別の合流線：外縁のさらに外側に平行な線
    gap = float(h["competing_gap_m"])
    rows = slice(max(0, best - win), best + 1)
    outside = mk[rows] & (strip.u[None, :] > (np.nan_to_num(outer[rows], nan=base)[:, None] + gap))
    outside &= strip.u[None, :] <= base + float(e["search_m"]) + 4.0
    if outside.any(axis=1).mean() > 0.5:
        return Detection(None, "adjacent_ramp", detail)
    # 同程度の候補が離れて複数（別の出口・合流の線を拾う可能性）
    if len(cands) > 1 and float(strip.s[cands[0]] - strip.s[cands[1]]) >= float(h["ambiguity_m"]):
        seg0 = np.nansum(has[: cands[0] + 1][-run * 3 :])
        seg1 = np.nansum(has[: cands[1] + 1][-run * 3 :])
        if seg1 >= 0.8 * seg0 and _gap_has_no_lane(has, cands[1], cands[0], run):
            return Detection(None, "ambiguous", detail)
    # 本線との境の線：加速車線なら破線。実線で隔てられていれば別のランプ・別の合流線（集散路など）
    cols = np.abs(strip.u - base) <= float(e["tolerance_m"])
    up = slice(max(0, best - 3 * run), best)
    cov = float(mk[up][:, cols].any(axis=1).mean()) if cols.any() and best > 0 else 0.0
    detail["inner_coverage"] = cov
    if cov >= float(cfg["dash"]["solid_min"]):
        return Detection(
            None, "adjacent_ramp", {**detail, "reason": "本線との境が実線（別のランプ・合流線）"}
        )
    if need_inner and cov < float(cfg["dash"]["dash_min"]):
        return Detection(None, "lost", {**detail, "reason": "本線との境の線が見えない"})
    return Detection(s_end, None, detail)


def _gap_has_no_lane(has: NDArray[np.bool_], a: int, b: int, run: int) -> bool:
    """候補 a（上流）と b（下流）の間に、車線のない区間が run 以上あるか（＝別々の車線）。"""
    seg = ~has[a + 1 : b]
    best = cur = 0
    for v in seg:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best >= run


def start_by_dash(
    strip: Strip, mk: NDArray[np.bool_], base_u: float, cfg: dict[str, Any], s_max: float
) -> Detection:
    """米国の始点：本線の外縁の位置の線が、実線（被覆率 solid_min 以上）から破線（dash_min〜dash_max）に変わり、
    run_m 続く最初の点。"""
    d, e, h = cfg["dash"], cfg["edge"], cfg["hold"]
    step = float(strip.s[1] - strip.s[0]) if len(strip.s) > 1 else 1.0
    cols = np.abs(strip.u - base_u) <= float(e["tolerance_m"])
    line = mk[:, cols].any(axis=1).astype(float)
    k = max(3, round(float(d["window_m"]) / step))
    cov = np.convolve(line, np.ones(k) / k, mode="same")
    solid = cov >= float(d["solid_min"])
    dash = (cov >= float(d["dash_min"])) & (cov <= float(d["dash_max"]))
    run = max(2, round(float(d["run_m"]) / step))
    found: list[int] = []
    seen_solid = False
    for i in range(len(cov)):
        if strip.s[i] > s_max:
            break
        if solid[i]:
            seen_solid = True
            continue
        if seen_solid and dash[i : i + run].mean() >= 0.8 and i + run <= len(cov):
            found.append(i + k // 2)  # 窓の中心のずれを戻す（実線が終わる点）
            seen_solid = False
    detail: dict[str, Any] = {"transitions_s": [float(strip.s[min(i, len(strip.s) - 1)]) for i in found]}
    if not found:
        ratio, why = _visibility(strip, 0, len(strip.s), base_u - 1.0, base_u + 1.0, cfg)
        return Detection(
            None,
            why if ratio < float(h["visible_min"]) else "lost",
            {**detail, "reason": "実線→破線の変化が見えない"},
        )
    i0 = min(found[0], len(strip.s) - 1)
    win = round(float(h["window_m"]) / step)
    ratio, why = _visibility(strip, i0 - win, i0 + win, base_u - 1.0, base_u + 1.0, cfg)
    detail["visible_ratio"] = ratio
    if ratio < float(h["visible_min"]):
        return Detection(None, why, detail)
    if len(found) > 1 and float(strip.s[min(found[1], len(strip.s) - 1)] - strip.s[i0]) >= float(
        h["ambiguity_m"]
    ):
        return Detection(None, "ambiguous", detail)
    return Detection(float(strip.s[i0]), None, detail)


# ---- 1 ランプの補正候補 ---------------------------------------------------------------------------------------


@dataclass
class Candidate:
    ramp_id: str
    country: str | None
    side: str
    side_method: str
    baseline: list[float] | None  # [始点, 終点]（測点、m）
    start: Detection
    end: Detection
    method: str  # "jp_edge"・"us_dash_width"

    @property
    def adjusted(self) -> list[float] | None:
        """補正候補の [始点, 終点]。どちらかが保留なら None（候補を出さない）。日本の始点は現行値。"""
        if self.start.s is None or self.end.s is None or self.end.s <= self.start.s:
            return None
        return [self.start.s, self.end.s]

    @property
    def held(self) -> dict[str, str]:
        return {k: d.held for k, d in (("start", self.start), ("end", self.end)) if d.held}

    def as_dict(self) -> dict[str, Any]:
        a = self.adjusted
        return {
            "ramp_id": self.ramp_id,
            "country": self.country,
            "method": self.method,
            "entry_side": self.side,
            "entry_side_method": self.side_method,
            "baseline": self.baseline,
            "adjusted": a,
            "length_m": None if a is None else a[1] - a[0],
            "held": self.held or None,
            "evidence": {
                "start": {"s": self.start.s, "held": self.start.held, **self.start.detail},
                "end": {"s": self.end.s, "held": self.end.held, **self.end.detail},
            },
            "adopted": False,  # 本番の自動値には採用しない
        }


def detect(
    path_xy: FloatArray,
    to_lonlat: Callable[[FloatArray, FloatArray], tuple[FloatArray, FloatArray]],
    sampler: Sampler,
    ramp_id: str,
    country: str | None,
    merge_s: float,
    baseline: list[float] | None,
    cfg: dict[str, Any],
    main_half_m: float | None = None,
    downstream_m: float | None = None,
) -> Candidate:
    """1 本の道筋で補正候補を求める。日本は終点だけ、米国は始点と終点を写真から求める。

    main_half_m：下流の本線の中心線から外縁までの距離（車線数×車線幅÷2）。分かれば、下流の端まで車線が
    続いているか（画像端）を判定できる。"""
    side, how = entry_side(path_xy, merge_s, country, cfg)
    s_end = min(float(_stations(path_xy)[-1]), merge_s + float(downstream_m or cfg["downstream_m"]))
    strip = make_strip(path_xy, to_lonlat, sampler, side, merge_s - float(cfg["approach_m"]), s_end, cfg)
    mk = marks(strip, cfg)
    s_min = baseline[0] if baseline else merge_s
    if country == "JP":
        start = Detection(
            baseline[0] if baseline else None, None if baseline else "lost", {"method": "current"}
        )
        end = end_by_edge(strip, mk, cfg, s_min, need_inner=False, main_half_m=main_half_m)
        return Candidate(ramp_id, country, side, how, baseline, start, end, "jp_edge")
    end = end_by_edge(
        strip, mk, cfg, merge_s - float(cfg["approach_m"]), need_inner=True, main_half_m=main_half_m
    )
    base_u = end.detail.get("base_u")
    if base_u is None:
        start = Detection(None, end.held or "lost", {"reason": "本線の外縁が見えない"})
    else:
        start = start_by_dash(strip, mk, float(base_u), cfg, end.s if end.s is not None else s_end)
    return Candidate(ramp_id, country, side, how, baseline, start, end, "us_dash_width")


# ---- 航空写真のタイル（ingest/http.py 経由。検証用の CLI からだけ使う） -------------------------------------------


class TileMosaic:
    """XYZ タイルを取得・保存し、経度・緯度で明るさを標本化する。取得は HttpClient（レート制限・キャッシュ）。"""

    def __init__(self, http: Any, url: str, zoom: int, cache_dir: Path, source: str):
        self.http, self.url, self.zoom, self.cache, self.source = http, url, zoom, cache_dir, source
        self.tiles: dict[tuple[int, int], FloatArray | None] = {}
        self.fetched = 0

    def _tile(self, x: int, y: int) -> FloatArray | None:
        if (x, y) in self.tiles:
            return self.tiles[(x, y)]
        from PIL import Image

        from roadmri.ingest.http import NotFoundError

        p = self.cache / self.source / str(self.zoom) / str(x) / f"{y}.img"
        data: bytes | None = None
        if p.exists():
            data = p.read_bytes()
        elif not p.with_suffix(".missing").exists():
            try:
                data = self.http.get_bytes(self.url.format(z=self.zoom, x=x, y=y))
                self.fetched += 1
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            except NotFoundError:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.with_suffix(".missing").write_text("404")
        arr = None
        if data:
            arr = np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=float)
        self.tiles[(x, y)] = arr
        return arr

    def sample(self, lon: FloatArray, lat: FloatArray) -> tuple[FloatArray, NDArray[np.bool_]]:
        n = 2.0**self.zoom * 256.0
        gx = (lon + 180.0) / 360.0 * n
        lr = np.radians(lat)
        gy = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n
        tx, ty = (gx // 256).astype(int), (gy // 256).astype(int)
        out = np.zeros(lon.shape)
        ok = np.zeros(lon.shape, dtype=bool)
        for key in set(zip(tx.tolist(), ty.tolist(), strict=True)):
            t = self._tile(*key)
            if t is None:
                continue
            sel = (tx == key[0]) & (ty == key[1])
            px = np.clip((gx[sel] - key[0] * 256).astype(int), 0, t.shape[1] - 1)
            py = np.clip((gy[sel] - key[1] * 256).astype(int), 0, t.shape[0] - 1)
            out[sel] = t[py, px]
            ok[sel] = True
        return out, ok
