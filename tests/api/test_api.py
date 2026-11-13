"""API テスト：全エンドポイントの正常系・異常系（404、422、ジョブ失敗）、OpenAPI スナップショット、応答時間。"""

from __future__ import annotations

import time
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from roadmri.api.main import create_app


@pytest.fixture(scope="module")
def client(synthetic_db):
    cfg, store, _ = synthetic_db
    app = create_app(cfg, store)
    with TestClient(app) as c:
        c.state = app.state.roadmri  # type: ignore[attr-defined]
        yield c


def wait(c, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed"):
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


def fid(c, key="合成ダイヤモンド|IC"):
    return c.state.store.scalar("SELECT facility_id FROM facility WHERE normalized_name=?", [key])


def test_meta_display_settings(client):
    m = client.get("/api/meta").json()
    assert m["map"]["label_max_count"] > 0 and m["map"]["tiles"]["layers"]["general"]["min_zoom"] > 0
    assert m["list"]["page_size"] > 0


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["counts"]["facility"] == 8 and m["has_data"]
    assert "© OpenStreetMap contributors" in m["attribution"]
    assert m["run"]["run_id"]


def test_regions(client):
    rs = client.get("/api/regions").json()
    ids = {r["id"] for r in rs}
    assert {"tochigi", "tochigi_small", "synthetic"} <= ids
    assert next(r for r in rs if r["id"] == "synthetic")["status"] == "processed"
    assert client.get("/api/regions/synthetic/status").status_code == 200
    assert client.get("/api/regions/nope/status").status_code == 404
    bad = client.post(
        "/api/regions", json={"id": "Bad Id!", "label": "x", "range": {"type": "bbox", "bbox": [0, 0, 1, 1]}}
    )
    assert bad.status_code == 422 and bad.headers["content-type"].startswith("application/problem+json")
    bad = client.post(
        "/api/regions",
        json={
            "id": "selfx",
            "label": "x",
            "range": {
                "type": "polygon",
                "polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            },
        },
    )
    assert bad.status_code == 422 and "自己交差" in bad.json()["detail"]


def test_pipeline_job_and_failure(client):
    r = client.post("/api/pipeline/run", json={"region": "nope"})
    assert r.status_code == 404
    j = client.post("/api/pipeline/run", json={"region": "synthetic", "from_step": "nonexistent"}).json()
    assert wait(client, j["job_id"])["status"] == "failed"
    assert client.get("/api/jobs/NOPE").status_code == 404


def test_layers(client):
    r = client.get("/api/layers/mainline.fgb")
    assert r.status_code == 200 and r.content[:3] == b"fgb"
    r = client.get("/api/layers/ramps.fgb?bbox=139.8,36.4,139.9,36.7&zoom=12")
    assert r.status_code == 200 and r.content[:3] == b"fgb"
    gj = client.get("/api/layers/facilities.geojson").json()
    assert len(gj["features"]) == 8
    assert client.get("/api/layers/unknown.fgb").status_code == 404
    assert client.get("/api/layers/ramps.fgb?bbox=1,2").status_code == 422


def _tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    import math

    n = 2**z
    lr = math.radians(lat)
    return int((lon + 180) / 360 * n), int((1 - math.log(math.tan(lr) + 1 / math.cos(lr)) / math.pi) / 2 * n)


def test_tiles(client):
    import mapbox_vector_tile

    TILE_PROPS = client.get("/api/meta").json()["map"]["tiles"]["layers"]["mainline"]["props"]

    summ = client.get("/api/layers/summary").json()
    w, so, e, n = summ["bounds"]
    assert summ["z_range"][0] <= summ["z_range"][1] and summ["shapes"]
    run_id = client.get("/api/meta").json()["run"]["run_id"]
    lon, lat = (w + e) / 2, (so + n) / 2
    counts = {}
    for z in (6, 12, 14):
        x, y = _tile_of(lon, lat, z)
        r = client.get(f"/api/tiles/mainline/{z}/{x}/{y}.pbf?v={run_id}")
        assert r.status_code == 200 and r.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert "immutable" in r.headers["cache-control"]
        layer = mapbox_vector_tile.decode(r.content)["mainline"]
        assert layer["extent"] == 4096 and layer["features"]
        # 地図で使う属性だけ（設定の props）を入れる
        assert "direction" in layer["features"][0]["properties"]
        assert all(set(f["properties"]) <= set(TILE_PROPS) for f in layer["features"])
        counts[z] = len(layer["features"])
    # 広域（z6）は連続する要素をまとめた版なので、詳細（z14）より 1 タイルの要素が少ないか同じ
    assert counts[6] <= counts[12]
    # 古い URL（公開 ID が違う）はブラウザに長期キャッシュさせない
    x, y = _tile_of(lon, lat, 12)
    assert "no-cache" in client.get(f"/api/tiles/mainline/12/{x}/{y}.pbf?v=old").headers["cache-control"]
    # 最小ズーム未満の一般道は空のタイル
    assert (
        client.get(f"/api/tiles/general/5/{_tile_of(lon, lat, 5)[0]}/{_tile_of(lon, lat, 5)[1]}.pbf").content
        == b""
    )
    assert client.get("/api/tiles/unknown/1/0/0.pbf").status_code == 404
    assert client.get("/api/tiles/mainline/3/9/0.pbf").status_code == 404
    # 低ズーム用のまとめ版は GeoJSON でも取れる（範囲統計比較の重ね描き用）
    base = client.get("/api/layers/mainline.geojson").json()["features"]
    coarse = client.get("/api/layers/mainline.geojson?lod=16").json()["features"]
    assert 0 < len(coarse) < len(base)


def test_facilities(client):
    r = client.get("/api/facilities").json()
    assert r["total"] == 8
    assert client.get("/api/facilities?type=JCT").json()["total"] == 2
    assert client.get("/api/facilities?q=クローバー").json()["items"][0]["name"] == "合成クローバーIC"
    assert client.get("/api/facilities?q=合成クローバーインターチェンジ").json()["total"] >= 1
    assert client.get("/api/facilities?sort=bogus").status_code == 422
    # サーバー側のページ分割・並べ替え（一覧画面）
    page1 = client.get("/api/facilities?sort=name&order=desc&limit=3&offset=0").json()
    page2 = client.get("/api/facilities?sort=name&order=desc&limit=3&offset=3").json()
    assert page1["total"] == page2["total"] == 8 and len(page1["items"]) == 3
    names = [x["name"] for x in page1["items"] + page2["items"]]
    assert names == sorted(names, reverse=True)
    assert not {x["facility_id"] for x in page1["items"]} & {x["facility_id"] for x in page2["items"]}
    # 一覧と同じ条件の CSV（全件、BOM 付き）
    csv = client.get("/api/facilities.csv?type=JCT")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("施設ID,名称,種別") and len(lines) == 1 + 2
    assert client.get("/api/facilities.csv?sort=bogus").status_code == 422
    d = client.get(f"/api/facilities/{fid(client)}").json()
    assert d["shape_class"] == "ダイヤモンド型" and len(d["ramps"]) == 4 and d["sections"]
    assert d["geometry"]["type"] == "Point" and d["classification"]["features"]["ramp_count"] == 4
    assert client.get("/api/facilities/NOPE").status_code == 404
    s = client.get(f"/api/facilities/{fid(client)}/similar?limit=3").json()
    assert len(s["results"]) == 3
    h = client.get(f"/api/facilities/{fid(client)}/history").json()
    assert "自動分類" in h["auto"]["text_ja"]


def test_ramps(client):
    rid = client.state.store.scalar("SELECT ramp_id FROM ramp ORDER BY ramp_id LIMIT 1")
    d = client.get(f"/api/ramps/{rid}").json()
    assert d["ramp_id"] == rid and d["geometry"]["type"] == "LineString" and d["siblings"]
    p = client.get(f"/api/ramps/{rid}/profile").json()["points"]
    assert len(p) > 10 and {"z_dem_raw", "z_smoothed", "z_corrected", "grade_w100", "curvature"} <= set(p[0])
    p2 = client.get(f"/api/ramps/{rid}/profile?step=20").json()["points"]
    assert len(p2) < len(p)
    assert client.get(f"/api/ramps/{rid}/similar").json()["results"]
    assert client.get("/api/ramps/NOPE").status_code == 404
    assert client.get("/api/ramps/NOPE/profile").status_code == 404


def test_sections(client):
    r = client.get("/api/sections?route=SYN1&direction=up").json()
    assert r["items"] and all(s["direction"] == "up" for s in r["items"])
    sid = r["items"][0]["section_id"]
    d = client.get(f"/api/sections/{sid}").json()
    assert {x["definition"] for x in d["distances"]} >= {"standard", "facility_point"}
    assert d["reverse_section_id"]
    assert len(client.get(f"/api/sections/{sid}/profile").json()["points"]) > 10
    assert client.get("/api/sections/NOPE").status_code == 404


def test_route(client):
    a, b = fid(client, "合成トランペット|IC"), fid(client, "合成スマート|SIC") or fid(client, "合成|SIC")
    r = client.post("/api/route", json={"from_facility_id": a, "to_facility_id": b})
    assert r.status_code == 200
    rt = r.json()["routes"][0]
    assert rt["length_m"] > 15000 and len(rt["passed_facilities"]) >= 4 and rt["profile"]
    assert (
        client.post("/api/route?format=geojson", json={"from_facility_id": a, "to_facility_id": b}).json()[
            "type"
        ]
        == "FeatureCollection"
    )
    assert client.post(
        "/api/route?format=csv", json={"from_facility_id": a, "to_facility_id": b}
    ).text.startswith("route_index")
    assert (
        client.post("/api/route", json={"from_facility_id": "NOPE", "to_facility_id": b}).status_code == 404
    )
    assert client.post("/api/route", json={}).status_code == 422


def test_search(client):
    q = {
        "where": {"all": [{"field": "min_radius_m", "op": "lte", "value": 80}]},
        "sort": [{"field": "min_radius_m", "dir": "asc"}],
    }
    r = client.post("/api/search/ramps", json=q).json()
    assert r["count"] > 0 and r["total"] >= r["count"] and "最小R" in r["text"]
    assert client.post("/api/search/facilities", json={"where": {}}).json()["count"] == 8
    assert client.post("/api/search/sections", json={"where": {}}).json()["count"] > 0
    bad = client.post(
        "/api/search/ramps", json={"where": {"all": [{"field": "bogus", "op": "eq", "value": 1}]}}
    )
    assert bad.status_code == 422 and "検索できない" in bad.json()["detail"]
    assert client.post("/api/search/buildings", json={}).status_code == 422
    t = client.post(
        "/api/search/text",
        json={"target": "ramp", "where": {"all": [{"field": "grade_inst_max", "op": "gte", "value": 9}]}},
    ).json()
    assert t["warnings"] and "最大瞬間勾配" in t["text"]
    assert len(client.get("/api/presets").json()) == 9
    assert "ramp" in client.get("/api/search/fields").json()


def test_saved(client):
    r = client.post(
        "/api/saved-queries", json={"name": "q1", "target": "ramp", "query": {"target": "ramp", "where": {}}}
    )
    assert r.status_code == 201
    qid = r.json()["query_id"]
    assert any(x["query_id"] == qid for x in client.get("/api/saved-queries").json())
    assert client.delete(f"/api/saved-queries/{qid}").status_code == 204
    assert client.delete(f"/api/saved-queries/{qid}").status_code == 404
    r = client.post(
        "/api/saved-ranges", json={"name": "A", "spec": {"type": "route", "route_refs": ["SYN1"]}}
    )
    rid = r.json()["range_id"]
    assert client.get("/api/saved-ranges").json()
    assert client.delete(f"/api/saved-ranges/{rid}").status_code == 204


def test_compare_and_stats(client):
    a, b = fid(client), fid(client, "合成クローバー|IC")
    r = client.post("/api/compare/facilities", json={"a": a, "b": b}).json()
    assert 0 < r["similarity"] < 1 and any(x["differs"] for x in r["table"])
    assert client.post("/api/compare/facilities", json={"a": a, "b": "NOPE"}).status_code == 404
    presets = client.get("/api/stats/presets").json()
    assert len(presets) >= 5
    t0 = time.time()
    s = client.post(
        "/api/stats/compare", json={"range_a": presets[0]["range_a"], "range_b": presets[0]["range_b"]}
    )
    assert s.status_code == 200 and time.time() - t0 < 3.0
    body = s.json()
    assert body["metrics"] and body["insights_ja"] and body["categorical"]
    bad = client.post(
        "/api/stats/compare",
        json={"range_a": {"type": "zzz"}, "range_b": {"type": "route", "route_refs": []}},
    )
    assert bad.status_code == 422


def test_export_formats(client):
    for fmt in ("csv", "xlsx", "geojson", "gpkg", "parquet", "sqlite"):
        j = client.post("/api/export", json={"dataset": "ramps", "format": fmt}).json()
        res = wait(client, j["job_id"])
        assert res["status"] == "succeeded", res
        z = zipfile.ZipFile(BytesIO(client.get(res["download_url"]).content))
        names = z.namelist()
        assert "provenance.json" in names and any(n.startswith("ramps.") for n in names)
        if fmt == "csv":
            data = z.read("ramps.csv")
            assert data.startswith("﻿".encode()) and b"run_id" in data
    j = client.post(
        "/api/export",
        json={
            "dataset": "facilities",
            "format": "csv",
            "query": {
                "target": "facility",
                "where": {"all": [{"field": "facility_type", "op": "eq", "value": "JCT"}]},
            },
        },
    ).json()
    z = zipfile.ZipFile(BytesIO(client.get(wait(client, j["job_id"])["download_url"]).content))
    assert z.read("facilities.csv").decode("utf-8-sig").count("\n") == 3
    assert client.post("/api/export", json={"dataset": "x", "format": "csv"}).status_code == 422
    assert client.post("/api/export", json={"dataset": "ramps", "format": "pdf"}).status_code == 422
    j = client.post(
        "/api/export",
        json={
            "dataset": "ramps",
            "format": "csv",
            "query": {"target": "ramp", "where": {"all": [{"field": "bad", "op": "eq", "value": 1}]}},
        },
    ).json()
    assert wait(client, j["job_id"])["status"] == "failed"


def test_images_and_reports(client):
    r = client.post(
        "/api/images/render", json={"width": 640, "height": 480, "format": "png", "metric": "grade"}
    )
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    r = client.post("/api/images/render", json={"width": 640, "height": 480, "format": "svg"})
    assert r.status_code == 200 and b"<svg" in r.content[:500]
    r = client.post(
        "/api/images/render", json={"width": 640, "height": 480, "format": "jpeg", "background": "#ffffff"}
    )
    assert r.content[:2] == b"\xff\xd8"
    assert client.post("/api/images/render", json={"width": 10}).status_code == 422
    j = client.post(
        "/api/images", json={"width": 400, "height": 300, "facility_ids": [fid(client)], "batch": True}
    ).json()
    res = wait(client, j["job_id"])
    z = zipfile.ZipFile(BytesIO(client.get(res["download_url"]).content))
    assert any(n.endswith(".png") for n in z.namelist())
    j = client.post("/api/reports", json={"limit": 2}).json()
    res = wait(client, j["job_id"], 120)
    assert res["status"] == "succeeded"
    z = zipfile.ZipFile(BytesIO(client.get(res["download_url"]).content))
    html = z.read("report.html").decode()
    assert "© OpenStreetMap contributors" in html and "標高" in html


def test_edits_and_quality(client):
    f = fid(client, "合成ハーフ|IC")
    r = client.post(
        "/api/edits",
        json={
            "target_type": "facility",
            "target_id": f,
            "field": "shape_class",
            "new_value": "ダイヤモンド型",
            "comment": "t",
        },
    )
    assert r.status_code == 201
    assert client.get(f"/api/facilities/{f}").json()["shape_class"] == "ダイヤモンド型"
    eid = r.json()["edit_id"]
    assert client.post(f"/api/edits/{eid}/revert", json={}).status_code == 201
    assert client.get(f"/api/facilities/{f}").json()["shape_class"] == "ハーフIC"
    assert (
        client.post(
            "/api/edits", json={"target_type": "facility", "target_id": f, "field": "lon", "new_value": 1}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/edits",
            json={"target_type": "facility", "target_id": "NOPE", "field": "comment", "new_value": "x"},
        ).status_code
        == 404
    )
    assert client.post("/api/edits/NOPE/revert", json={}).status_code == 404
    assert len(client.get("/api/edits").json()) >= 2
    assert isinstance(client.get("/api/quality/flags?severity=warn").json(), list)
    q = client.get("/api/review-queue").json()
    assert "facilities" in q and "orphaned_edits" in q
    assert client.get("/api/changes").json()["phase"] == "P4"


def test_openapi_snapshot(client, snapshot):
    spec = client.get("/openapi.json").json()
    paths = sorted(f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops)
    assert paths == snapshot


def test_response_times(client):
    t0 = time.time()
    client.get("/api/facilities?q=合成")
    assert time.time() - t0 < 1.0
    sid = client.get("/api/sections").json()["items"][0]["section_id"]
    t0 = time.time()
    client.get(f"/api/sections/{sid}")
    client.get(f"/api/sections/{sid}/profile")
    assert time.time() - t0 < 1.0


def test_problem_details_for_unknown_api(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
