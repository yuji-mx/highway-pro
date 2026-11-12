# ADR-0013 米国対応（P5 の前倒し）

- 状況：MVP は日本（栃木）だけを対象にし、北米は「設計上の考慮のみ」だった。利用者から米国への対応を求められた。
- 決定：
  1. **データ**：OSM は Geofabrik の州別 PBF（`config/regions/michigan.yaml`、`colorado.yaml`）。標高は USGS 3DEP を
     The National Map の 3DEPElevation ImageServer（登録・キー不要）の `exportImage` で、Web メルカトル XYZ タイル
     （z15、北緯 42° で約 3.5 m 格子、float32 GeoTIFF、NAVD88）として取得し、deflate 圧縮してキャッシュする
     （`Usgs3depTileProvider`。格子が地理院タイルと同じなので双一次補間は共通）。欠損は Copernicus GLO-30 で補完。
     レート制限は毎秒 5 タイル・同時 2 接続（`config/settings.yaml`）。
  2. **方向**：米国・カナダは起点基準の上り／下りではなく方位（north / south / east / west。標識の NB/SB/EB/WB）。
     路線番号が奇数なら南北、偶数なら東西（Interstate の番号規則）、番号がなければ路線の広がりで決める
     （`config/routes.yaml` の `direction_mode`）。統計比較で区間を片方向だけ数えるときは路線ごとに south / west を使う。
  3. **施設名**：米国の出口ノードは名称がなく出口番号（`ref`）だけのことが多い。出口番号の基本番号（180A・180B → 180）が
     同じで同一路線・1.5 km 以内のノードを統合し、施設名を「路線＋出口番号（行先）」（例：I-94 Exit 177（State Street））とする。
     JCT は両路線の出口番号を併記する（I-94 Exit 180 / US-23 Exit 35）。さらに、同じランプ群を共有する無名の出口ノードを
     統合する（路線が異なる場合、または出口番号がない場合。同じ路線の異なる出口番号は推移的にも統合しない）。
  4. **路線番号**：`I-94` / `I94` / `I 94` を `I 94` に統一（表示は `I-94`）。`maxspeed` の `70 mph` を km/h に換算して保存する。
  5. **英語名**：Rest Area / Welcome Center → PA、Service Plaza / Travel Plaza / Service Area → SA、Interchange → IC。
  6. **投影**：範囲が 1 つの UTM ゾーンに収まらない場合（ミシガン州は 15〜17 帯）は、重心経度を中央子午線とする横メルカトル
     （k0=0.9996）を使う。§4.2-3 の「ゾーンごとに分割して統合」は、ゾーン境界をまたぐ道路の分断処理が要るため採らなかった。
     ミシガン州の東西端で縮尺誤差は 0.25 % 未満（テストで確認）。
  7. **形状分類**：`classification_rules.yaml` の `country_overrides.US`（CA も同じ）で、料金所を使わないトランペット型、
     部分クローバー型（parclo）とシングルポイント型（SPUI）の加点強化に置き換える（`rules-1.1.0`）。
     ダイバージング・ダイヤモンド（DDI）は交差道路の左右入れ替えの検出が必要なため未対応。
  8. **日米横断**：`roadmri build --range tochigi,michigan` で複数地域を地域ごとに quality 段まで処理し、手動修正の適用と
     公開をまとめて行う（施設 ID は `JP-F-` / `US-F-` で衝突しない）。RangeSpec に `type: country` を追加し、
     統計比較プリセット「日本 vs 米国」を自動で出す。QueryDSL の `country` で国を絞り込める。
  9. **表示単位**：内部・API・出力ファイルは SI のまま。UI のヘッダーで m・km/h ⇄ ft・mph（mi）を切り替える（§5.9）。
- 検証：テスト fixture は OSM API から取得した Ann Arbor, MI（I-94 Exit 175〜181、US-23 との JCT）と 3DEP タイル 26 枚。
  I-94/US-23 はクローバーリーフ型、Exit 177（State St）・181 は部分クローバー型と判定される（いずれも実際の形状どおり）。
  米国の出口番号はマイルポストなので、Exit 177 → 180 の標準距離 4.86 km を 3 マイル（4.83 km）と照合できる（差 0.6 %）。
- 未対応：カナダの DEM（NRCan HRDEM/CDEM）アダプタと州・準州の地域設定、州境界ポリゴンの自動取得、DDI の判定。
