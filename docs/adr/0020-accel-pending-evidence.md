# ADR-0020 加速車線：対象外と判定保留の区別、端点ごとの根拠、航空写真の補正候補（検証専用）

data/validation/accel_regional/REPORT.md（栃木・ミシガンの検証）を受けて、ADR-0016（定義）と ADR-0017（自動値と手動値の分離・履歴・取り消し）を保ったまま、次を決めた。

## 1. 状態

| 自動の判定 | 理由コード | 画面 | 距離 |
|---|---|---|---|
| 測定済み | — | 暫定値（地図データ） | 自動値 |
| 対象外 | `lane_addition` | 対象外・車線追加 | null |
| 対象外 | `auxiliary_to_exit` | 対象外・出口まで続く付加車線 | null |
| 判定保留 | `no_mainline` `lanes_inconsistent` `mainline_ends` `lanes_unknown_downstream` `another_merge` `no_parallel_section` ほか | 判定保留（始点／終点：理由） | null |

- 終点を見失っただけでは車線追加にしない。下流に車線数不明の本線があり車線の戻りを確認できないときは `lanes_unknown_downstream`（判定保留・終点）。以前はこれが `lane_addition` になっていた。
- 判定保留でも OSM の推定値があれば「地図データによる暫定値」として併記する（距離そのものは null）。
- 手動の確認に `pending`（端点ごとの理由：影・橋・画像の範囲外・隣接ランプ・工事・その他）と、`not_accel_lane` の対象外の理由（`exclusion_reason`）を追加。手動値は別の列（`accel_manual_*`）、履歴は manual_edit（追記のみ）、取り消しで元に戻る。
- 検索・統計・難易度は従来どおり自動値（`accel_lane_len_m`）。表示状態は `network/accel_display.py`（Python）と `STATE_SQL`（一覧の SQL）で同じ判定。

## 2. 端点ごとの根拠

`accel_{start,end}_{method,confidence,pending}` を ramp に追加（migration 0004）。method は `osm_parallel`・`osm_merge_node`・`osm_lane_drop`・`manual`。以前の DB は列が空のため画面では「未評価」、また以前は判定保留の理由も status='excluded' で保存していたので、状態は理由コードから導き直す（`status_of`）。CSV は既存の列の並びを変えず、末尾に列を追加。

ランプ詳細の地図：自動の始点 ▲・終点 ■（水色）、手動（橙）、指定中 ◆（桃）。記号と文字（「始点（自動）」など）と凡例で区別し、色だけに頼らない。

## 3. 航空写真による補正候補（検証専用）

`src/roadmri/validation/`。`roadmri accel-photo` で候補を出し、`roadmri accel-eval` で採点する。本番の自動値には採用しない（出力に `adopted: false`）。`roadmri serve` は画像を取得しない。設定は `settings.yaml` の `validation`（config_hash に含めない）。

- 道筋（ランプ＋下流の本線）に沿って航空写真をまっすぐな帯にし、進入側は合流ノード手前のランプの位置（幾何）で決める。決まらないときだけ国の既定（日本は左、米国は右）。
- 日本：始点は現行値、終点は下流の本線の外縁から上流へ連続する白線をたどり、外側に車線幅が現れる最も下流の点（テーパーの傾きで幅 0 まで延ばす）。
- 米国：始点は本線との境の線が実線から破線に変わる点、終点は境の線と外縁の間の幅が消える点（境の線が見えなければ保留）。
- 保留：端点付近が影・画像なし、本線との境が実線（別のランプ・集散路）、外側に平行な線、同程度の候補が離れて複数、画像端（下流へ延ばし、取得元の最大ズーム＝元の解像度で取り直し、それでも見えなければ保留）。
- 画像の取得は `ingest/http.py`（レート制限・キャッシュ・プロキシ）。日本は地理院シームレス空中写真、米国は Esri World Imagery（検証用のみ。出典を出力に記録）。

採点（`validation/accel_eval.py`）は始点・終点・距離の誤差と保留を別々に数え、現行と候補を同じ地点で比べる。端点が同じ向きに 50 m 以上ずれて距離が範囲内なら「相殺」、範囲の上限を大きく超えれば「行き過ぎ」、画像証拠がなく現行値に戻した端点は「後戻り」として示す。REPORT の数値は `tests/fixtures/validation/accel_regional_report_sites.json` にあり、R10（相殺）・P01（行き過ぎ）・P03（後戻り）を回帰テストにした。

## 制約

- 利用者の検証データ（manifest・references・predictions・写真）とスクリプトが使う補助モジュールはリポジトリにないため、検出器はこのリポジトリの部品で組み直した。REPORT の推定値は再現していない（採点は REPORT の数値で行った）。
- 実写での精度は、写真の判読基準がない fixture のランプでは評価できない。合成画像のテストで、真の端点・左右反転・影・隣のランプ・画像端・下流の別の合流を確かめた。
