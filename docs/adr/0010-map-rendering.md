# ADR-0010 地図表示（MapLibre・FlatGeobuf・ラベル）

- 表示用ベクタは run ごとに FlatGeobuf（`data/tiles/*.fgb`）を生成し、`/api/layers/{name}.fgb` で配信する。
  bbox 指定時は DuckDB の `display_feature` から切り出して返す。本線は 100 m、ランプは約 20 m ごとに分割し、
  勾配・標高・曲率などの色分け用の値を属性に持たせる。
- MapLibre の文字描画にはグリフ（PBF フォント）が必要で、オフラインでは CDN から取れない。施設ラベルと品質警告の
  三角記号は HTML マーカーで描き、同梱の Noto Sans JP を使う（ズーム 11 未満は JCT だけ）。
- 料金所の「5 px の四角」は circle レイヤー（半径 2.5 px）で近似した。発光は線レイヤーを 3 層重ねて blur で近似（§13.3）。
