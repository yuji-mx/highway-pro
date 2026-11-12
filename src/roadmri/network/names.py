"""施設名称の正規化（§4.3.4）。規則は config/name_normalization.yaml、別名は config/name_aliases.yaml。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_BRACKETS = re.compile(r"[（(〔\[［【][^）)〕\]］】]*[）)〕\]］】]")
_KATAKANA = re.compile(r"[゠-ヿ]")


@dataclass(frozen=True)
class NormalizedName:
    stem: str
    code: str | None

    @property
    def key(self) -> str:
        return f"{self.stem}|{self.code}" if self.code else self.stem


class NameNormalizer:
    def __init__(self, rules: dict[str, Any], aliases: dict[str, Any] | None = None):
        self.strip_chars: list[str] = [unicodedata.normalize("NFKC", c) for c in rules.get("strip_chars", [])]
        self.strip_brackets = bool(rules.get("strip_brackets", True))
        sfx = [
            (
                unicodedata.normalize("NFKC", str(s["pattern"])).replace(".", "").replace(" ", ""),
                str(s["code"]),
            )
            for s in rules.get("suffixes", [])
        ]
        self.suffixes = sorted(sfx, key=lambda t: -len(t[0]))
        self.aliases: dict[str, str] = dict((aliases or {}).get("aliases", {}) or {})

    def _clean(self, s: str) -> str:
        s = unicodedata.normalize("NFKC", s)
        if self.strip_brackets:
            s = _BRACKETS.sub("", s)
        s = s.replace(" ", "").replace("　", "").replace(".", "")
        return s.strip()

    def _strip_stem(self, s: str) -> str:
        out = []
        for i, ch in enumerate(s):
            if ch == "ー":
                # 長音記号はカタカナに続く場合だけ残す（漢字間の誤記ハイフンは除去）
                prev = s[i - 1] if i > 0 else ""
                if prev and _KATAKANA.match(prev) and prev != "ー":
                    out.append(ch)
                continue
            if ch in self.strip_chars:
                continue
            out.append(ch)
        return "".join(out)

    def normalize(self, name: str | None) -> NormalizedName | None:
        if not name:
            return None
        s = self._clean(str(name))
        code: str | None = None
        for pat, c in self.suffixes:
            if pat and s.upper().endswith(pat.upper()):
                s = s[: len(s) - len(pat)]
                code = c
                break
        stem = self._strip_stem(s)
        nn = NormalizedName(stem, code)
        alias = self.aliases.get(nn.key)
        if alias:
            st, _, cd = alias.partition("|")
            nn = NormalizedName(st, cd or None)
        return nn

    def key(self, name: str | None) -> str | None:
        nn = self.normalize(name)
        return nn.key if nn else None

    @staticmethod
    def normalize_en(name: str | None) -> str | None:
        if not name:
            return None
        s = unicodedata.normalize("NFKC", str(name)).lower()
        return re.sub(r"\s+", " ", s).strip()


def type_from_code(code: str | None) -> str | None:
    return {"IC": "IC", "JCT": "JCT", "SA": "SA", "PA": "PA", "SIC": "SIC", "TB": None}.get(code or "")
