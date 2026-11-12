"""RangeSpec の幾何化と施設の解決。"""

from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import box

from roadmri.analysis.ranges import RangeError, range_geometry, range_label, resolve_facilities

FAC = pd.DataFrame(
    {
        "facility_id": ["a", "b", "c"],
        "lon": [139.80, 139.85, 140.50],
        "lat": [36.50, 36.60, 36.60],
        "route_refs": [["E4"], ["E4", "E81"], ["E50"]],
        "shape_class": ["トランペット型", "複合型", "Y型"],
        "admin_code": ["09", "09", "08"],
    }
)


def test_geometries():
    assert range_geometry({"type": "bbox", "bbox": [139, 36, 140, 37]}).equals(box(139, 36, 140, 37))
    c = range_geometry({"type": "circle", "circle": {"lon": 139.8, "lat": 36.5, "radius_km": 10}})
    assert c.contains(box(139.79, 36.49, 139.81, 36.51)) and not c.contains(box(140, 36.5, 140.01, 36.51))
    u = range_geometry(
        {
            "type": "union",
            "members": [{"type": "bbox", "bbox": [0, 0, 1, 1]}, {"type": "bbox", "bbox": [2, 2, 3, 3]}],
        }
    )
    assert u.area == pytest.approx(2.0)
    assert range_geometry({"type": "bbox", "bbox": [0, 0, 1, 1], "buffer_km": 11.1}).area > 1.0
    assert range_geometry(
        {"type": "admin", "admin_codes": ["09"], "bbox": [0, 0, 1, 1]}
    ).area == pytest.approx(1.0)
    assert range_geometry({"type": "route", "route_refs": ["E4"]}) is None


def test_polygon_validation():
    ok = {"type": "Polygon", "coordinates": [[[139, 36], [140, 36], [140, 37], [139, 36]]]}
    assert range_geometry({"type": "polygon", "polygon": ok}).area > 0
    with pytest.raises(RangeError):
        range_geometry(
            {
                "type": "polygon",
                "polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
            }
        )
    with pytest.raises(RangeError):
        range_geometry({"type": "polygon", "polygon": {"type": "Point", "coordinates": [0, 0]}})
    with pytest.raises(RangeError):
        range_geometry(
            {
                "type": "polygon",
                "polygon": {"type": "Polygon", "coordinates": [[[0, 0], [400, 0], [400, 1], [0, 0]]]},
            }
        )


def test_resolve():
    assert resolve_facilities({"type": "route", "route_refs": ["E81"]}, FAC) == {"b"}
    assert resolve_facilities({"type": "facilities", "facility_ids": ["a", "zz"]}, FAC) == {"a"}
    assert resolve_facilities({"type": "shape", "shape_classes": ["Y型"]}, FAC) == {"c"}
    assert resolve_facilities({"type": "admin", "admin_codes": ["09"]}, FAC) == {"a", "b"}
    assert resolve_facilities({"type": "bbox", "bbox": [139, 36, 140, 37]}, FAC) == {"a", "b"}
    assert resolve_facilities(
        {
            "type": "union",
            "members": [
                {"type": "route", "route_refs": ["E50"]},
                {"type": "facilities", "facility_ids": ["a"]},
            ],
        },
        FAC,
    ) == {"a", "c"}
    q = {"type": "query", "query": {"target": "ramp", "where": {}}}
    assert resolve_facilities(q, FAC, lambda _q: pd.DataFrame({"facility_id": ["c"]})) == {"c"}
    with pytest.raises(RangeError):
        resolve_facilities(q, FAC)
    with pytest.raises(RangeError):
        resolve_facilities({"type": "nonsense"}, FAC)


def test_labels():
    assert range_label({"type": "route", "route_refs": ["E4", "E50"]}) == "路線 E4・E50"
    assert range_label({"type": "facilities", "facility_ids": ["a", "b"]}) == "個別選択（2 施設）"
    assert range_label({"type": "bbox", "label": "県南"}) == "県南"
