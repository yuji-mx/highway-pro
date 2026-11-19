"""加速車線の表示状態（ADR-0020）：対象外（車線追加・出口まで続く付加車線）と判定保留の区別、端点ごとの根拠、
手動の判定保留・対象外の理由、以前の DB の「未評価」。日本・米国の実データ fixture（宇都宮IC〜鹿沼IC、Ann Arbor）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from roadmri.api.main import create_app
from tests.conftest import _build


@pytest.fixture(scope="module")
def mixed(tmp_path_factory, cfg):
    c, store, _ = _build(tmp_path_factory.mktemp("accel_states"), cfg, "tochigi_small,us_annarbor_small")
    with TestClient(create_app(c, store)) as client:
        client.state = store  # type: ignore[attr-defined]
        yield client


def _rid(client, reason=None, status=None):
    sql = "SELECT ramp_id FROM ramp WHERE " + ("accel_lane_reason=?" if reason else "accel_lane_status=?")
    return client.state.scalar(sql + " ORDER BY ramp_id LIMIT 1", [reason or status])


@pytest.mark.parametrize(
    ("reason", "label"),
    [("lane_addition", "対象外・車線追加"), ("auxiliary_to_exit", "対象外・出口まで続く付加車線")],
)
def test_excluded_cases_have_null_length_and_reason(mixed, reason, label):
    """R13・R14（車線追加）・R08・R11（出口まで続く付加車線）と同じ種類：距離は null、理由を区別して表示。"""
    rid = _rid(mixed, reason=reason)
    assert rid, f"fixture に {reason} がない"
    a = mixed.get(f"/api/ramps/{rid}/accel_lane").json()
    assert a["auto"]["length_m"] is None and a["auto"]["status"] == "excluded"
    assert (
        a["display"]["state"] == "excluded"
        and a["display"]["label"] == label
        and a["display"]["length_m"] is None
    )
    assert mixed.get(f"/api/ramps/{rid}").json()["accel_display"]["label"] == label
    items = mixed.get("/api/accel_lanes?state=excluded&limit=500").json()["items"]
    row = next(x for x in items if x["ramp_id"] == rid)
    assert row["display_label"] == label and row["display_length_m"] is None and row["auto_len_m"] is None


def test_measured_is_provisional_with_endpoint_evidence(mixed):
    rid = _rid(mixed, status="measured")
    a = mixed.get(f"/api/ramps/{rid}/accel_lane").json()
    d = a["display"]
    assert d["state"] == "provisional" and d["label"] == "暫定値（地図データ）"
    assert d["length_m"] == a["auto"]["length_m"] == d["provisional_length_m"]
    for w in ("start", "end"):
        e = d["endpoints"][w]
        assert e["evaluated"] and e["method"] and e["method_text"].startswith("地図データ")
        assert e["confidence"] in {"high", "medium", "low"} and e["pending"] is None and e["lonlat"]


def test_pending_shows_unconfirmed_endpoint_and_reason(mixed):
    store = mixed.state
    rid = store.scalar("SELECT ramp_id FROM ramp WHERE accel_lane_status='pending' ORDER BY ramp_id LIMIT 1")
    assert rid
    d = mixed.get(f"/api/ramps/{rid}/accel_lane").json()["display"]
    assert d["state"] == "pending" and d["label"].startswith("判定保留（") and d["length_m"] is None
    assert any(e["pending"] for e in d["endpoints"].values())
    assert mixed.get("/api/accel_lanes?state=pending&limit=500").json()["total"] >= 1


def test_old_db_rows_are_unevaluated_and_pending_reasons_are_not_excluded(mixed):
    """以前の DB：端点の根拠の列が空、判定保留の理由も status='excluded'。読み込めて「未評価」・判定保留になる。"""
    store = mixed.state
    rid = store.scalar("SELECT ramp_id FROM ramp WHERE accel_lane_status='pending' ORDER BY ramp_id LIMIT 1")
    keep = store.one("SELECT * FROM ramp WHERE ramp_id=?", [rid])
    cols = [f"accel_{w}_{k}" for w in ("start", "end") for k in ("method", "confidence", "pending")]
    store.execute(
        f"UPDATE ramp SET accel_lane_status='excluded', {', '.join(c + '=NULL' for c in cols)} WHERE ramp_id=?",
        [rid],
    )
    try:
        d = mixed.get(f"/api/ramps/{rid}/accel_lane").json()["display"]
        assert d["state"] == "pending" and d["auto_status"] == "pending"
        pending_end = [w for w, e in d["endpoints"].items() if e["pending"]]
        assert pending_end  # 理由コードから、確定できなかった端点を補う
        mrid = _rid(mixed, status="measured")
        store.execute(f"UPDATE ramp SET {', '.join(c + '=NULL' for c in cols)} WHERE ramp_id=?", [mrid])
        e = mixed.get(f"/api/ramps/{mrid}/accel_lane").json()["display"]["endpoints"]
        assert not e["start"]["evaluated"] and not e["end"]["evaluated"]  # 画面では「未評価」
        assert mixed.get("/api/accel_lanes?auto_status=pending&limit=500").json()["total"] >= 1
    finally:
        store.execute(
            f"UPDATE ramp SET accel_lane_status=?, {', '.join(c + '=?' for c in cols)} WHERE ramp_id=?",
            [keep["accel_lane_status"], *[keep[c] for c in cols], rid],
        )


def test_manual_pending_and_exclusion_reasons_keep_auto_value(mixed):
    """手動の判定保留（端点と理由）・対象外の理由。自動の値は変わらず、取り消すと元に戻る（ADR-0017）。"""
    store = mixed.state
    rid = _rid(mixed, status="measured")
    auto = store.scalar("SELECT accel_lane_len_m FROM ramp WHERE ramp_id=?", [rid])
    r = mixed.post(
        f"/api/ramps/{rid}/accel_lane",
        json={"action": "pending", "pending": {"end": "shadow"}, "comment": "影"},
    )
    assert r.status_code == 201
    d = r.json()["display"]
    assert d["state"] == "pending" and d["label"] == "判定保留（終点：影で目印が見えない）"
    assert d["provisional_length_m"] == auto and d["length_m"] is None  # 地図データによる暫定値は残す
    assert store.scalar("SELECT accel_lane_len_m FROM ramp WHERE ramp_id=?", [rid]) == auto
    assert mixed.post(f"/api/ramps/{rid}/accel_lane", json={"action": "pending"}).status_code == 422
    r2 = mixed.post(
        f"/api/ramps/{rid}/accel_lane",
        json={"action": "not_accel_lane", "exclusion_reason": "auxiliary_to_exit"},
    )
    assert r2.json()["display"]["label"] == "対象外・出口まで続く付加車線"
    row = next(x for x in mixed.get("/api/accel_lanes?limit=500").json()["items"] if x["ramp_id"] == rid)
    assert row["display_state"] == "excluded" and row["display_label"] == "対象外・出口まで続く付加車線"
    for e in reversed(r2.json()["history"]):
        if e["status"] == "active" and e["new_value"]["action"] in ("pending", "not_accel_lane"):
            mixed.post(f"/api/edits/{e['edit_id']}/revert")
    assert mixed.get(f"/api/ramps/{rid}/accel_lane").json()["display"]["state"] == "provisional"
    assert store.scalar("SELECT accel_lane_len_m FROM ramp WHERE ramp_id=?", [rid]) == auto
    assert mixed.get("/api/accel_lanes?state=bogus").status_code == 422
