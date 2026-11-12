"""facility_id の一意化（同名の施設が geohash7 の同じセルにある場合を含む）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from roadmri.network.junctions import dedupe_facility_ids, geohash


def _fac(fid: str, lon: float, lat: float, osm_id: int) -> Any:
    return SimpleNamespace(
        facility_id=fid, lon=lon, lat=lat, name="Rest Area", members=[SimpleNamespace(osm_id=osm_id)]
    )


def test_same_cell_collision_gets_unique_deterministic_ids() -> None:
    # 約 20 m 離れた同名の 2 施設（同じ geohash7 セル）と、離れた場所の同名施設、別名の施設
    a = _fac("US-F-aaaa", -84.50000, 42.70000, 200)
    b = _fac("US-F-aaaa", -84.50020, 42.70005, 100)
    c = _fac("US-F-aaaa", -85.10000, 43.10000, 300)
    d = _fac("US-F-bbbb", -84.50000, 42.70000, 400)
    assert geohash(a.lon, a.lat) == geohash(b.lon, b.lat)
    facs = [a, b, c, d]
    dedupe_facility_ids(facs)
    ids = [f.facility_id for f in facs]
    assert len(set(ids)) == len(ids)
    cell = geohash(a.lon, a.lat)
    assert c.facility_id == f"US-F-aaaa-{geohash(c.lon, c.lat)}"
    assert d.facility_id == "US-F-bbbb"  # 衝突のない施設は変わらない
    assert {a.facility_id, b.facility_id} == {f"US-F-aaaa-{cell}", f"US-F-aaaa-{cell}-2"}
    # 入力の順序によらず同じ ID になる
    a2, b2 = _fac("US-F-aaaa", a.lon, a.lat, 200), _fac("US-F-aaaa", b.lon, b.lat, 100)
    dedupe_facility_ids([b2, a2, _fac("US-F-aaaa", c.lon, c.lat, 300)])
    assert (a2.facility_id, b2.facility_id) == (a.facility_id, b.facility_id)


def test_suffix_does_not_clash_with_existing_id() -> None:
    a = _fac("X", 0.0, 0.0, 1)
    b = _fac("X", 0.0, 0.0, 2)
    cell = geohash(0.0, 0.0)
    existing = _fac(f"X-{cell}-2", 10.0, 10.0, 3)  # 既に -2 付きの ID がある場合
    facs = [a, b, existing]
    dedupe_facility_ids(facs)
    ids = [f.facility_id for f in facs]
    assert len(set(ids)) == 3 and existing.facility_id == f"X-{cell}-2"
