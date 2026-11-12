"""名称正規化（付録 B の表記ゆれ 40 例）。"""

from __future__ import annotations

import pytest

from roadmri.network.names import NameNormalizer


@pytest.fixture(scope="module")
def nn(cfg):
    return NameNormalizer(cfg.name_normalization, cfg.name_aliases)


CASES = [
    ("佐野藤岡IC", "佐野藤岡|IC"),
    ("佐野藤岡ＩＣ", "佐野藤岡|IC"),
    ("佐野藤岡インターチェンジ", "佐野藤岡|IC"),
    ("佐野藤岡 I.C.", "佐野藤岡|IC"),
    ("佐野藤岡I.C", "佐野藤岡|IC"),
    ("　佐野藤岡ＩＣ　", "佐野藤岡|IC"),
    ("栃木都賀JCT", "栃木都賀|JCT"),
    ("栃木都賀ジャンクション", "栃木都賀|JCT"),
    ("栃木都賀 J.C.T.", "栃木都賀|JCT"),
    ("栃木都賀ＪＣＴ", "栃木都賀|JCT"),
    ("上河内SA", "上河内|SA"),
    ("上河内サービスエリア", "上河内|SA"),
    ("上河内ＳＡ", "上河内|SA"),
    ("上河内スマートIC", "上河内|SIC"),
    ("上河内スマートインターチェンジ", "上河内|SIC"),
    ("上河内スマートＩＣ", "上河内|SIC"),
    ("宇都宮IC（上り）", "宇都宮|IC"),
    ("宇都宮IC(下り)", "宇都宮|IC"),
    ("宇都宮ＩＣ（上り線）", "宇都宮|IC"),
    ("西那須野塩原IC", "西那須野塩原|IC"),
    ("西那須野ｰ塩原IC", "西那須野塩原|IC"),
    ("西那須野-塩原IC", "西那須野塩原|IC"),
    ("西那須野―塩原IC", "西那須野塩原|IC"),
    ("西那須野・塩原IC", "西那須野塩原|IC"),
    ("西那須野IC", "西那須野塩原|IC"),
    ("大谷PA", "大谷|PA"),
    ("大谷パーキングエリア", "大谷|PA"),
    ("大谷PA (下り)", "大谷|PA"),
    ("大谷ＰＡ（上り）", "大谷|PA"),
    ("那須IC", "那須|IC"),
    ("那須インター チェンジ", "那須|IC"),
    ("鹿沼IC", "鹿沼|IC"),
    ("鹿沼ＩＣ", "鹿沼|IC"),
    ("岩舟JCT", "岩舟|JCT"),
    ("岩舟ジャンクション", "岩舟|JCT"),
    ("ﾊｰﾍﾞｽﾄ PA", "ハーベスト|PA"),
    ("矢板北スマートIC", "矢板北|SIC"),
    ("都賀西方PA", "都賀西方|PA"),
    ("佐野SA", "佐野|SA"),
    ("宇都宮上三川IC", "宇都宮上三川|IC"),
]


def test_has_40_cases():
    assert len(CASES) == 40


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalize(nn, raw, expected):
    assert nn.key(raw) == expected


def test_english_name(nn):
    assert nn.normalize_en("Sano-Fujioka  IC") == "sano-fujioka ic"
    assert nn.normalize_en("Sano-Fujioka IC") == "sano-fujioka ic"


def test_empty(nn):
    assert nn.key(None) is None and nn.key("") is None
    assert nn.normalize("名称のみ").code is None
