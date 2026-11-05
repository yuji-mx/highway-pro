# 栃木県＋周辺の検証記録

## 1. 同梱 fixture（宇都宮IC〜鹿沼IC、2026-11-05 取得の OSM・地理院 DEM）— 実施済み

`tests/integration/test_pipeline.py::test_tochigi_small_golden` で毎回確認している。

| 項目 | 結果 |
|---|---|
| 施設 | 鹿沼IC（トランペット型 1.00）、大谷PA（SA/PA）、宇都宮IC（複合型 0.98、E4＋E81〈日光宇都宮道路〉の IC＋JCT 複合）。上下線の出口ノード（鹿沼IC 2、宇都宮IC 3、大谷PA 2＋休憩施設ポリゴン 2）はそれぞれ 1 施設に統合 |
| 取りこぼし・重複 | なし（道の駅うつのみや ろまんちっく村〈highway=services〉は本線に接続しないため施設から除外：ADR-0007） |
| ランプ | 35 本（鹿沼IC 12、宇都宮IC 19、大谷PA 4） |
| 区間距離（standard） | 鹿沼IC→大谷PA 8.03 km（下り）／ 7.90 km（上り）、大谷PA→宇都宮IC 3.62 km（下り）／ 3.30 km（上り）。鹿沼IC→宇都宮IC は下り 11.65 km・上り 11.20 km |
| DEM | 地理院 DEM5A（不足部は DEM10B）。標高付与率 > 99 % |
| 本線勾配 > 7 % | なし |

区間距離の NEXCO キロポストとの照合（±3 %、中央値 ±1 %）は、NEXCO 東日本の公開値を
`tochigi_reference.json` の `distances_km` に登録してから `pytest -m network` で実施する（未実施）。

## 2. 栃木県全域（§14.3）— 未実施

開発環境から Geofabrik・Overpass に接続できなかったため、県全域の PBF による検証は未実施。受け入れ時の手順：

```bash
uv run roadmri fetch --range tochigi
uv run roadmri build --range tochigi
uv run pytest -m network tests/integration/test_realdata.py
```

確認項目：東北道（佐野藤岡IC〜那須IC）・北関東道（県内区間）・日光宇都宮道路の全施設の抽出（`tochigi_reference.json` の
facilities は主要施設のみ。NEXCO の施設一覧で補完すること）、区間距離、形状分類精度（`scripts/label_tool.py` で 30 施設以上に
正解ラベル）、本線 100 m 窓勾配 > 7 % の説明可能性、仕様書の代表クエリ上位 5 件の航空写真確認。
