"""補正候補の採点（ADR-0020）。data/validation/accel_regional/REPORT.md の数値で、始点・終点・距離の誤差と保留を
分けて評価できること、端点の誤差の相殺（R10）・行き過ぎ（P01）・現行値への後戻り（P03）を示せることを確かめる。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roadmri.validation.accel_eval import (
    Thresholds,
    check_classification,
    report_markdown,
    score_site,
    sites_from_json,
    summarize,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "validation" / "accel_regional_report_sites.json"


@pytest.fixture(scope="module")
def th(cfg) -> Thresholds:
    return Thresholds.from_settings(cfg.settings)


@pytest.fixture(scope="module")
def results(th):
    sites = sites_from_json(json.loads(FIX.read_text(encoding="utf-8")))
    return {r["code"]: r for r in (score_site(s, th) for s in sites)}


def test_r10_length_in_range_but_both_endpoints_early(results):
    """R10：両側追跡の 288 m は判読範囲 267〜320 m に入るが、始点・終点とも約 80 m 手前（相殺）。"""
    r = results["R10"]
    c = r["candidate"]
    assert c["length"] == 288 and c["length_in_range"]
    assert r["reference"]["length"] == [267, 320]
    assert c["start_err"] == pytest.approx(-78) and c["end_err"] == pytest.approx(-83.5)
    assert any(f.startswith("相殺") and "手前" in f for f in r["flags"])
    # 距離だけなら改善だが、端点ごとにも示す（始点・終点とも改善しているが、まだ 80 m ずれている）
    assert r["comparison"] == {"start": "improved", "end": "improved", "length": "improved"}


def test_r06_endpoints_separately(results):
    r = results["R06"]
    assert r["candidate"]["start_err"] == pytest.approx(-46)
    assert r["candidate"]["end_err"] == pytest.approx(6.5)
    assert not any(f.startswith("相殺") for f in r["flags"])


def test_p01_overshoot_regression(results):
    """P01：真の終点より下流を選んだ（495 m、判読 244〜295 m）。悪化・行き過ぎとして示す。"""
    r = results["P01"]
    assert r["outcome"] == "worse"
    assert any("上限を 200 m 超える" in f for f in r["flags"])


def test_p03_fallback_to_short_baseline_regression(results):
    """P03：終点の画像証拠が基準に届かず、短すぎる現行の終点に戻った（63 m、判読 429〜507 m）。"""
    r = results["P03"]
    assert any(f.startswith("終点：画像の証拠が基準に届かず") for f in r["flags"])
    assert any("下限より" in f for f in r["flags"])
    assert not r["candidate"]["length_in_range"]


@pytest.mark.parametrize(
    ("code", "outcome"), [("P02", "improved"), ("P04", "improved"), ("P05", "worse"), ("P06", "improved")]
)
def test_prospective_outcomes(results, code, outcome):
    assert results[code]["outcome"] == outcome


def test_p05_start_early_makes_length_worse(results):
    r = results["P05"]
    assert any("上限を" in f for f in r["flags"])


def test_summary_matches_report(results, th):
    s = summarize(list(results.values()), th.endpoint_m)
    p = [results[c] for c in ("P01", "P02", "P03", "P04", "P05", "P06")]
    ps = summarize(p, th.endpoint_m)
    assert ps["length_outcomes"] == {"improved": 4, "same": 0, "worse": 2}  # REPORT：4 件改善・2 件悪化
    assert ps["length_within"] == {"baseline": 1, "candidate": 3}  # REPORT：1/6 → 3/6
    assert s["cancellation"] == 1 and s["by_type"]["continuation"] == 2 and s["by_type"]["to_exit"] == 2
    assert s["non_merge_correct_null"] == 4 and s["non_merge_emitted"] == 0
    md = report_markdown(list(results.values()), s, [])
    assert "| R10 |" in md and "相殺" in md and "保留" in md


def test_classification_of_non_merge_sites():
    """R13・R14（車線追加）、R08・R11（出口まで続く付加車線）：アプリが距離 null と理由を返しているか。"""
    refs = {
        c: {"type": t}
        for c, t in (("R13", "continuation"), ("R14", "continuation"), ("R08", "to_exit"), ("R11", "to_exit"))
    }
    app = {
        "R13": {"status": "excluded", "reason": "lane_addition", "length_m": None},
        "R14": {"status": "excluded", "reason": "lane_addition", "length_m": None},
        "R08": {"status": "excluded", "reason": "auxiliary_to_exit", "length_m": None},
        "R11": {"status": "pending", "reason": "lanes_unknown_downstream", "length_m": None},
    }
    out = {c["code"]: c["ok"] for c in check_classification(refs, app)}
    assert out == {"R08": True, "R11": False, "R13": True, "R14": True}
