# 米国の検証記録

## 同梱 fixture（Ann Arbor, MI。2026-11-08 取得の OSM・USGS 3DEP）— 実施済み

`tests/integration/test_pipeline.py::test_us_fixture_golden` で毎回確認している。

| 項目 | 結果 |
|---|---|
| 施設 | I-94 Exit 177（State Street）：部分クローバー型、I-94 Exit 180 / US-23 Exit 35：JCT・クローバーリーフ型（I-94 と US-23 の出口ノード 11 個を統合）、I-94 Exit 181（US-12 / Saline）：部分クローバー型、I-94 Exit 175：fixture の西端で切れているためハーフIC 扱い |
| ランプ | 40 本。分岐はすべて右側（右側通行）、制限速度 70 mph → 113 km/h |
| 方向 | I-94 は東行き・西行き（偶数番号）、US-23 は北行き・南行き（奇数番号） |
| 区間距離 | Exit 177 → 180：4.86 km（東行き）／ 4.91 km（西行き）。出口番号（マイルポスト）差 3 マイル＝4.83 km に対し +0.6〜1.7 % |
| DEM | USGS 3DEP（z15、NAVD88）。欠損率 0.08 % |

## 州全域（ミシガン州など）— 未実施

開発環境から Geofabrik に接続できなかったため未実施。手順：

```bash
uv run roadmri fetch --range michigan
uv run roadmri build --range michigan
```

照合の目安：出口番号＝マイルポストなので、同一路線の隣接出口の距離（マイル差 × 1.609 km）と標準距離を比べられる
（出口番号は四捨五入されたマイル値なので ±0.5 マイル程度の差はありうる）。
