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
from shapely.geometry import LineString

# 除外理由（コード → 説明）。画面・CSV に出す
REASONS: dict[str, str] = {
    "no_mainline": "合流先の本線（上流・下流）が見つからない",
    "lanes_inconsistent": "本線の車線数が合流点で減っている（情報の矛盾）",
    "lane_addition": "車線追加：合流車線がそのまま本線の車線になる（探索範囲内で車線が減らない）",
    "mainline_ends": "車線が戻る前に本線のデータが途切れる（範囲の端など。判定できない）",
    "auxiliary_to_exit": "付加車線・織り込み：車線が終わる前に出口へ分岐する",
    "another_merge": "車線が終わる前に別の合流がある",
    "no_parallel_section": "隔て部分の終わりが特定できない（合流部が本線と並走しておらず、車線も増えていない）",
}


@dataclass
class AccelLane:
    status: str  # measured / excluded
    reason: str | None = None
    length_m: float | None = None
    method: str | None = None  # geometry / lanes / geometry+lanes
    confidence: str | None = None  # high / medium / low
    start_xy: tuple[float, float] | None = None
    end_xy: tuple[float, float] | None = None
    parallel_m: float | None = None  # 始点から合流ノードまで（ランプ上）
    added_lane_m: float | None = None  # 合流ノードから終点まで（本線上）
    notes: list[str] = field(default_factory=list)

    def metrics(self) -> dict[str, Any]:
        return {
            "accel_lane_len_m": self.length_m if self.status == "measured" else None,
            "accel_lane_status": self.status,
            "accel_lane_reason": self.reason,
            "accel_lane_method": self.method,
            "accel_lane_confidence": self.confidence,
            "accel_parallel_m": self.parallel_m,
            "accel_added_lane_m": self.added_lane_m,
        }


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
        return AccelLane("excluded", "no_mainline")
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
        return AccelLane("excluded", "lanes_inconsistent")
    if lanes_known and la > lb:  # type: ignore[operator]
        found = False
        merge_tag = False
        for i, (u, _v, d) in enumerate(down_edges):
            if i > 0:  # 区間の途中のノード（u）で分岐・合流がないか
                if _has_link(g, u, out=True):
                    return AccelLane("excluded", "auxiliary_to_exit")
                if _has_link(g, u, out=False):
                    return AccelLane("excluded", "another_merge")
            n = lanes_of(d)
            if n is not None and n <= lb:  # type: ignore[operator]
                end_node, found = u, True
                break
            merge_tag = merge_tag or "merge_to" in str(d["tags"].get("turn:lanes", ""))
            added += float(d["length_m"])
        if not found:
            walked = sum(float(d["length_m"]) for _, _, d in down_edges)
            return AccelLane("excluded", "lane_addition" if walked >= search else "mainline_ends")
        if _has_link(g, end_node, out=True):  # 車線が出口の分岐で終わる＝付加車線
            return AccelLane("excluded", "auxiliary_to_exit")
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
        return AccelLane("excluded", "no_parallel_section", parallel_m=par, added_lane_m=added)
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
    )
