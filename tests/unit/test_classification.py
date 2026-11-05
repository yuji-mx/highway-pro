"""形状分類（典型形の合成ネットワークで top1 が正解）。"""

from __future__ import annotations

import pytest

from roadmri.classify.engine import classify
from roadmri.classify.rules import evaluate
from roadmri.ingest.synthetic import EXPECTED_SHAPES


@pytest.mark.parametrize(("key", "expected"), sorted(EXPECTED_SHAPES.items()))
def test_typical_shapes_top1(synthetic_db, key, expected):
    _, store, _ = synthetic_db
    r = store.one(
        "SELECT shape_class, shape_confidence, shape_class_2 FROM facility WHERE normalized_name=?", [key]
    )
    assert r is not None, key
    assert r["shape_class"] == expected
    assert 0 < r["shape_confidence"] <= 1


def test_reasons_and_second_candidate(synthetic_db):
    _, store, _ = synthetic_db
    import json

    r = store.one(
        "SELECT classification_reasons, shape_class_2 FROM facility WHERE normalized_name='合成トランペット|IC'"
    )
    reasons = json.loads(r["classification_reasons"])
    assert any(x["satisfied"] and "ループ" in x["text_ja"] for x in reasons)
    assert r["shape_class_2"] is not None


def test_unclassifiable(cfg):
    res = classify(
        {
            "facility_type": "JCT",
            "mainline_count": 1,
            "loop_ramp_count": 0,
            "connection_directions": 1,
            "terminating_routes": 0,
        },
        cfg.classification_rules,
        0.5,
    )
    assert res.shape_class == "分類不能"
    assert res.unclassifiable_reason and "必須条件" in res.unclassifiable_reason


def test_softmax_temperature(cfg):
    f = {
        "facility_type": "IC",
        "mainline_count": 1,
        "loop_ramp_count": 1,
        "general_road_connections": 1,
        "ramp_count": 4,
        "toll_on_general_side": True,
        "has_general_road": True,
    }
    hot = classify(f, cfg.classification_rules, 5.0).confidence
    cold = classify(f, cfg.classification_rules, 0.2).confidence
    assert cold > hot


def test_rule_ops():
    f = {"a": 3, "b": [1, 2], "c": True}
    assert evaluate({"feature": "a", "op": "between", "value": [1, 3]}, f)
    assert evaluate({"feature": "a", "op": "in", "value": [3, 4]}, f)
    assert not evaluate({"feature": "a", "op": "lt", "value": 3}, f)
    assert not evaluate({"feature": "missing", "op": "eq", "value": 1}, f)
    with pytest.raises(ValueError):
        evaluate({"feature": "a", "op": "zz", "value": 1}, f)


def test_rules_version_changes_with_yaml(cfg):
    other = cfg.with_overrides({"classification_rules.version": "rules-9.9.9"})
    assert other.config_hash != cfg.config_hash
