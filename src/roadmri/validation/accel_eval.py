"""加速車線長の検証用の採点（ADR-0020）。航空写真で判読した始点・終点の範囲と、現行（地図データ）・補正候補を
同じ地点で比べる。始点・終点・距離の誤差と保留を別々に数え、距離が近くても端点のずれが相殺されている場合を示す。

位置はすべて「測点」（ランプの始まりから、ランプ→合流ノード→下流の本線と道筋に沿った距離、m）。
判読の範囲（[下限, 上限]）の中点を基準とし、範囲そのものも残す（範囲内かどうかの判定に使う）。
これは同じ航空写真から作った暫定の基準で、独立した測量値ではない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# 写真の判読の種類（data/validation/accel_regional/references.json の type）
REF_TYPES = {
    "merge": "通常合流（車線が消失）",
    "continuation": "下流まで車線継続（車線追加）",
    "to_exit": "次の出口へ接続（出口まで続く付加車線）",
    "uncertain": "判定保留（写真では確定できない）",
}
# 判読の種類と、地図データ（OSM）の判定で期待するもの
EXPECTED_REASON = {"continuation": "lane_addition", "to_exit": "auxiliary_to_exit"}


@dataclass
class Thresholds:
    endpoint_m: float  # 端点のずれがこれ以上で「ずれ」とみなす（相殺・行き過ぎの判定）
    same_m: float  # 誤差の差がこれ以下なら「同値」
    fallback_m: float  # 候補の端点が現行値とこれ以下なら「現行値に戻した」

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Thresholds:
        c = settings["validation"]["accel_eval"]
        return cls(float(c["endpoint_m"]), float(c["same_m"]), float(c["fallback_m"]))


@dataclass
class Site:
    code: str
    ramp_id: str | None
    country: str | None
    ref_type: str
    start_range: list[float] | None
    end_range: list[float] | None
    length_range: list[float] | None
    baseline: list[float] | None  # [始点, 終点]
    candidate: list[float] | None
    baseline_length: float | None = None  # 長さだけ分かる場合
    candidate_length: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)  # 候補の端点の画像証拠（None は証拠なし）
    held_reason: str | None = None
    fallback: frozenset[str] = frozenset()  # 候補の手法が現行値に戻した端点（"start"・"end"）


def _mid(r: list[float] | None) -> float | None:
    return None if not r else float(np.mean(r))


def _length_range(s: list[float] | None, e: list[float] | None) -> list[float] | None:
    if not s or not e:
        return None
    return [float(e[0] - s[1]), float(e[1] - s[0])]


def _len(p: list[float] | None, given: float | None) -> float | None:
    if p:
        return float(p[1] - p[0])
    return given


def score_site(site: Site, th: Thresholds) -> dict[str, Any]:
    """1 地点の採点。誤差は「推定 − 判読の中点」（正なら下流側・長い側）。"""
    out: dict[str, Any] = {
        "code": site.code,
        "ramp_id": site.ramp_id,
        "country": site.country,
        "ref_type": site.ref_type,
    }
    flags: list[str] = []
    if site.ref_type != "merge":
        # 非合流・保留：距離を出さないのが正しい（保留は採点しない）
        out["emitted"] = {
            "baseline": site.baseline is not None or site.baseline_length is not None,
            "candidate": site.candidate is not None or site.candidate_length is not None,
        }
        out["outcome"] = (
            "not_scored"
            if site.ref_type == "uncertain"
            else ("correct_null" if not out["emitted"]["candidate"] else "emitted_for_non_merge")
        )
        out["flags"] = flags
        return out
    s_mid, e_mid = _mid(site.start_range), _mid(site.end_range)
    lr = site.length_range or _length_range(site.start_range, site.end_range)
    l_mid = _mid(lr) if lr else (e_mid - s_mid if s_mid is not None and e_mid is not None else None)
    out["reference"] = {"start": site.start_range, "end": site.end_range, "length": lr}

    def errors(p: list[float] | None, given_len: float | None) -> dict[str, Any] | None:
        length = _len(p, given_len)
        if length is None:
            return None
        e: dict[str, Any] = {"length": length, "length_err": length - l_mid if l_mid is not None else None}
        if p:
            e["start"], e["end"] = float(p[0]), float(p[1])
            e["start_err"] = p[0] - s_mid if s_mid is not None else None
            e["end_err"] = p[1] - e_mid if e_mid is not None else None
        e["length_in_range"] = bool(lr and lr[0] <= length <= lr[1])
        return e

    base = errors(site.baseline, site.baseline_length)
    cand = errors(site.candidate, site.candidate_length)
    out["baseline"], out["candidate"] = base, cand
    if cand is None:
        out["outcome"] = "held"
        out["held_reason"] = site.held_reason
        out["flags"] = flags
        return out
    # 端点ごと・距離の比較（同じ地点で現行と比べる）
    cmp: dict[str, str] = {}
    for k in ("start_err", "end_err", "length_err"):
        b, c = (base or {}).get(k), cand.get(k)
        if b is None or c is None:
            continue
        d = abs(b) - abs(c)
        cmp[k.removesuffix("_err")] = "improved" if d > th.same_m else ("worse" if d < -th.same_m else "same")
    out["comparison"] = cmp
    out["outcome"] = cmp.get("length", "unpaired")
    # 相殺：距離は範囲内（または誤差が小さい）なのに、始点・終点が同じ向きに大きくずれている
    se, ee = cand.get("start_err"), cand.get("end_err")
    if (
        se is not None
        and ee is not None
        and abs(se) >= th.endpoint_m
        and abs(ee) >= th.endpoint_m
        and se * ee > 0
    ) and (cand["length_in_range"] or abs(cand["length_err"] or 0) < th.endpoint_m):
        side = "手前（上流）" if se < 0 else "先（下流）"
        flags.append(f"相殺：距離は判読範囲内だが、始点 {se:+.0f} m・終点 {ee:+.0f} m とも{side}にずれている")
    # 現行値に戻した端点（画像の証拠が基準に届かず、補正をあきらめた）
    for i, k in ((0, "start"), (1, "end")):
        same = bool(
            site.candidate and site.baseline and abs(site.candidate[i] - site.baseline[i]) <= th.fallback_m
        )
        no_evidence = k in site.evidence and site.evidence[k] is None
        if k in site.fallback or (same and no_evidence):
            flags.append(f"{'始点' if k == 'start' else '終点'}：画像の証拠が基準に届かず、現行値に戻した")
    # 終点が判読範囲より下流（別の位置を拾った可能性）／距離が範囲を大きく超える
    if cand.get("end_err") is not None and site.end_range and cand["end"] > site.end_range[1] + th.endpoint_m:
        flags.append(
            f"終点が判読範囲より {cand['end'] - site.end_range[1]:.0f} m 下流（別の位置を終点として拾った可能性）"
        )
    elif lr and cand["length"] > lr[1] + th.endpoint_m:
        why = (
            "別の位置を終点として拾った可能性"
            if site.candidate
            else "端点の位置がないため、どちらの端点のずれかは判別できない"
        )
        flags.append(f"距離が判読範囲の上限を {cand['length'] - lr[1]:.0f} m 超える（{why}）")
    if lr and cand["length"] < lr[0] - th.endpoint_m:
        flags.append(f"距離が判読範囲の下限より {lr[0] - cand['length']:.0f} m 短い")
    out["flags"] = flags
    return out


def summarize(results: list[dict[str, Any]], within_m: float) -> dict[str, Any]:
    """件数（判読の種類別）、保留、端点ごと・距離の平均絶対誤差（現行と候補を同じ地点で）、改善・同値・悪化。"""
    merge = [r for r in results if r["ref_type"] == "merge"]
    paired = [r for r in merge if r.get("candidate") and r.get("baseline")]

    def count(key: str) -> int:
        return sum(
            1 for r in paired if r["baseline"].get(key) is not None and r["candidate"].get(key) is not None
        )

    def mae(key: str, which: str) -> float | None:
        v = [
            abs(r[which][key])
            for r in paired
            if r[which].get(key) is not None and r["candidate"].get(key) is not None
        ]
        return float(np.mean(v)) if v else None

    out: dict[str, Any] = {
        "sites": len(results),
        "by_type": {t: sum(r["ref_type"] == t for r in results) for t in REF_TYPES},
        "merge_scored": len(paired),
        "held": sum(r.get("outcome") == "held" for r in merge),
        "mae": {
            k: {
                "baseline": mae(f"{k}_err", "baseline"),
                "candidate": mae(f"{k}_err", "candidate"),
                "n": count(f"{k}_err"),
            }
            for k in ("start", "end", "length")
        },
        "length_outcomes": {
            o: sum(r.get("outcome") == o for r in paired) for o in ("improved", "same", "worse")
        },
        "endpoint_outcomes": {
            k: {
                o: sum(r.get("comparison", {}).get(k) == o for r in paired)
                for o in ("improved", "same", "worse")
            }
            for k in ("start", "end")
        },
        "length_within": {
            w: sum(abs(r[w].get("length_err") or 1e9) <= within_m for r in paired)
            for w in ("baseline", "candidate")
        },
        "within_m": within_m,
        "cancellation": sum(any(f.startswith("相殺") for f in r.get("flags", [])) for r in paired),
        "non_merge_correct_null": sum(r.get("outcome") == "correct_null" for r in results),
        "non_merge_emitted": sum(r.get("outcome") == "emitted_for_non_merge" for r in results),
    }
    return out


def check_classification(
    refs: dict[str, dict[str, Any]], app: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """写真で非合流と判読した地点で、アプリの判定（対象外の理由・距離 null）が合っているか。
    app は {code: {"status", "reason", "length_m"}}（現在の DB の値）。"""
    out = []
    for code, ref in sorted(refs.items()):
        want = EXPECTED_REASON.get(str(ref.get("type")))
        if want is None:
            continue
        a = app.get(code) or {}
        ok = (
            bool(a)
            and a.get("reason") == want
            and a.get("status") == "excluded"
            and a.get("length_m") is None
        )
        out.append(
            {"code": code, "ref_type": ref["type"], "expected_reason": want, "app": a or None, "ok": ok}
        )
    return out


# ---- 既存の検証ファイル（scripts/accel_regional_*.py の出力）を読む --------------------------------------------


def sites_from_json(data: dict[str, Any]) -> list[Site]:
    """地点の一覧（tests/fixtures/validation/accel_regional_report_sites.json の形）から。"""
    out = []
    for d in data["sites"]:
        out.append(
            Site(
                d["code"],
                d.get("ramp_id"),
                d.get("country"),
                d["ref_type"],
                d.get("start_range"),
                d.get("end_range"),
                d.get("length_range"),
                d.get("baseline"),
                d.get("candidate"),
                baseline_length=d.get("baseline_length"),
                candidate_length=d.get("candidate_length"),
                evidence=d.get("evidence", {}),
                held_reason=d.get("held_reason"),
                fallback=frozenset(d.get("fallback", [])),
            )
        )
    return out


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def sites_from_study(folder: Path, method: str = "regional") -> list[Site]:
    """data/validation/accel_regional の manifest・references・predictions（＋exploratory_width・prospective_*）から地点を作る。

    method：predictions.json の outputs の名前（regional・learned・guarded・image_only・regional_calibrated）、
    または "width"（exploratory_width.json の両側追跡）。prospective_* は常に両側追跡（adjusted）。
    """
    sites: list[Site] = []
    refs = _load(folder / "references.json") or {}
    preds = _load(folder / "predictions.json") or []
    widths = {r["ramp_id"]: r for r in (_load(folder / "exploratory_width.json") or [])}
    for r in preds:
        code = r.get("code")
        if not code or code not in refs:
            continue
        ref = refs[code]
        outs = r.get("outputs", {})
        if method == "width":
            w = widths.get(r["ramp_id"], {})
            cand, ev = w.get("adjusted"), w.get("evidence", {})
        else:
            cand, ev = outs.get(method), {k: r.get("model", {}).get(k) for k in ("start", "end")}
        sites.append(
            Site(
                code,
                r["ramp_id"],
                r.get("country"),
                str(ref.get("type")),
                ref.get("start"),
                ref.get("end"),
                None,
                outs.get("baseline"),
                cand,
                evidence=ev,
                held_reason=None if cand else "候補を出さなかった（保留）",
            )
        )
    prefs = _load(folder / "prospective_references.json") or {}
    for r in _load(folder / "prospective_predictions.json") or []:
        ref = prefs.get(r["code"], {})
        sites.append(
            Site(
                r["code"],
                r["ramp_id"],
                "US",
                str(ref.get("type", "merge")),
                ref.get("start"),
                ref.get("end"),
                None,
                r.get("baseline"),
                r.get("adjusted"),
                evidence=r.get("evidence", {}),
            )
        )
    return sites


def report_markdown(
    results: list[dict[str, Any]], summary: dict[str, Any], classification: list[dict[str, Any]]
) -> str:
    """地点ごとの改善・悪化・保留と、要約の表（日本語）。"""

    def f(v: Any) -> str:
        return "—" if v is None else f"{v:+.0f}"

    lines = [
        "# 加速車線長の補正候補の検証",
        "",
        "誤差は推定−判読範囲の中点（m、正なら下流側・長い側）。始点／終点／距離。",
        "",
    ]
    lines += ["| 地点 | 国 | 判読 | 現行 | 補正候補 | 距離の比較 | 注記 |", "|---|---|---|---|---|---|---|"]
    for r in results:
        if r["ref_type"] != "merge":
            lines.append(
                f"| {r['code']} | {r.get('country') or ''} | {REF_TYPES.get(r['ref_type'], r['ref_type'])} | — | — | "
                f"{'距離を出さない（正）' if r.get('outcome') == 'correct_null' else r.get('outcome')} | |"
            )
            continue
        b, c = r.get("baseline") or {}, r.get("candidate") or {}
        bt = f"{f(b.get('start_err'))}／{f(b.get('end_err'))}／{f(b.get('length_err'))}"
        ct = "保留" if not c else f"{f(c.get('start_err'))}／{f(c.get('end_err'))}／{f(c.get('length_err'))}"
        outcome = {"improved": "改善", "worse": "悪化", "same": "同値", "held": "保留"}.get(
            r.get("outcome", ""), r.get("outcome")
        )
        lines.append(
            f"| {r['code']} | {r.get('country') or ''} | 通常合流 | {bt} | {ct} | {outcome} | {'；'.join(r.get('flags', []))} |"
        )
    m = summary["mae"]
    lines += [
        "",
        "## 要約",
        "",
        f"採点 {summary['merge_scored']} 件（通常合流）、保留 {summary['held']} 件、相殺 {summary['cancellation']} 件。"
        f"距離：改善 {summary['length_outcomes']['improved']}・同値 {summary['length_outcomes']['same']}・悪化 {summary['length_outcomes']['worse']}。"
        f"判読中点からの距離誤差が {summary['within_m']:.0f} m 以内：現行 {summary['length_within']['baseline']}・"
        f"補正候補 {summary['length_within']['candidate']}（{summary['merge_scored']} 件中）。",
        "",
        "| 平均絶対誤差（m） | 件数 | 現行 | 補正候補 |",
        "|---|---:|---:|---:|",
    ]
    for k, label in (("start", "始点"), ("end", "終点"), ("length", "距離")):
        bv, cv = m[k]["baseline"], m[k]["candidate"]
        lines.append(
            f"| {label} | {m[k]['n']} | {'—' if bv is None else f'{bv:.1f}'} | {'—' if cv is None else f'{cv:.1f}'} |"
        )
    if classification:
        lines += [
            "",
            "## 非合流の判定（アプリの現在の値）",
            "",
            "| 地点 | 判読 | 期待 | アプリ | 一致 |",
            "|---|---|---|---|---|",
        ]
        for c in classification:
            a = c["app"] or {}
            lines.append(
                f"| {c['code']} | {REF_TYPES[c['ref_type']]} | {c['expected_reason']} | "
                f"{a.get('status')}・{a.get('reason')}・距離 {a.get('length_m')} | {'○' if c['ok'] else '×'} |"
            )
    return "\n".join(lines) + "\n"
