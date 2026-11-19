# 加速車線の航空写真による補正候補（検証用の手順）

本番の値は変わらない。ADR-0020 を参照。

```bash
uv run roadmri accel-photo --ramps R10=US-F-xxxx-RAMP-001,JP-F-yyyy-RAMP-003   # data/validation/accel_photo/<run_id>.json
uv run roadmri accel-photo --manifest data/validation/accel_regional/manifest.json --offline   # 保存済みの写真だけ
uv run roadmri accel-eval --study data/validation/accel_regional --method width --check-db
uv run roadmri accel-eval --sites tests/fixtures/validation/accel_regional_report_sites.json
uv run roadmri accel-eval --photo data/validation/accel_photo/<run_id>.json      # 同じフォルダの references.json と比べる
```

出力には run_id と provenance（設定・入力・git commit・`adopted_to_production: false`）を付ける。
`accel-eval` は scores.json と REPORT.md（地点ごとの改善・悪化・保留、相殺・行き過ぎ・後戻りの注記、端点ごと・距離の平均絶対誤差）を出す。

## REPORT の数値での採点（両側追跡、`--sites` の結果）

| 地点 | 現行の誤差（始点／終点／距離） | 候補の誤差 | 距離 | 注記 |
|---|---|---|---|---|
| R06 | −96／−252／−156 | −46／+6／+52 | 改善 | |
| R10 | −117／−342／−224 | −78／−84／−6 | 改善 | 相殺：距離 288 m は 267〜320 m に入るが、始点・終点とも約 80 m 手前 |
| P01 | —／—／−224 | —／—／+226 | 悪化 | 上限を 200 m 超える（下流の別の位置を終点に） |
| P02 | −101 | −34 | 改善 | |
| P03 | −425 | −405 | 改善（小） | 終点の証拠が足りず現行値に戻した・下限より 366 m 短い |
| P04 | −203 | +25 | 改善 | |
| P05 | −4 | +84 | 悪化 | 上限を 58 m 超える |
| P06 | −235 | +24 | 改善 | |
| R08・R11・R13・R14 | 非合流 | — | 距離を出さない（正） | |

P01〜P06：改善 4・悪化 2、判読中点から 50 m 以内は現行 1/6 → 候補 3/6（REPORT と一致）。
