"""結合テスト：全パイプライン（実データ fixture・合成）とゴールデン照合、手動修正の保持、ID 継承、再現性。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from roadmri.pipeline import run_pipeline
from roadmri.store.duckdb_store import Store
from roadmri.store.manual_edits import add_edit, effective_edits, revert_edit

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


def _golden_check(store: Store, name: str) -> None:
    g = json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    assert int(store.scalar("SELECT count(*) FROM facility")) == g["counts"]["facilities"]
    assert int(store.scalar("SELECT count(*) FROM ramp")) == g["counts"]["ramps"]
    assert int(store.scalar("SELECT count(*) FROM section")) == g["counts"]["sections"]
    fac = {r["normalized_name"]: r for r in store.rows("SELECT * FROM facility")}
    for gf in g["facilities"]:
        f = fac[gf["normalized_name"]]
        for k in ("facility_type", "shape_class", "ramp_count", "is_jct_combined", "is_half_ic"):
            assert f[k] == gf[k], (gf["normalized_name"], k)
    sec = store.df(
        "SELECT s.route_ref, f1.normalized_name a, f2.normalized_name b, s.direction, s.dist_standard_m "
        "FROM section s JOIN facility f1 ON s.from_facility_id=f1.facility_id "
        "JOIN facility f2 ON s.to_facility_id=f2.facility_id"
    )
    for gs in g["sections"]:
        m = sec[
            (sec.route_ref == gs["route_ref"])
            & (sec.a == gs["a"])
            & (sec.b == gs["b"])
            & (sec.direction == gs["direction"])
        ]
        assert len(m) == 1
        assert float(m.iloc[0]["dist_standard_m"]) == pytest.approx(gs["dist_standard_m"], abs=1.0)


def test_tochigi_small_golden(tochigi_db):
    _, store, _result = tochigi_db
    _golden_check(store, "tochigi_small")
    # 実データ：宇都宮IC は日光宇都宮道路（E81）との IC＋JCT 複合、鹿沼IC は料金所を持つ
    utsu = store.one("SELECT * FROM facility WHERE normalized_name='宇都宮|IC'")
    assert utsu["is_jct_combined"] and "E81" in utsu["route_refs"]
    kanuma = store.one("SELECT facility_id FROM facility WHERE normalized_name='鹿沼|IC'")
    assert (
        store.scalar(
            "SELECT count(*) FROM facility_osm_map WHERE facility_id=? AND role='toll'",
            [kanuma["facility_id"]],
        )
        >= 1
    )
    # 地理院 DEM（同梱タイル）で標高が付与されている
    sp = store.df("SELECT z_dem_raw, dem_source FROM sample_point")
    assert sp["z_dem_raw"].notna().mean() > 0.99
    assert set(sp["dem_source"].dropna()) <= {"gsi_dem5a", "gsi_dem10b"}
    dems = store.df("SELECT * FROM dem_dataset")
    assert set(dems["vertical_datum"]) == {"T.P."}
    # 本線 100 m 窓勾配 > 7 % は構造物・DEM 低信頼で説明できること（§14.3）
    steep = store.df(
        "SELECT low_confidence FROM sample_point WHERE element_type='mainline' AND abs(grade_w100) > 7"
    )
    assert steep["low_confidence"].all() if len(steep) else True


def test_synthetic_golden(synthetic_db):
    _, store, _ = synthetic_db
    _golden_check(store, "synthetic")


def test_outputs_have_run_id_and_provenance(tochigi_db):
    cfg, store, result = tochigi_db
    run = store.current_run()
    assert run["run_id"] == result["run_id"]
    for t in ("facility", "ramp", "section"):
        assert set(store.df(f"SELECT DISTINCT run_id FROM {t}")["run_id"]) == {run["run_id"]}
    prov = json.loads(run["provenance"])
    for k in (
        "run_id",
        "git_commit",
        "osm_sources",
        "dem_datasets",
        "params",
        "classifier_version",
        "manual_edit_count",
        "config_hash",
        "attribution",
    ):
        assert k in prov
    assert "© OpenStreetMap contributors" in prov["attribution"] and "国土地理院" in prov["attribution"]
    assert (cfg.path("processed_db").parent / "provenance.json").exists()
    assert (cfg.path("tiles_dir") / "mainline.fgb").exists()


def test_manual_edits_survive_rebuild(fresh_synthetic):
    cfg, store, _ = fresh_synthetic
    fid = store.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成ダイヤモンド|IC'")
    rid = store.scalar("SELECT ramp_id FROM ramp WHERE facility_id=? ORDER BY seq LIMIT 1", [fid])
    e1 = add_edit(store, "facility", fid, "shape_class", "変形ダイヤモンド型", "tester", "目視確認")
    add_edit(store, "ramp", rid, "flow", "collector_distributor", "tester")
    e3 = add_edit(store, "facility", fid, "review_status", "reviewed", "tester")
    revert_edit(store, e3["edit_id"], "tester")
    assert len(effective_edits(store)) == 2
    run_pipeline(cfg, "synthetic", store)
    f = store.one("SELECT * FROM facility WHERE facility_id=?", [fid])
    assert f["shape_class"] == "変形ダイヤモンド型" and f["shape_class_auto"] == "ダイヤモンド型"
    assert store.scalar("SELECT flow FROM ramp WHERE ramp_id=?", [rid]) == "collector_distributor"
    assert store.scalar("SELECT flow_auto FROM ramp WHERE ramp_id=?", [rid]) != "collector_distributor"
    # 追記専用：取り消しも行として残る
    assert int(store.scalar("SELECT count(*) FROM manual_edit")) == 4
    assert store.current_run()["manual_edit_count"] == 2
    res = store.df("SELECT * FROM manual_edit_resolution")
    assert set(res["status"]) == {"active"} and e1["edit_id"] in set(res["edit_id"])


def test_id_inheritance_and_orphaned_edits(fresh_synthetic, tmp_path):
    """構成 Way を変えた fixture で、ID 継承と孤立修正の検出を確認する。"""
    cfg, store, _ = fresh_synthetic
    fid = store.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成ハーフ|IC'")
    rid = store.scalar("SELECT ramp_id FROM ramp WHERE facility_id=? ORDER BY seq LIMIT 1", [fid])
    add_edit(store, "ramp", rid, "excluded", True, "tester")
    sa = store.scalar("SELECT facility_id FROM facility WHERE normalized_name='合成|SA'")
    add_edit(store, "facility", sa, "comment", "SA は後で消える", "tester")
    # 合成 fixture を加工：ハーフ IC の Way ID を振り直し（同じ形状）、SA を削除
    import osmium

    from roadmri.ingest.synthetic import build_synthetic

    b = build_synthetic()
    b.areas = []
    for w in b.ways:
        w["way_id"] += 100000 if w["tags"].get("highway") == "motorway_link" else 0
    from roadmri.ingest.synthetic import TS, to_lonlat

    out = tmp_path / "mod.osm.pbf"
    wr = osmium.SimpleWriter(str(out))
    ts = TS.strftime("%Y-%m-%dT%H:%M:%SZ")
    sa_nodes = {n for n, t in b.node_tags.items() if "合成SA" in t.get("name", "")}
    for n, (x, y) in sorted(b.coords.items()):
        tags = {} if n in sa_nodes else b.node_tags.get(n, {})
        wr.add_node(
            osmium.osm.mutable.Node(id=n, location=to_lonlat(x, y), tags=tags, version=1, timestamp=ts)
        )
    for wd in b.ways:
        wr.add_way(
            osmium.osm.mutable.Way(
                id=wd["way_id"], nodes=wd["nodes"], tags=wd["tags"], version=1, timestamp=ts
            )
        )
    wr.close()
    region = cfg.root / "config" / "regions" / "_test_mod.yaml"
    region.write_text(
        f"id: _test_mod\ncountry: JP\nrange: {{type: bbox, bbox: [139.6, 36.3, 140.1, 36.8]}}\n"
        f"pbf: {{local: {out}}}\ndem: synthetic\n",
        encoding="utf-8",
    )
    try:
        run_pipeline(cfg, "_test_mod", store)
    finally:
        region.unlink()
    res = store.df(
        "SELECT r.status, r.resolved_target_id, e.target_type FROM manual_edit_resolution r "
        "JOIN manual_edit e USING (edit_id)"
    )
    ramp_res = res[res.target_type == "ramp"].iloc[0]
    assert ramp_res["status"] == "active"
    assert store.scalar("SELECT excluded FROM ramp WHERE ramp_id=?", [ramp_res["resolved_target_id"]])
    assert res[res.target_type == "facility"].iloc[0]["status"] == "orphaned"
    assert store.scalar("SELECT count(*) FROM quality_flag WHERE code='ORPHANED_EDIT'") == 1


def _numeric_hash(store: Store) -> str:
    h = hashlib.sha256()
    for t, key in (
        ("facility", "facility_id"),
        ("ramp", "ramp_id"),
        ("section", "section_id"),
        ("sample_point", "element_id, seq"),
        ("section_distance", "section_id, definition"),
    ):
        df = store.df(f"SELECT * FROM {t} ORDER BY {key}")
        num = df.select_dtypes(include="number")
        h.update(t.encode())
        h.update(pd.util.hash_pandas_object(num, index=False).values.tobytes())
    return h.hexdigest()


def test_reproducibility(tmp_path, cfg):
    """同じ fixture・同じ設定で 2 回 build → 数値列のハッシュが一致。設定を変えると config_hash と run_id が変わる。"""
    from tests.conftest import _cfg_for

    c1 = _cfg_for(tmp_path / "a", cfg)
    c2 = _cfg_for(tmp_path / "b", cfg)
    s1, s2 = Store(c1.path("processed_db")), Store(c2.path("processed_db"))
    r1 = run_pipeline(c1, "tochigi_small", s1)
    r2 = run_pipeline(c2, "tochigi_small", s2)
    assert r1["run_id"] == r2["run_id"]
    assert _numeric_hash(s1) == _numeric_hash(s2)
    c3 = _cfg_for(tmp_path / "c", cfg.with_overrides({"analysis.grade.default_window_m": 50}))
    s3 = Store(c3.path("processed_db"))
    r3 = run_pipeline(c3, "tochigi_small", s3)
    assert c3.config_hash != c1.config_hash
    assert r3["run_id"] != r1["run_id"]


def test_resume_from_step(tmp_path, cfg):
    from tests.conftest import _cfg_for

    c = _cfg_for(tmp_path, cfg)
    st = Store(c.path("processed_db"))
    run_pipeline(c, "synthetic", st, to_step="sections")
    assert not st.has_data()
    res = run_pipeline(c, "synthetic", st, from_step="metrics")
    assert st.has_data() and res["counts"]["facilities"] == 8
    with pytest.raises(ValueError):
        run_pipeline(c, "synthetic", st, from_step="nonexistent")


def test_us_fixture_golden(tmp_path, cfg):
    """米国（Ann Arbor, MI の I-94 / US-23）：ゴールデン照合、方位、出口番号の施設名、mph、3DEP（NAVD88）。"""
    from tests.conftest import _cfg_for

    c = _cfg_for(tmp_path, cfg)
    st = Store(c.path("processed_db"))
    run_pipeline(c, "us_annarbor_small", st)
    _golden_check(st, "us_annarbor_small")
    fac = st.df("SELECT * EXCLUDE (geom, hull) FROM facility")
    assert set(fac["country"]) == {"US"} and all(f.startswith("US-F-") for f in fac["facility_id"])
    assert set(fac["admin_code"]) == {"26"}
    jct = fac[fac["facility_type"] == "JCT"].iloc[0]
    assert jct["shape_class"] == "クローバーリーフ型" and set(jct["route_refs"]) == {"I 94", "US 23"}
    assert set(fac[fac["normalized_name"] == "I 94 Exit 177"]["shape_class"]) == {"部分クローバー型"}
    assert {"east", "west"} <= set(st.df("SELECT DISTINCT direction FROM section")["direction"])
    ramps = st.df("SELECT branch_side, maxspeed_kmh FROM ramp")
    assert (ramps["branch_side"].dropna() == "right").all()  # 右側通行
    assert 113 in set(ramps["maxspeed_kmh"].dropna())  # 70 mph
    assert set(st.df("SELECT vertical_datum FROM dem_dataset")["vertical_datum"]) == {"NAVD88"}
    # 出口番号はマイルポスト：Exit 177 → 180 は約 3 マイル（±3 %）
    d = st.scalar(
        "SELECT avg(s.dist_standard_m) FROM section s JOIN facility a ON s.from_facility_id=a.facility_id "
        "JOIN facility b ON s.to_facility_id=b.facility_id WHERE a.normalized_name='I 94 Exit 177' "
        "AND b.normalized_name LIKE 'I 94 Exit 180%'"
    )
    assert abs(d - 3 * 1609.344) / (3 * 1609.344) < 0.03


def test_multi_region_jp_us(tmp_path, cfg):
    """日米横断：2 地域を 1 つの DB に公開し、国をまたいで検索・統計比較できる。手動修正は両地域で保持。"""
    from roadmri.analysis.compare import compare_ranges
    from roadmri.analysis.search import run_query
    from tests.conftest import _cfg_for

    c = _cfg_for(tmp_path, cfg)
    st = Store(c.path("processed_db"))
    res = run_pipeline(c, "tochigi_small,us_annarbor_small", st)
    assert res["counts"]["facilities"] == 7
    assert dict(st.df("SELECT country, count(*) n FROM facility GROUP BY 1").values.tolist()) == {
        "JP": 3,
        "US": 4,
    }
    assert st.current_run()["region_id"] == "tochigi_small+us_annarbor_small"
    assert set(st.df("SELECT DISTINCT run_id FROM ramp")["run_id"]) == {res["run_id"]}
    us = run_query(
        st,
        {
            "target": "ramp",
            "where": {"all": [{"field": "country", "op": "eq", "value": "US"}]},
            "limit": 10000,
        },
    )
    assert len(us) == 40 and all(r.startswith("US-F-") for r in us["ramp_id"])
    r = compare_ranges(
        st,
        c,
        {
            "range_a": {"type": "country", "countries": ["JP"]},
            "range_b": {"type": "country", "countries": ["US"]},
            "bootstrap": 200,
        },
    )
    assert r["a"]["counts"]["facility"] == 3 and r["b"]["counts"]["facility"] == 4
    assert r["b"]["counts"]["section"] == 3  # 米国は路線ごとに片方向（west）だけ数える
    jp = st.scalar("SELECT facility_id FROM facility WHERE normalized_name='鹿沼|IC'")
    usf = st.scalar("SELECT facility_id FROM facility WHERE normalized_name='I 94 Exit 177'")
    add_edit(st, "facility", jp, "review_status", "reviewed", "t")
    add_edit(st, "facility", usf, "review_status", "reviewed", "t")
    run_pipeline(c, "tochigi_small,us_annarbor_small", st)
    assert set(st.df("SELECT status FROM manual_edit_resolution")["status"]) == {"active"}
    assert st.scalar("SELECT count(*) FROM facility WHERE review_status='reviewed'") == 2
