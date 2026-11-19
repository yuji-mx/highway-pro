"""加速車線長（ADR-0016）。

定義（利用者と合意）：
- 始点：合流車線と本線の隔て部分（ゼブラ・縁石など）が終わり、車線境界が破線になる点。
- 終点：加速車線が完全になくなる点（テーパーの終わり）。
- 対象：本線への合流（流入ランプ・JCT 連絡路・SA/PA からの合流。設定 accel_lane.flows）。減速車線は対象外。
- 除外：合流車線がそのまま本線の車線になる「車線追加」、次の出口まで続く「付加車線・織り込み」、
  そのほか定義に当てはめられないもの（理由を残す）。

OSM での描き方は 2 通りあり、両方を扱う。
- 並走型：ランプの way が本線の脇を並走し、加速車線の終わり（テーパーの終わり）で本線に接続する。
  → 終点は合流ノード。始点は、ランプが本線の「すぐ隣」（本線の半幅＋ランプの半幅＋許容差）まで近づいた点。
- 車線増減型：ランプが隔て部分の終わりで本線に接続し、本線の lanes が 1 つ増え、加速車線の終わりで元に戻る。
  → 始点は合流ノード（上と同じ幾何判定で、並走部があればそこから）、終点は本線の lanes が合流前の数に戻る点。
OSM は手描きなので数十 m の誤差がありうる。判定に使った方法と信頼度を残し、画面で確認・修正できるようにする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import substring

# 除外・保留の理由（コード → 説明）。画面・CSV に出す
REASONS: dict[str, str] = {
    # 対象外（定義に当てはまらない。距離は null）
    "lane_addition": "対象外・車線追加：合流車線がそのまま本線の車線になる（探索範囲内で車線が減らない）",
    "auxiliary_to_exit": "対象外・出口まで続く付加車線：車線が終わる前に出口へ分岐する",
    # 判定保留（端点を確定できない。距離は null）
    "no_mainline": "判定保留：合流先の本線（上流・下流）が見つからない",
    "lanes_inconsistent": "判定保留：本線の車線数が合流点で減っている（情報の矛盾）",
    "mainline_ends": "判定保留：車線が戻る前に本線のデータが途切れる（範囲の端など）",
    "lanes_unknown_downstream": "判定保留：下流の本線の車線数が不明で、車線が終わる点を確定できない",
    "another_merge": "判定保留：車線が終わる前に別の合流があり、終点を確定できない",
    "no_parallel_section": "判定保留：隔て部分の終わりが特定できない（合流部が本線と並走しておらず、車線も増えていない）",
}
# 定義上の対象外（ADR-0016）。これ以外で距離を出せないものは「判定保留」
EXCLUDED_REASONS = frozenset({"lane_addition", "auxiliary_to_exit"})
# 判定保留の理由ごとに、確定できなかった端点
PENDING_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "no_mainline": ("start", "end"),
    "lanes_inconsistent": ("end",),
    "mainline_ends": ("end",),
    "lanes_unknown_downstream": ("end",),
    "another_merge": ("end",),
    "no_parallel_section": ("start",),
}
# 端点の算出方法（ADR-0020）
ENDPOINT_METHODS: dict[str, str] = {
    "osm_parallel": "地図データ：ランプが本線のすぐ隣に寄った点（並走部の始まり）",
    "osm_merge_node": "地図データ：ランプが本線に接続する点",
    "osm_lane_drop": "地図データ：本線の車線数が合流前の数に戻る点",
    "manual": "手動（航空写真などで確認）",
}


def status_of(reason: str | None) -> str:
    """理由コードから状態（measured / excluded / pending）。以前の DB では保留の理由も excluded だった。"""
    if reason is None:
        return "measured"
    return "excluded" if reason in EXCLUDED_REASONS else "pending"


@dataclass
class Endpoint:
    """始点・終点ごとの根拠。method は ENDPOINT_METHODS、confidence は high / medium / low、pending は保留の理由。"""

    method: str | None = None
    confidence: str | None = None
    pending: str | None = None


@dataclass
class AccelLane:
    status: str  # measured / excluded / pending
    reason: str | None = None
    length_m: float | None = None
    method: str | None = None  # geometry / lanes / geometry+lanes
    confidence: str | None = None  # high / medium / low（全体）
    start_xy: tuple[float, float] | None = None
    end_xy: tuple[float, float] | None = None
    parallel_m: float | None = None  # 始点から合流ノードまで（ランプ上）
    added_lane_m: float | None = None  # 合流ノードから終点まで（本線上）
    notes: list[str] = field(default_factory=list)
    start: Endpoint = field(default_factory=Endpoint)
    end: Endpoint = field(default_factory=Endpoint)

    def metrics(self) -> dict[str, Any]:
        return {
            "accel_lane_len_m": self.length_m if self.status == "measured" else None,
            "accel_lane_status": self.status,
            "accel_lane_reason": self.reason,
            "accel_lane_method": self.method,
            "accel_lane_confidence": self.confidence,
            "accel_parallel_m": self.parallel_m,
            "accel_added_lane_m": self.added_lane_m,
            "accel_start_method": self.start.method,
            "accel_start_confidence": self.start.confidence,
            "accel_start_pending": self.start.pending,
            "accel_end_method": self.end.method,
            "accel_end_confidence": self.end.confidence,
            "accel_end_pending": self.end.pending,
        }


def _unmeasured(reason: str, **kw: Any) -> AccelLane:
    """対象外または判定保留。保留なら、確定できなかった端点に理由を付ける。"""
    status = status_of(reason)
    r = AccelLane(status, reason, **kw)
    if status == "pending":
        for which in PENDING_ENDPOINTS.get(reason, ("start", "end")):
            getattr(r, which).pending = reason
    return r


def lanes_of(d: dict[str, Any]) -> int | None:
    try:
        return int(str(d["tags"].get("lanes")).split(";")[0])
    except (TypeError, ValueError):
        return None


def _main_edges(
    g: Any, node: int, route: str | None, upstream: bool
) -> list[tuple[int, int, dict[str, Any]]]:
    it = g.in_edges(node, data=True) if upstream else g.out_edges(node, data=True)
    es = [(u, v, d) for u, v, d in it if d["cls"] == "mainline"]
    same = [e for e in es if route is None or e[2].get("route_ref") == route]
    return same or es


def _walk(
    g: Any, start: int, route: str | None, upstream: bool, limit: float
) -> list[tuple[int, int, dict[str, Any]]]:
    """合流ノードから本線を上流または下流へ limit m までたどった辺（近い順）。"""
    out: list[tuple[int, int, dict[str, Any]]] = []
    node, dist, seen = start, 0.0, {start}
    while dist < limit:
        es = _main_edges(g, node, route, upstream)
        if not es:
            break
        u, v, d = es[0]
        out.append((u, v, d))
        dist += float(d["length_m"])
        node = u if upstream else v
        if node in seen:
            break
        seen.add(node)
    return out


def _has_link(g: Any, node: int, out: bool) -> bool:
    it = g.out_edges(node, data=True) if out else g.in_edges(node, data=True)
    return any(d["cls"] == "link" for _, _, d in it)


def _parallel_length(
    ramp_xy: np.ndarray, upstream: LineString, thr: float, step: float, limit: float
) -> float:
    """合流ノードからランプを遡り、本線からの距離が thr 以下で続く長さ（隔て部分がない区間）。"""
    rl = LineString(ramp_xy)
    total = float(rl.length)
    prev_d, prev_off = 0.0, 0.0
    d = step
    while d <= min(total, limit) + 1e-9:
        off = float(upstream.distance(rl.interpolate(total - d)))
        if off > thr:
            # 直前の標本との間を線形補間して境界を求める
            return prev_d + (thr - prev_off) / (off - prev_off) * (d - prev_d) if off > prev_off else prev_d
        prev_d, prev_off = d, off
        d += step
    return min(prev_d, total)


def measure(
    g: Any,
    ramp_xy: np.ndarray,
    merge_node: int,
    ramp_lanes: int | None,
    cfg: dict[str, Any],
    lane_width_m: float,
) -> AccelLane:
    """合流ランプ 1 本の加速車線を測る。g は道路グラフ（networkx）、座標は投影座標（m）。"""
    search = float(cfg["max_search_m"])
    up = _main_edges(g, merge_node, None, upstream=True)
    down = _main_edges(g, merge_node, None, upstream=False)
    if not up or not down:
        return _unmeasured("no_mainline")
    route = down[0][2].get("route_ref")
    up_edges = _walk(g, merge_node, route, upstream=True, limit=search)
    down_edges = _walk(g, merge_node, route, upstream=False, limit=search)
    lb, la = lanes_of(up_edges[0][2]), lanes_of(down_edges[0][2])
    notes: list[str] = []

    # ---- 終点：車線増減型なら本線の車線数が元に戻る点、並走型なら合流ノード
    added = 0.0
    end_node = merge_node
    lanes_known = lb is not None and la is not None
    if lanes_known and la < lb:  # type: ignore[operator]
        return _unmeasured("lanes_inconsistent")
    if lanes_known and la > lb:  # type: ignore[operator]
        found = False
        merge_tag = False
        unknown = False  # 車線数の分からない辺があった（その辺で車線が終わっているかもしれない）
        for i, (u, _v, d) in enumerate(down_edges):
            if i > 0:  # 区間の途中のノード（u）で分岐・合流がないか
                if _has_link(g, u, out=True):
                    return _unmeasured("auxiliary_to_exit")
                if _has_link(g, u, out=False):
                    return _unmeasured("another_merge")
            n = lanes_of(d)
            if n is not None and n <= lb:  # type: ignore[operator]
                end_node, found = u, True
                break
            unknown = unknown or n is None
            merge_tag = merge_tag or "merge_to" in str(d["tags"].get("turn:lanes", ""))
            added += float(d["length_m"])
        if not found:
            # 終点を見失っただけでは車線追加としない：車線数が分かる区間で探索範囲の端まで続いたときだけ車線追加
            walked = sum(float(d["length_m"]) for _, _, d in down_edges)
            if unknown:
                return _unmeasured("lanes_unknown_downstream")
            return _unmeasured("lane_addition" if walked >= search else "mainline_ends")
        if _has_link(g, end_node, out=True):  # 車線が出口の分岐で終わる＝付加車線
            return _unmeasured("auxiliary_to_exit")
        if not merge_tag:
            notes.append("turn:lanes に合流（merge_to_*）の記載がない")
    elif not lanes_known:
        notes.append("本線の車線数が不明")

    # ---- 始点：ランプが本線のすぐ隣（本線の半幅＋ランプの半幅＋許容差）まで近づいた点
    main_lanes = lb if lb is not None else int(cfg["default_mainline_lanes"])
    thr = (main_lanes * lane_width_m + (ramp_lanes or 1) * lane_width_m) / 2 + float(
        cfg["adjacency_tolerance_m"]
    )
    up_xy = np.vstack([d["xy"] for _, _, d in reversed(up_edges)])
    upstream = LineString(up_xy)
    step = float(cfg["sample_step_m"])
    par = _parallel_length(ramp_xy, upstream, thr, step, search)
    sens = float(cfg["sensitivity_m"])
    # 信頼度：許容差を広げたときに始点がどれだけ遠くへ動くか（浅い角度で寄ってくると境界があいまい）
    spread = _parallel_length(ramp_xy, upstream, thr + sens, step, search) - par

    length = par + added
    if length < float(cfg["min_length_m"]):
        return _unmeasured("no_parallel_section", parallel_m=par, added_lane_m=added)
    rl = LineString(ramp_xy)
    sp = rl.interpolate(rl.length - par)
    end_xy = (float(g.nodes[end_node]["x"]), float(g.nodes[end_node]["y"]))
    method = "geometry+lanes" if added > 0 and par >= step else ("lanes" if added > 0 else "geometry")
    confidence = "high"
    if notes:
        confidence = "medium"
    if spread > float(cfg["low_confidence_spread_m"]):
        confidence = "low"
        notes.append(
            f"許容差を {sens:g} m 広げると始点が {spread:.0f} m 動く（ランプが浅い角度で本線に寄る）"
        )
    # 端点ごとの根拠（ADR-0020）
    start = Endpoint(
        "osm_parallel" if par >= step else "osm_merge_node",
        "low" if spread > float(cfg["low_confidence_spread_m"]) else ("high" if lanes_known else "medium"),
    )
    if added > 0:
        end = Endpoint("osm_lane_drop", "high" if merge_tag_seen(down_edges, end_node) else "medium")
    else:
        # ランプが本線に接続する点。描いた人がテーパーの途中に接続していると実際より手前になる（ADR-0017）
        end = Endpoint("osm_merge_node", "medium")
    return AccelLane(
        "measured",
        None,
        float(length),
        method,
        confidence,
        (float(sp.x), float(sp.y)),
        end_xy,
        float(par),
        float(added),
        notes,
        start,
        end,
    )


def merge_tag_seen(down_edges: list[tuple[int, int, dict[str, Any]]], end_node: int) -> bool:
    """合流ノードから終点までの辺に turn:lanes の合流（merge_to_*）の記載があるか。"""
    for u, _v, d in down_edges:
        if u == end_node:
            break
        if "merge_to" in str(d["tags"].get("turn:lanes", "")):
            return True
    return False


# ---- 手動での確認・修正（ADR-0017） ------------------------------------------------------


def lane_path(g: Any, ramp_xy: np.ndarray, merge_node: int, search_m: float) -> np.ndarray:
    """加速車線を測る道筋：ランプ全体と、合流ノードから下流の本線（search_m まで）をつないだ線（投影座標）。

    自動の終点は OSM でランプが本線に接続する点になりやすいが、実際の加速車線はその先まで続くことがあるので、
    手動の終点は本線上にも置けるようにする。
    """
    down = _main_edges(g, merge_node, None, upstream=False)
    route = down[0][2].get("route_ref") if down else None
    parts = [np.asarray(ramp_xy, dtype=float)]
    for _, _, d in _walk(g, merge_node, route, upstream=False, limit=search_m):
        parts.append(np.asarray(d["xy"], dtype=float)[1:])
    return np.vstack(parts)


@dataclass
class Span:
    length_m: float
    start_xy: tuple[float, float]  # 道筋上に寄せた点
    end_xy: tuple[float, float]
    start_offset_m: float  # 指定した点と道筋の距離
    end_offset_m: float
    path_xy: np.ndarray  # 始点〜終点の道筋


def measure_between(path_xy: np.ndarray, start_xy: tuple[float, float], end_xy: tuple[float, float]) -> Span:
    """道筋上の 2 点の間の長さ（指定した点は道筋に寄せる）。終点が始点より手前なら ValueError。"""
    line = LineString(path_xy)
    a, b = Point(start_xy), Point(end_xy)
    sa, sb = float(line.project(a)), float(line.project(b))
    if sb <= sa:
        raise ValueError("終点が始点より手前（上流）にあります。合流の進行方向に沿って指定してください")
    pa, pb = line.interpolate(sa), line.interpolate(sb)
    sub = substring(line, sa, sb)
    return Span(
        sb - sa,
        (float(pa.x), float(pa.y)),
        (float(pb.x), float(pb.y)),
        float(a.distance(pa)),
        float(b.distance(pb)),
        np.asarray(sub.coords, dtype=float),
    )
