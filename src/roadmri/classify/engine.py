"""ルールベース分類エンジン（§4.9.4）。必須条件＋加点条件のスコアをソフトマックスで信頼度に変換する。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from roadmri.classify.rules import describe, evaluate


@dataclass
class ClassResult:
    shape_class: str
    confidence: float
    second: str | None
    second_confidence: float | None
    reasons: list[dict[str, Any]] = field(default_factory=list)
    unclassifiable_reason: str | None = None
    scores: dict[str, float] = field(default_factory=dict)


def classify(
    features: dict[str, Any], rules: dict[str, Any], temperature: float, country: str = "JP"
) -> ClassResult:
    ftype = features.get("facility_type")
    override = (rules.get("country_overrides") or {}).get(country) or {}
    disabled = set(override.get("disable", []) or []) if isinstance(override, dict) else set()
    classes = [c for c in rules.get("classes", []) if c.get("id") not in disabled]
    classes += override.get("classes", []) if isinstance(override, dict) else []
    penalty = float(rules.get("required_penalty", 3.0))
    cands: list[tuple[str, float, bool, list[dict[str, Any]]]] = []
    for c in classes:
        if ftype not in c.get("applies_to", []):
            continue
        reasons: list[dict[str, Any]] = []
        ok = True
        unmet = 0
        for r in c.get("required", []):
            sat = evaluate(r, features)
            reasons.append({"kind": "required", "satisfied": sat, "text_ja": describe(r, features)})
            if not sat:
                ok = False
                unmet += 1
        score = 0.0
        for r in c.get("score", []):
            sat = evaluate(r, features)
            reasons.append(
                {
                    "kind": "score",
                    "satisfied": sat,
                    "weight": r.get("weight", 1.0),
                    "text_ja": r.get("reason_ja") or describe(r, features),
                }
            )
            if sat:
                score += float(r.get("weight", 1.0))
        cands.append((c["label"], score - penalty * unmet, ok, reasons))
    if not cands:
        fb = rules.get("fallback", {})
        return ClassResult(
            fb.get("label", "分類不能"), 0.0, None, None, [], f"種別 {ftype} に適用できる分類がありません"
        )
    # 同じラベル（IC と JCT のトランペット型など）は高い方を残す
    best_by_label: dict[str, tuple[str, float, bool, list[dict[str, Any]]]] = {}
    for c in cands:
        if c[0] not in best_by_label or (c[2], c[1]) > (best_by_label[c[0]][2], best_by_label[c[0]][1]):
            best_by_label[c[0]] = c
    cands = sorted(best_by_label.values(), key=lambda t: (-int(t[2]), -t[1], t[0]))
    mx = max(s for _, s, _, _ in cands)
    exps = [math.exp((s - mx) / temperature) for _, s, _, _ in cands]
    tot = sum(exps)
    probs = {lab: e / tot for (lab, _, _, _), e in zip(cands, exps, strict=True)}
    top = cands[0]
    order = sorted(cands, key=lambda t: (-int(t[2]), -probs[t[0]], t[0]))
    top = order[0]
    second = order[1] if len(order) > 1 else None
    scores = {lab: round(s, 3) for lab, s, _, _ in cands}
    if not top[2]:
        fb = rules.get("fallback", {})
        unmet_txt = "、".join(r["text_ja"] for r in top[3] if r["kind"] == "required" and not r["satisfied"])
        reason = str(fb.get("reason_template_ja", "{unmet}")).format(unmet=unmet_txt)
        return ClassResult(fb.get("label", "分類不能"), 0.0, top[0], probs[top[0]], top[3], reason, scores)
    return ClassResult(
        top[0],
        probs[top[0]],
        second[0] if second else None,
        probs[second[0]] if second else None,
        top[3],
        None,
        scores,
    )
