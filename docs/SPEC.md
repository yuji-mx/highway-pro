# ROAD MRI 実装仕様書（v1.0）

高速道路ネットワーク解析・可視化システム ― 完成版開発のための実装仕様

| 項目 | 内容 |
|---|---|
| 文書ID | ROADMRI-SPEC-001 |
| 版 | 1.0（2026-11-05） |
| 対象読者 | 実装担当（Claude Code）、レビュー担当（駆動系開発DX推進） |
| 前提資料 | 要求仕様案（高速道路ネットワーク解析・可視化システム 要求仕様案）、UIモックアップ `road_mri_mockup.html` v2 |
| 位置づけ | 本書は要求仕様案とモックアップでの検討結果を統合し、実装・テスト・受け入れの基準とする。本書と要求仕様案が矛盾する場合は本書を優先する。 |

---

## 0. Claude Code への作業指示（最初に読むこと）

1. 本書をリポジトリの `docs/SPEC.md` に配置し、§16 のマイルストーン順に実装する。各マイルストーンの完了条件（§15）を満たしてから次へ進む。
2. UI の見た目と操作の正解は `docs/mockup/road_mri_mockup.html` である。**モックアップ内の数値・座標・分類結果はすべてダミー**であり、実装へ流用してはならない。ロジック（距離・曲率・勾配・分類・統計）は本書の定義に従って新規実装する。
3. テストは常にオフラインで実行できること。ネットワーク取得を伴うテストは `@pytest.mark.network` を付け、既定では実行しない。テスト用の小規模 OSM 抽出・DEM タイルは `tests/fixtures/` に同梱する。
4. 公開サーバー（Overpass API、地理院タイル等）への大量アクセスをしない。§5.6 のレート制限とキャッシュを必ず通す。
5. 本書で「設定値」と書いた数値はハードコードせず、`config/*.yaml` から読む。
6. 判断に迷う点は §17「未決事項と暫定決定」の暫定決定で進め、決定を `docs/adr/` に ADR として記録する。
7. `CLAUDE.md` には §16.3 の開発規約を転記する。

---

## 1. プロジェクト概要

### 1.1 目的

日本および北米の道路網について、無償で取得できる公開データ（OpenStreetMap、公的DEM）を統合し、高速道路・IC・JCT・ランプを抽出してデータベース化する。そのうえで以下を実現する。

- 名称、位置、接続関係、形状分類、道路距離、ランプ長、曲率、標高、勾配の算出と蓄積
- 「MRIの血管画像」のような背景地図なしの軽量道路ネットワーク表示
- 条件検索、類似地点検索、2地点比較、範囲間の統計比較
- データ・画像・レポートの出力

### 1.2 最終価値

地図を眺めることではなく、車両性能検討の候補地点抽出を成立させること。代表クエリは次のとおり。

> 関東または北米で、ランプ長 500 m 以上、最小曲率半径 100 m 以下、100 m 平均上り勾配 5 % 以上、標高データ信頼度が中以上の流入ランプを抽出する。

加えて、モックアップ検討で追加された次の問いに答えられること。

> 範囲A（例：東北道沿い／関東）と範囲B（例：北関東道沿い／北米ミシガン州）で、ランプの曲率・勾配・高低差・形状構成に統計的な差はあるか。

### 1.3 開発フェーズ

| フェーズ | 内容 | 本書でのスコープ |
|---|---|---|
| P1 成立性確認 | 栃木県周辺で OSM 取得、IC/JCT 一覧、重複統合、ランプ抽出、区間距離、MRI 表示、CSV・画像出力 | **MVP必須** |
| P2 車両性能向け解析 | DEM 接合、標高プロファイル、勾配、曲率、条件検索、品質判定 | **MVP必須** |
| P3 形状分類 | 特徴量生成、ルールベース分類、信頼度、手動修正、修正蓄積 | **MVP必須** |
| P3+ 比較・統計 | 類似地点検索（ランプ・IC/JCT）、2地点比較、範囲統計比較 | **MVP必須**（モックアップ v2 で追加） |
| P4 全国展開 | PBF 一括処理、地域別キャッシュ、差分更新、全国検索、PMTiles | 設計上の考慮のみ・実装は後続 |
| P5 北米展開 | 北米 OSM、USGS 等 DEM、州別処理、日米横断検索 | 設計上の考慮のみ |
| P6 実走データ統合 | GPS/CAN/MF4、マップマッチング、類似試験路検索 | インターフェース定義のみ |

**MVP の定義**：栃木県＋周辺（緩衝 10 km、群馬・茨城・福島の県境部を含む）を対象に、P1〜P3+ の全機能が本書の完成条件（§15.1）を満たすこと。ただしアーキテクチャは P4〜P6 へ拡張可能であること（§15.3）。

---

## 2. 用語定義

| 用語 | 定義 |
|---|---|
| 施設（facility） | IC、JCT、スマートIC、SA、PA、料金所など、本線に出入口を持つ構造の総称。上下線の複数 OSM ノードを統合した論理単位。 |
| 本線（mainline） | `highway=motorway`（および設定により高規格の `trunk`）の Way 群。上下線が別 Way の場合は carriageway として方向別に扱う。 |
| 論理ランプ（ramp） | 連続する `*_link` Way を、分岐・合流点の間で 1 本に統合したもの。 |
| 区間（section） | 同一路線上で隣接する 2 施設間の、方向別の本線経路。 |
| 代表点（representative point） | 施設ごと・方向別に定める本線上の基準点（§4.5.3）。 |
| サンプル点（sample point） | 道路中心線を等間隔に再サンプリングした点。標高・勾配・曲率を保持する。 |
| 窓平均勾配 | 走行距離 w の窓における標高差 ÷ w。w = 10/50/100/500/1000 m。 |
| 範囲（range） | 統計比較や検索の対象集合の定義（路線、地域、ポリゴン、形状、検索結果、個別選択など）。 |
| 難易度候補値 | 候補抽出用の複合指標（0〜100）。絶対評価として使用しない。 |
| 品質レベル | 高／中／低／使用不可の4段階（§4.10）。 |
| 実行（run） | パイプライン 1 回分の処理。すべての出力は run_id と来歴（provenance）を持つ。 |

---

## 3. 要求仕様（要求一覧）

優先度：**M** = MVP 必須、**L** = 後続フェーズ（設計のみ考慮）。各要求の詳細は §4 の対応節を参照する。

### 3.1 データ取得・管理

| ID | 要求 | 優先 | 詳細 |
|---|---|---|---|
| REQ-D01 | OSM から高速道路・ランプ・接続一般道・付帯施設を取得する | M | §4.1 |
| REQ-D02 | 規模に応じて Overpass（小規模試験）と PBF ローカル処理を切り替える | M | §4.1.2 |
| REQ-D03 | 地域ごとに DEM を取得し、メタデータ（名称、解像度、座標系、標高基準、取得日、推定誤差、欠損）を記録する | M | §4.7.1 |
| REQ-D04 | 地域別キャッシュと取得日の記録を行う | M | §4.1.4 |
| REQ-D05 | OSM 差分による更新と、IC/JCT の新設・廃止・名称変更候補の検出を行う | L（データ構造は M） | §4.18 |
| REQ-D06 | 原則としてユーザー登録、APIキー、従量課金を必要としない | M | §13.1 |
| REQ-D07 | 取得後はオフラインで全機能が動作する | M | §5.2 |

### 3.2 抽出・解析

| ID | 要求 | 優先 | 詳細 |
|---|---|---|---|
| REQ-A01 | `highway=motorway_junction` を中心に IC/JCT を抽出する | M | §4.3.1 |
| REQ-A02 | 接続ネットワークから IC/JCT/スマートIC/施設ランプ/ハーフIC を判定する | M | §4.3.2 |
| REQ-A03 | 上下線の重複ノードを統合し、元 OSM オブジェクトとの対応を保持する | M | §4.3.3 |
| REQ-A04 | 名称の表記ゆれを正規化する | M | §4.3.4 |
| REQ-A05 | 連続する link Way を論理ランプに統合し、一意 ID を付与する | M | §4.4 |
| REQ-A06 | ランプの方向（流入／流出／JCT連絡／集散路、上下線、左右分岐など）を分類する | M | §4.4.3 |
| REQ-A07 | IC/JCT 間の道路距離を、方向別・定義別に算出する | M | §4.5 |
| REQ-A08 | 経路異常（経路なし、一方通行違反、一般道迂回、本線切断など）を検出する | M | §4.5.5 |
| REQ-A09 | 道路中心線を等間隔に補間し、DEM 標高を付与する | M | §4.7 |
| REQ-A10 | 標高の平滑化方式を選択できる。元標高と平滑化標高の両方を保存する | M | §4.7.3 |
| REQ-A11 | 構造物（橋梁・高架・トンネル等）を識別して低信頼フラグを付け、端点補間による補正を行う | M | §4.7.4 |
| REQ-A12 | 勾配指標（瞬間、窓平均、最大上り・下り、累積、連続距離、標高差、最高・最低）を算出する | M | §4.8 |
| REQ-A13 | 曲率・線形指標を算出する | M | §4.6 |
| REQ-A14 | 車両性能向け複合指標と難易度候補値を算出する | M | §4.15 |
| REQ-A15 | IC/JCT の形状分類を推定し、信頼度・第2候補・根拠・分類不能理由を出力する | M | §4.9 |
| REQ-A16 | ルート解析（起終点指定、通過施設、区間別距離、プロファイル、候補経路比較）を行う | M | §4.16 |

### 3.3 検索・比較・統計

| ID | 要求 | 優先 | 詳細 |
|---|---|---|---|
| REQ-S01 | 名称、路線、出口番号、行政区域、座標、中心点からの距離で基本検索できる | M | §4.11 |
| REQ-S02 | 形状分類と分類信頼度の下限で絞り込める | M | §4.11 |
| REQ-S03 | 数値条件の単独または組合せ（AND/OR）で検索できる。対象はランプ・IC/JCT・区間 | M | §4.11 |
| REQ-S04 | 目的別プリセット（登坂、加速合流、旋回・駆動力、回生、熱負荷、高標高、連続勾配、小R）を使える | M | §4.15.3 |
| REQ-S05 | 選択したランプに類似するランプを検索できる | M | §4.12.1 |
| REQ-S06 | 選択した IC/JCT に構造が類似する IC/JCT を検索できる（ランプ詳細・IC/JCT 詳細・条件検索から起動） | M | §4.12.2 |
| REQ-S07 | 条件検索画面の中で 2 つの IC/JCT を比較対象に登録し、並べて比較できる | M | §4.13 |
| REQ-S08 | 2 つの範囲を定義し、範囲内の路線・IC/JCT・ランプ・区間を統計的に比較できる | M | §4.14 |
| REQ-S09 | 検索結果を統計比較の範囲として直接設定できる | M | §4.14.2 |
| REQ-S10 | 日本・北米を横断して検索できる | L | §4.11.6 |

### 3.4 表示・出力・運用

| ID | 要求 | 優先 | 詳細 |
|---|---|---|---|
| REQ-V01 | 背景地図なしの「道路MRI」表示（黒背景、発光表現） | M | §11.4 |
| REQ-V02 | レイヤー切替（本線、ランプ、一般道、IC、JCT、SA・PA、料金所、橋梁、トンネル、解析対象、品質警告、ラベル） | M | §11.4 |
| REQ-V03 | 色分け指標の選択（道路種別、勾配、標高、曲率半径、制限速度、車線数、ランプ種別、IC/JCT分類、データ信頼度、実走データ有無） | M（実走データ有無は L） | §11.4 |
| REQ-V04 | クリックで施設・ランプ情報を表示する | M | §11.4 |
| REQ-V05 | 標高・勾配・曲率プロファイルを表示し、地図とカーソル位置を連動させる | M | §11.6, §11.7 |
| REQ-V06 | PNG/JPEG/SVG の静止画像を出力する（サイズ、縮尺、背景色、線幅を指定）。一括生成にも対応する | M | §4.17.2 |
| REQ-V07 | CSV、Excel、GeoJSON、GeoPackage、Parquet、SQLite を出力する（PostGIS は L） | M | §4.17.1 |
| REQ-V08 | 画像レポートを自動生成する | M | §4.17.3 |
| REQ-V09 | 手動修正と修正履歴を管理し、再計算後も保持する | M | §4.19 |
| REQ-V10 | 低信頼施設だけを確認対象にする人手確認ワークフロー | M | §4.19.3 |
| REQ-V11 | 公式情報（NEXCO 等）との照合 | L | §4.20 |
| REQ-V12 | 実走データ（GPS/CAN/MF4）との照合 | L | §4.21 |
| REQ-V13 | すべての解析結果に再現性情報を記録する | M | §4.22 |

---

## 4. 機能仕様

### 4.1 OSM データ取得

#### 4.1.1 取得対象タグ

| 分類 | タグ |
|---|---|
| 本線 | `highway=motorway`、`highway=trunk`（`motorroad=yes` または設定で指定した ref のみ） |
| ランプ | `highway=motorway_link`、`highway=trunk_link` |
| 出口ノード | `highway=motorway_junction`（`name`、`name:en`、`ref`、`exit_to` 系） |
| 接続一般道 | `primary`、`secondary`、`tertiary`（施設周辺 2 km に限定、設定可）。表示用には `residential` までの簡略版を別レイヤーで保持 |
| 属性 | `name`、`name:ja`、`name:en`、`ref`、`int_ref`、`oneway`、`lanes`、`maxspeed`、`destination`、`destination:ref`、`destination:street`、`junction:ref`、`bridge`、`tunnel`、`layer`、`covered`、`cutting`、`embankment`、`toll` |
| 付帯施設 | `barrier=toll_booth`、`highway=services`（SA）、`highway=rest_area`（PA）、`highway=motorway_junction` かつ名称に「スマート」、`access=*` と `motorcar:conditional` の ETC 条件 |
| リレーション | `type=route` + `route=road`（路線名と ref の補完） |

#### 4.1.2 取得方式の切替

| 規模 | 方式 | 用途 |
|---|---|---|
| 試験（半径 30 km 以下） | Overpass API（タイムアウト 180 s、1 リクエスト／10 s 以上の間隔） | 開発時の検証のみ。CLI オプション `--source overpass` |
| 都道府県〜全国 | Geofabrik 地域 PBF（例 `asia/japan/kanto-latest.osm.pbf`）→ `osmium extract` で範囲抽出 → pyosmium で読込 | **既定** |
| 北米 | Geofabrik `north-america/us/<state>`、`north-america/canada/<province>` | P5 |

- 範囲抽出のポリゴンは、指定範囲に緩衝距離（既定 10 km、設定値）を加えたもの。緩衝内の施設は「範囲外参照」として区間の端点にだけ使い、一覧の既定表示からは除外する。
- 公開タイルサーバーと Overpass への過剰負荷を避ける（§5.6）。

#### 4.1.3 地域指定方法

`RangeSpec`（§9.11）として統一的に扱う。

| 種類 | 入力 | 実装 |
|---|---|---|
| 都道府県・市区町村 | JIS 都道府県コード／市区町村コード | OSM の `boundary=administrative`（admin_level 4/7）ポリゴン。オフライン用に P1 で抽出して `data/boundaries/` に保存 |
| 州・郡 | FIPS コード | OSM 境界（P5） |
| 矩形 | 北西・南東の緯度経度 | — |
| 中心＋半径 | 緯度経度、半径 km | 測地線距離によるバッファ |
| 路線名 | 路線名または ref | ルートリレーションの Way 集合＋緩衝 |
| IC/JCT 名 | 施設名＋半径 | 施設代表点のバッファ |
| GeoJSON ポリゴン | Polygon / MultiPolygon | 検証（自己交差なし、WGS84） |
| 地図上の選択 | UI で矩形またはポリゴンを描画 | GeoJSON として送信 |

#### 4.1.4 キャッシュと取得記録

- 生データは `data/raw/osm/<source>/<yyyymmdd>/` に保存し、ファイルの sha256、取得 URL、取得日時、OSM のレプリケーション timestamp と sequence を `dataset_source` テーブルに記録する。
- 同じ範囲・同じソース日付での再取得はスキップする（`--force` で強制）。

### 4.2 道路ネットワーク構築

1. 本線・ランプ・接続一般道の Way を読み込み、ノード共有でトポロジーを構築する（有向グラフ。`oneway` が未指定の motorway と link は一方通行として扱う。`oneway=-1` は向きを反転する）。
2. グラフは networkx の `MultiDiGraph` とする。エッジには `way_id`、`highway`、`length_m`、属性、geometry を持たせる。P4 での全国規模を見据え、グラフ操作は `network/graph.py` のインターフェース越しに行い、rustworkx への置換を可能にする。
3. 投影座標系：解析は対象範囲の重心経度から決めた UTM（WGS84 / UTM zone、EPSG:326xx）で行う。日本も北米も同じロジックとする。範囲が 2 ゾーンにまたがる場合は、ゾーンごとに分割処理して結果を WGS84 で統合する。
4. 本線 carriageway の識別：同一 ref・同一名称の motorway Way を、向きの連続性で連結した有向チェーンとする。上下線（`direction` = up/down）は路線の起点側定義（`config/routes.yaml` の `origin` 座標）に近づく向きを「上り」とする。環状路線は inner/outer とする。

### 4.3 IC・JCT 抽出

#### 4.3.1 基本抽出

`highway=motorway_junction` ノードから次を取得する：OSM ID、名称（ja/en）、出口番号（`ref`/`junction:ref`）、緯度経度、所属本線 Way と路線、接続ランプ、方面・行先（下流ランプの `destination*`）、上下線、取得元タグ一式、OSM timestamp。

`highway=services` / `rest_area` と、名称に SA/PA/スマートIC を含むものも施設候補とする。

#### 4.3.2 種別判定（名称だけに頼らない）

接続ネットワークから判定し、名称と矛盾した場合は `quality_flags` に `TYPE_NAME_MISMATCH` を立てる。

| 判定 | 条件（ネットワーク） |
|---|---|
| IC | 施設に属するランプ群が、本線と本線以外の道路（primary 以下、または trunk でも motorroad でないもの）を接続する |
| JCT | ランプ群が異なる ref の本線同士だけを接続する |
| IC＋JCT 複合 | 上記の両方を満たす（例：宇都宮IC と日光宇都宮道路の接続）。`facility_type=IC` とし、`is_jct_combined=true` |
| スマートIC | ランプに ETC 専用条件（`access`、`motorcar:conditional`、名称「スマート」）がある。SA/PA 併設型は `parent_facility_id` で SA/PA に紐付ける |
| SA/PA（施設ランプ） | ランプの先が `highway=services/rest_area` 領域で終わり、本線に戻る |
| ハーフIC | 流入・流出が上下線のどちらか片側、または一方向にしか存在しない（`is_half_ic=true`） |
| 料金所 | `barrier=toll_booth` を施設の付属物として保持（`toll_plaza` 点） |

#### 4.3.3 重複統合

上下線ごとに存在する複数の junction ノードを、1 施設に統合する。

1. 候補ペア：正規化名称が一致し、代表点間距離が `merge.max_distance_m`（既定 2,000 m、JCT は 3,000 m）以内のもの。
2. 名称がない場合：出口番号が一致し、同一路線で、距離が 1,000 m 以内のもの。
3. 補強条件：ランプ接続関係を共有する（同じ一般道ノード・料金所に到達する）、上下線の対応が取れている。
4. 統合後の代表点は、統合したノード群と料金所の重心を本線に投影した点とする。
5. 元 OSM ノード ID をすべて `facility_osm_map` に保持する。
6. 統合判定の根拠（どの条件で統合したか）を `merge_reason` に記録する。

#### 4.3.4 名称正規化

`normalize_name()` は次の順に適用する。規則は `config/name_normalization.yaml` に置く。

1. Unicode NFKC 正規化（全角英数・半角カナの統一）
2. 前後の空白、全角空白、ハイフン類（‐-–—―ｰ）、中黒、括弧書き（例「（上り）」）の除去
3. 接尾辞の統一：`インターチェンジ`、`I.C.`、`I.C`、`ＩＣ`、`IC` → 種別 `IC`。`ジャンクション`、`JCT`、`J.C.T.` → `JCT`。`サービスエリア` → `SA`。`パーキングエリア` → `PA`。`スマートインターチェンジ`、`スマートIC` → `SIC`
4. 正規化名称＝接尾辞を除いた語幹＋種別コード（例：`佐野藤岡|IC`）
5. 英語名（`name:en`）は小文字化・空白統一して別フィールドに保持
6. 旧名称・別名は `config/name_aliases.yaml`（手動管理）で同一視し、`name_variants` に全表記を保持する

### 4.4 ランプ抽出

#### 4.4.1 論理ランプ化

1. 施設ごとに、施設代表点から `ramp.search_radius_m`（既定 2,500 m、設定値）以内の link Way をグラフ連結成分として収集する。
2. link Way の有向グラフで、入次数・出次数が 1 の連続ノードを縮約し、「分岐点・合流点・本線接続点・一般道接続点・料金所」の間を 1 本の論理ランプとする。
3. 料金所を挟む場合は、料金所の前後で別ランプとする（料金所連絡路は `flow=toll_link`）。
4. ランプの所属施設は、グラフ上で最初に到達する施設とする。複数施設に到達する集散路は `flow=collector_distributor` とし、両施設を `related_facility_ids` に持つ。

#### 4.4.2 ID 付与

- `ramp_id = {facility_id}-RAMP-{nnn}`（3 桁ゼロ埋め）
- 番号は再現性のため決定的に振る。施設代表点から見た、ランプ始点の方位角（真北 0°、時計回り）の昇順。同じ方位角の場合は長さの降順。
- OSM 更新後も同一ランプを追跡できるよう、構成 Way ID 集合の Jaccard 係数 0.5 以上で旧 ID を継承する（§4.18）。

#### 4.4.3 方向分類

| 属性 | 値 | 判定 |
|---|---|---|
| `flow` | `entry`（流入）/ `exit`（流出）/ `jct_link`（JCT連絡）/ `collector_distributor`（集散路）/ `facility_link`（SA・PA連絡）/ `toll_link`（料金所連絡）/ `two_way`（双方向） | 本線への接続方向で判定 |
| `carriageway` | `up` / `down` / `inner` / `outer` / `unknown` | 接続する本線 carriageway の向き |
| `branch_side` | `left` / `right` | 本線進行方向に対する分岐・合流位置（外積の符号） |
| `merge_diverge` | `merge` / `diverge` / `both` | 本線との接点の種類 |
| `from_ref`, `to_ref` | 路線 ref、方向、一般道名、料金所 | 両端の接続先 |
| `is_loop` | bool | 累積方位変化 ≥ 200° |

#### 4.4.4 ランプ属性

ランプ長、緯度経度列、方向、車線数、制限速度、曲率系（§4.6）、標高・勾配系（§4.7, §4.8）、構造物延長（トンネル／橋梁／高架）、加速車線長（流入ランプの合流点から本線の車線減少点まで。OSM で取れない場合は null）、合流直前 100 m の最大勾配、終端 100 m の平均勾配、OSM 品質、DEM 品質を保持する。項目の一覧は §9.3 を参照。

### 4.5 IC/JCT 間距離

#### 4.5.1 原則

距離は直線距離ではなく、本線ネットワーク上の経路長とする。経路探索は本線 carriageway グラフに限定する。本線外（一般道）を通る経路は異常（§4.5.5）とする。

#### 4.5.2 区間の種類と方向

- 対象：IC↔IC、IC↔JCT、JCT↔JCT、IC/JCT↔SA・PA。同一路線上で隣接する施設ペアをすべて生成する。
- 方向別（up/down、inner/outer、A→B と B→A）に別レコードとする。距離が同じでも別に保存する。
- `section_id = {route_ref}-{from_facility_id}-{to_facility_id}-{direction}`

#### 4.5.3 距離の定義（複数保持）

| 定義キー | 内容 |
|---|---|
| `standard` | **標準値**：代表点間の本線中心線距離。代表点＝その施設のその方向の carriageway 上で、最初の分流点と最後の合流点の中点（本線上の弧長で中点） |
| `diverge_to_diverge` | 起点施設の分流点 → 終点施設の分流点 |
| `merge_to_diverge` | 起点施設の合流点 → 終点施設の分流点（純粋な本線走行区間） |
| `facility_point` | 施設代表点（統合後の重心の本線投影点）間 |
| `toll_to_toll` | 料金所間（ランプ経由。両施設に料金所がある場合のみ） |
| `ramp_end_to_ramp_end` | 起点の流入ランプ始端 → 終点の流出ランプ終端 |
| `mainline_connection` | 起点の本線接続点 → 終点の本線接続点 |

上下線が別 Way の区間では、方向ごとに自分の carriageway 上の距離を使う。両方向の平均を「中心線距離」として `distances.centerline_avg` にも保持する。

#### 4.5.4 区間属性

道路距離（定義別）、平均勾配、窓平均の最大上り・最大下り、累積上昇・下降、最低・最高標高、最小曲率半径、トンネル延長、橋梁延長、通過 Way ID 列、geometry、品質フラグを持つ。

#### 4.5.5 経路異常検出

| フラグ | 条件 |
|---|---|
| `NO_PATH` | 本線グラフで経路が見つからない |
| `ONEWAY_VIOLATION` | 無向なら到達できるが、有向では到達できない |
| `VIA_NON_MOTORWAY` | 本線外の道路を経由しないと到達できない |
| `EXCESSIVE_DETOUR` | 経路長 ÷ 代表点間の直線距離 > 1.8（設定値） |
| `NAME_COLLISION` | 同名施設が複数あり、接続の候補が曖昧 |
| `MAINLINE_GAP` | 本線チェーンが途中で切れている（端点間 50 m 以内に未接続の本線端点がある） |
| `NETWORK_DISCONTINUITY` | 上記以外の連結成分の分断 |
| `OUT_OF_RANGE` | 終点が範囲外参照の施設 |

### 4.6 曲率・線形解析

#### 4.6.1 前処理

1. UTM に投影する。
2. 等距離に再サンプリングする（ランプ 1 m、本線 5 m を内部の計算間隔とする。出力するサンプル点は §4.7.1 の補間間隔）。
3. 平面座標を Savitzky–Golay 平滑化する（窓：ランプ 15 m、本線 51 m、2 次。設定値）。OSM のノード折れ点による曲率スパイクを除くため。

#### 4.6.2 曲率の定義

- 三点外接円法：弧長 s の点 P(s) について、P(s−L)、P(s)、P(s+L) の外接円の半径を R(s) とし、符号は左カーブを正とする。L はランプ 10 m、本線 30 m（設定値）。
- 曲率 κ(s) = ±1/R(s) [1/m]
- 区間端の L 以内は計算対象外とする。

#### 4.6.3 指標

| 指標 | 定義 |
|---|---|
| 最小曲率半径 `min_radius_m` | `|κ|` に長さ `curvature.min_arc_m`（既定 15 m）の移動中央値を適用した後の最大値の逆数。短いノイズ区間を除外する |
| 最大曲率 `max_curvature` | 同上の最大 |κ| |
| 平均曲率 `mean_curvature` | 全長の |κ| の平均 |
| 平均曲率半径 `mean_radius_m` | カーブ区間（|κ| > 1/3000）の 1/mean|κ| |
| 曲率変化率 `curvature_rate_max` | dκ/ds の最大絶対値 [1/m²]（緩和曲線の評価用） |
| カーブ区間 | |κ| > 1/3000 が 20 m 以上続く区間。左右、長さ、最小 R を保持 |
| 連続カーブ区間数、直線区間長 | 上記から集計 |

### 4.7 標高付与

#### 4.7.1 補間間隔と DEM

補間間隔（設定値）：高精度解析 5 m（ランプ既定）、通常解析 10 m（本線既定）、広域スクリーニング 25 m、全国概算 50 m。

| 地域 | 優先順 | 取得方法（登録不要） |
|---|---|---|
| 日本 | 1. 地理院 標高タイル DEM5A（5 m、航空レーザ）→ 2. DEM5B/5C → 3. DEM10B（10 m）→ 4. Copernicus GLO-30 | 地理院タイル `cyberjapandata.gsi.go.jp/xyz/dem5a_png/{z}/{x}/{y}.png` 等（PNG 標高タイル、z=15/14）。※基盤地図情報ダウンロードサービスはログインが必要なため使用しない |
| 米国 | 1. USGS 3DEP 1 m（提供範囲内）→ 2. 3DEP 1/3 arc-second（約 10 m）→ 3. Copernicus GLO-30 | USGS TNM（The National Map）の公開 GeoTIFF・Cloud Optimized GeoTIFF |
| カナダ | 1. NRCan HRDEM / MRDEM → 2. CDEM → 3. Copernicus GLO-30 | NRCan 公開データ |
| 共通補完 | Copernicus DEM GLO-30 | AWS Open Data の COG |

- NASA SRTM（Earthdata ログインが必要）と ALOS AW3D30（JAXA 登録が必要）は、登録不要の原則（REQ-D06）に反するため既定では使わない。ユーザーが自分で取得したファイルをローカル指定した場合だけ読み込める（`dem.local_sources`）。
- 地理院 PNG 標高タイルは RGB から標高を復元する（無効値は (128,0,0)）。復元式は `elevation/gsi_tiles.py` に実装し、単体テストで検証する。
- 取得した DEM ごとに `dem_dataset` テーブル（§9.9）へ、名称、解像度、水平座標系、標高基準（日本は T.P.、米国は NAVD88 など）、取得日、推定誤差（公称値）、欠損率を記録する。

#### 4.7.2 DEM 接合

各サンプル点で双一次補間により標高を取得する。欠損時は優先順に従って下位 DEM で補完し、`dem_source` に実際に使った DEM を記録する。全 DEM で欠損した点は NaN とし、`DEM_MISSING` フラグを立てる。

#### 4.7.3 平滑化

選択式（設定値、既定は「外れ値除去 → Savitzky–Golay」）：

| 方式 | パラメータ既定値 |
|---|---|
| 移動平均 | 窓 25 m（ランプ）／ 250 m（本線） |
| 移動中央値 | 同上 |
| Savitzky–Golay | 窓 25 m／ 250 m、2 次 |
| 距離ベースのローパス | カットオフ波長 50 m／ 500 m（ゼロ位相 Butterworth） |
| 外れ値除去 | ローカル中央値との差 > 3×MAD の点を補間置換 |

元標高 `z_dem_raw`、平滑化後 `z_smoothed`、構造物補正後 `z_corrected` の 3 系列をすべて保存する。勾配の算出には `z_corrected` を使う。

#### 4.7.4 構造物補正

- 識別：`bridge=*`（viaduct 含む）、`tunnel=*`、`covered=yes`、`layer≠0`、`cutting`、`embankment`、多層 JCT（同じ平面位置に異なる layer のランプが重なる）
- 構造物区間のサンプル点には `low_confidence=true` を付ける。
- 補正：構造物区間の前後の非構造物点（各 20 m の平均）を端点とし、区間内を補間する。既定はエルミート補間（端点の勾配を保持）、選択で線形補間。
- 上下線の標高差：同じ断面の上下線サンプル点で、|Δz| > 5 m の区間に `CARRIAGEWAY_ELEV_DIFF` を記録する（情報のみ）。
- トンネル内の DEM 値は地表高なので、`z_dem_raw` は保存するが勾配計算には使わない。

### 4.8 勾配解析

#### 4.8.1 定義

- 瞬間勾配 `g_inst(s) = (z_c(s+Δ) − z_c(s−Δ)) / 2Δ × 100 [%]`（Δ＝補間間隔）。**表示用。候補抽出には使わない**。
- 窓平均勾配 `g_w(s) = (z_c(s+w/2) − z_c(s−w/2)) / w × 100 [%]`。w ∈ {10, 50, 100, 500, 1000} m。窓が要素の外にはみ出る s は計算しない。要素長が w 未満の場合は端点間の平均勾配 1 値とし、`window_truncated=true` を立てる。
- 符号は要素の進行方向基準（区間は方向別なので、上り方向と下り方向で符号が逆になる）。

#### 4.8.2 指標（ランプ・区間ごと）

瞬間勾配の最大・最小、平均勾配（端点標高差 ÷ 長さ）、窓別の最大上り・最大下り、累積上昇量・下降量（`z_corrected` の正負差分の和。1 m 未満の振動は 0.3 m ヒステリシスで除外）、連続上り距離・連続下り距離（g_100 > +1 % または < −1 % が続く最長距離）、標高差、最高・最低標高。

#### 4.8.3 標準仕様

候補抽出と検索の既定は 100 m 窓平均勾配とする。最大瞬間勾配だけで候補を抽出する検索条件は、UI で警告を出す。

### 4.9 IC/JCT 形状分類

#### 4.9.1 基本方針

OSM に形状分類が登録されていることを前提とせず、ネットワークの特徴量からルールベースで推定する。ルールは `config/classification_rules.yaml` に外出しし、`classifier_version`（例 `rules-1.0.0`）で版管理する。

#### 4.9.2 分類候補

- IC：ダイヤモンド型、変形ダイヤモンド型、トランペット型、クローバーリーフ型、部分クローバー型、シングルポイント型、平面交差接続型、ハーフIC、スマートIC、複合型、分類不能
- JCT：Y型、T型、トランペット型、クローバーリーフ型、タービン型、スタック型、風車型、複合型、分類不能
- SA/PA は分類対象外（`shape_class=SA/PA`）

#### 4.9.3 特徴量（`classification_feature` に保存）

接続本線数、接続方向数（施設に出入りする有向本線方向の数）、ランプ本数、ループランプ数、ランプ接続順序（本線上の分流・合流の並び列）、施設サブグラフの次数分布、本線同士の交差角度、左折・右折方向の構成、曲率の符号と変化（各ランプの累積方位変化）、閉ループの有無、ランプ同士の交差と立体関係（layer 差・bridge）、一般道接続の有無と接続点数、料金所の有無・位置（本線側か一般道側か）。

#### 4.9.4 ルールと信頼度

- 各分類について、特徴量に対する条件群（必須条件と加点条件）でスコアを計算し、ソフトマックス（温度は設定値）で 0〜1 の信頼度に変換する。
- 出力：推定分類、信頼度、第2候補とその信頼度、判定根拠（満たした・満たさなかった条件の一覧。人が読める日本語文）、分類不能理由（必須条件を満たす分類がない場合）。
- 初版ルールの例（付録 C に全量）：
  - トランペット型（IC）：接続本線 1、一般道接続 1、ループ 1 本、ランプ 4 本前後、料金所が一般道側。
  - ダイヤモンド型：ループ 0、ランプ 4 本、各ランプが本線と交差道路を直結、一般道接続 2 点。
  - クローバーリーフ型：ループ 4 本。部分クローバーはループ 1〜3 本かつ平面交差あり。
  - タービン型（JCT）：接続方向 4、ループ 0、旋回ランプ 4 本が大きな同方向曲率を持つ。
  - スタック型：接続方向 4、ループ 0、立体レベル（layer の種類数）≥ 3。
- 信頼度 < 0.70（設定値）の施設は `review_status=needs_review` とし、人手確認キュー（§4.19.3）に入れる。

### 4.10 データ品質管理

#### 4.10.1 品質レベル

| レベル | 条件 |
|---|---|
| 高 high | 必須属性（名称、路線、ランプ接続）が揃い、ネットワークが連続し、重大フラグがない |
| 中 medium | 解析は可能だが一部属性が欠損（出口番号なし、車線数なし等）、または軽微フラグあり |
| 低 low | 手動確認が必要（ランプ欠落の疑い、分類低信頼、DEM 低信頼が 30 % 超など） |
| 使用不可 unusable | 経路・形状・標高が成立しない（`NO_PATH`、DEM 全欠損、ランプ 0 本など） |

DEM 品質（`dem_quality`）は別に持つ：構造物・DEM 欠損・低解像度 DEM の使用率から高／中／低を判定する（閾値は設定値。既定は低信頼点の割合 ≤ 10 % で高、≤ 40 % で中、それ以外は低）。

#### 4.10.2 検出フラグ（`quality_flag` テーブル）

`NAME_MISSING`、`EXIT_NUMBER_MISSING`、`RAMP_MISSING`、`NETWORK_GAP`、`UNNATURAL_DISTANCE`、`EXTREME_GRADE`（|g_100| > 12 %）、`EXTREME_CURVATURE`（R < 15 m）、`DEM_MISSING`、`TUNNEL_DEM`、`ELEVATED_GROUND_MISMATCH`、`CARRIAGEWAY_MISCONNECT`、`DUPLICATE_FACILITY`、`LOW_CLASSIFICATION_CONFIDENCE`、`TYPE_NAME_MISMATCH`、§4.5.5 の経路異常フラグ。

各フラグは、重大度（info/warn/error）、対象 ID、説明文（日本語）、検出 run_id を持つ。

### 4.11 検索

#### 4.11.1 対象

ランプ、IC/JCT（施設）、区間の 3 種類。UI ではモード切替（ランプ検索 / IC/JCT 検索 / 区間検索）とする。

#### 4.11.2 基本検索

名称（正規化名称で部分一致。表記ゆれを吸収）、路線名・ref、出口番号、都道府県・州、緯度経度、中心点からの距離。

#### 4.11.3 形状検索

形状分類（複数選択）、ループランプあり、ハーフIC、分類信頼度の下限。

#### 4.11.4 数値条件

| 対象 | 検索可能フィールド |
|---|---|
| ランプ | 長さ、最小曲率半径、最大曲率、カーブ長、最大上り（窓別）、最大下り（窓別）、平均勾配、連続上り距離、標高差、最高標高、車線数、制限速度、加速車線長、合流直前勾配、終端勾配、難易度候補値、flow、DEM 品質、OSM 品質 |
| 施設 | 種別、形状、信頼度、ランプ数、ループ数、平均ランプ長、最小 R（施設内）、最大上り（施設内）、最大高低差、接続方向数、品質、確認状態 |
| 区間 | 距離（定義を選択）、平均勾配、最大上り・下り、累積上昇、累積上昇／km、最小曲率半径、トンネル・橋梁延長、最高標高、品質 |

#### 4.11.5 クエリ表現（QueryDSL）

UI・API・保存クエリで共通の JSON とする（§9.12）。AND/OR のネスト、単位付きの値、並び順、件数上限を表現できる。クエリを人が読める日本語文に変換する `to_text()` を実装する（モックアップの検索式表示と同じ）。

#### 4.11.6 日本・北米横断（P5）

country を検索条件に含め、単位はメートル法に統一する（表示は設定で mph/ft に切替可）。分類体系の差（北米の SPUI＝シングルポイント型、partial cloverleaf の多さ等）はルールファイルの country 別の分岐で吸収する。

### 4.12 類似地点検索

#### 4.12.1 類似ランプ

- 特徴ベクトル：長さ、log(最小 R)、100 m 最大上り、100 m 最大下り、|標高差|、曲率プロファイル、勾配プロファイル、flow（カテゴリ）、所属施設の形状分類（カテゴリ）。
- プロファイルは正規化弧長で 64 点に再標本化し、曲率は符号付きで比較する（左右反転を許可する設定あり、既定は許可）。
- 距離：スカラー特徴は母集団（検索対象範囲）で z 標準化した重み付きユークリッド距離。プロファイルはユークリッド距離（オプションで DTW）。カテゴリ不一致はペナルティを加える。重みは `config/similarity.yaml`。
- 類似度 = 1/(1+d)。上位 N 件（既定 10）と、特徴量ごとの寄与を返す。

#### 4.12.2 類似 IC/JCT

- 特徴量：ランプ数、ループ数、平均ランプ長、log(施設内最小 R)、施設内最大 100 m 上り、最大高低差、接続方向数、形状分類（一致で加点）、種別（一致で加点）、道路規格（本線の設計速度帯。maxspeed から推定）。
- 特徴量ごとの近さ `sim_i = exp(−|a_i − b_i| / σ_i)`（σ_i は母集団の標準偏差）。総合類似度＝重み付き平均。
- 出力：上位 N 件、総合類似度、特徴量ごとの近さ（UI では7マスの濃淡で表示）、形状一致の有無。
- SA/PA は SA/PA 同士だけで比較する。
- 起動場所：IC/JCT 詳細画面の「類似IC/JCT」カード、ランプ詳細画面の「類似IC/JCTを検索」ボタン、条件検索（IC/JCT モード）の各行の「類似」。

### 4.13 2地点比較（IC/JCT）

- 条件検索画面の結果行（ランプ・IC/JCT どちらのモードでも）にある「＋比較」で比較トレイに登録する（A・Bの2枠。3件目を登録すると古い方を押し出す）。ランプ行から登録した場合は、そのランプの所属施設を登録する。
- 比較パネル：A・B の周辺形状図（同じ縮尺）、A–B 構造類似度と特徴量ごとの近さ、比較表（種別、形状分類、信頼度、ランプ数、ループ数、平均ランプ長、最小 R、最大上り勾配、最大高低差、接続方向、データ品質）。値が異なる行は強調表示する。
- 比較・出力画面にも同じ比較を持ち、トレイの状態を共有する。
- IC/JCT 詳細の類似一覧の「比較」から、条件検索の比較パネルを直接開ける。

### 4.14 範囲統計比較

#### 4.14.1 目的

2 つの範囲 A・B に含まれる路線・IC/JCT・ランプ・区間の分布を比較し、差の大きさと統計的な確からしさを示す。例：関東 vs 北米ミシガン州、東北道沿い vs 北関東道沿い、検索結果 vs 路線全体。

#### 4.14.2 範囲定義（RangeSpec, §9.11）

路線、行政区域、地域プリセット、矩形・円・ポリゴン、形状分類、検索クエリ（QueryDSL）の結果、施設の個別選択。条件検索の結果から「範囲Aへ／範囲Bへ」で設定できる。範囲定義は名前を付けて保存できる。

#### 4.14.3 集合の決め方

- 施設：範囲に含まれる施設（代表点が範囲内。路線指定の場合はその路線に属する施設）。
- ランプ：対象施設のランプ。`toll_link` は既定で除外（設定で含められる）。
- 区間：路線指定の場合はその路線の区間。それ以外は両端の施設が範囲内にある区間。片方向（既定は down）だけを数え、二重計上を避ける（設定で両方向も可）。
- A と B が重複する要素：既定では「重複を含めたまま比較し、重複件数を警告表示」する。オプションで「重複を両方から除外」「重複を A 側にだけ残す」を選べる。重複がある場合、独立性の仮定が崩れることを注記する。

#### 4.14.4 指標レジストリ

統計比較の指標は `analysis/metrics.py` のレジストリに登録し、追加しやすくする。初期セット：

| 対象 | 指標 |
|---|---|
| ランプ | 長さ、最小曲率半径、100 m 最大上り、100 m 最大下り、|標高差|、最高標高、難易度候補値、曲率変化率、加速車線長 |
| 施設 | ランプ数、ループ数、分類信頼度、施設内最小 R |
| 区間 | 距離、100 m 最大上り、累積上昇／km、最小曲率半径、トンネル比率 |
| 割合（比率） | ループランプ率、低信頼分類率、品質「低」率、DEM 低信頼率、現在の検索条件の該当率 |
| 密度 | ランプ密度（本／10 km）、施設密度（施設／100 km）※区間延長で正規化 |
| カテゴリ | 形状分類の構成、flow の構成 |

各指標は、名称、対象、単位、小数桁、値の取得関数、「大きいほど厳しい」かどうか、の属性を持つ。

#### 4.14.5 統計手法

| 種類 | 記述統計 | 検定・効果量 |
|---|---|---|
| 連続量 | n、平均、標準偏差、中央値、四分位、P10、P90、最小、最大 | Mann–Whitney U 検定（両側。n ≤ 20 は正確検定、それ以外は連続性補正付き正規近似。scipy を使用）。効果量は Cliff's δ（主）と Cohen's d（参考）。中央値差の 95 % ブートストラップ信頼区間（2,000 回、乱数シード固定） |
| 割合 | 件数、割合、Wilson 95 % 信頼区間 | 2 標本比率の検定。期待度数 < 5 の場合は Fisher の正確検定。割合の比と差 |
| カテゴリ構成 | 構成比 | χ² 検定。期待度数 < 5 のセルが 20 % を超える場合は Fisher–Freeman–Halton（モンテカルロ、シード固定） |
| 多重比較 | — | 同時に表示する全指標の p 値に Benjamini–Hochberg 補正を行い、補正前と補正後（q 値）を両方表示 |

- n < 10 の指標は「参考値」と表示し、自動抽出（§4.14.6）の対象から外す。
- 区間延長で正規化した密度指標は、ポアソン率の比の検定を使う。

#### 4.14.6 主な差異の自動抽出

|Cliff's δ| ≥ 0.33（medium）または q < 0.10 の指標を、|δ| の大きい順に最大 5 件、日本語の文で示す。

例：「ランプ最小曲率半径：中央値 A 237 m / B 233 m（B−A −4 m、95%CI [−38, 29]、δ=−0.06、q=0.81）」

加えて、現在の検索条件の該当率を A・B で比較する文を必ず出す。

#### 4.14.7 可視化

範囲 A・B を色分けした地図（A：シアン、B：オレンジ、重複：白）、集計サマリー表、指標別統計表（箱ひげ図列付き。行クリックで分布図の指標が切り替わる）、分布図（構成比のヒストグラム重ね合わせ。ビン数は Freedman–Diaconis、上限 30）、ECDF 図（切替表示）、散布図（最小 R × 100 m 上り勾配。対数 x 軸、仕様例の閾値線、点クリックでランプ詳細）、形状分類構成の帯グラフ。

#### 4.14.8 出力

統計表 CSV（記述統計、検定、効果量、CI、q 値、範囲定義、run_id を含む）、統計レポート（HTML＋PNG、§4.17.3）、範囲定義 JSON。

### 4.15 車両性能向け指標と候補ランキング

#### 4.15.1 複合指標（ランプ単位で算出し、検索可能にする）

| キー | 定義（既定閾値は設定値） |
|---|---|
| `small_r_uphill` | 最小 R ≤ 150 m の区間内の最大 g_100 |
| `small_r_long` | 最小 R ≤ 150 m かつ長さ ≥ 400 m（bool） |
| `continuous_curve_relief` | 連続カーブ区間の総長 × 高低差 |
| `pre_merge_grade` | 合流点手前 100 m の最大 g_50 |
| `short_accel` | 加速車線長 < 200 m（bool。null の場合は不明） |
| `curvature_rate_max` | §4.6.3 |
| `end_uphill` | 終端 100 m の平均勾配 > 3 %（bool） |
| `high_alt_steep` | 最高標高 ≥ 800 m かつ g_100 ≥ 4 %（bool） |

#### 4.15.2 難易度候補値

```
difficulty = 100 × Σ w_k × c_k
c_R  = clamp((150 − minR) / 110, 0, 1)        # 曲率寄与
c_G  = clamp(g100_up_max / 7, 0, 1)            # 上り勾配寄与
c_L  = clamp(length / 1000, 0, 1)              # ランプ長寄与
c_K  = clamp(curvature_rate_max / k_ref, 0, 1) # 曲率変化寄与
c_M  = clamp((320 − accel_len) / 150, 0, 1)    # 合流条件寄与（流入のみ、null は 0 とし欠損フラグ）
w = {R:0.30, G:0.30, L:0.15, K:0.10, M:0.15}  # config/difficulty.yaml
```

構成要素の値は常に併記して保存・表示する。スコアだけで判断させない UI 文言を入れる。

#### 4.15.3 目的別プリセット（`config/presets.yaml`）

| プリセット | 既定条件（QueryDSL） |
|---|---|
| 仕様書の例 | flow=entry, 長さ≥500, 最小R≤100, g_100上り≥5, DEM品質≥中 |
| 登坂性能評価 | g_100上り≥4.5, 長さ≥300 |
| 加速合流評価 | flow=entry, 加速車線長≤200 または 長さ≤450, 合流直前勾配≥2 |
| 旋回・駆動力評価 | 最小R≤120, g_100上り≥3 |
| 回生性能評価 | g_100下り≥4 |
| 熱負荷評価 | 区間：連続上り距離≥3 km かつ 平均勾配≥2 |
| 高標高性能評価 | 最高標高≥800（日本の MVP 範囲では≥300 で試験） |
| 連続勾配評価 | 区間：g_1000 上り≥3 |
| 小Rランプ評価 | 最小R≤80 |

プリセットは並び順付きのランキング（既定は難易度候補値の降順）を返す。

### 4.16 ルート解析

- 入力：起点・終点（施設、または座標を最寄り本線へスナップ）、経由地（任意）。
- 経路：本線＋JCT 連絡ランプのグラフで、最短距離経路と、代替経路を最大 3 本（k-shortest simple paths、重複率 70 % 未満のもの）。
- 出力：総距離、通過施設（順序付き）、区間別距離、標高プロファイル、最大勾配、累積上昇、最小曲率半径、トンネル・橋梁延長、通過ランプ一覧、Route GeoJSON、解析結果 CSV。候補経路の比較表。
- UI：区間画面の「ルート」タブとして実装する（§11.8）。

### 4.17 出力

#### 4.17.1 データ出力

| 形式 | 対象 | ライブラリ |
|---|---|---|
| CSV（UTF-8 BOM 付き。Excel 互換） | 全データセット | pandas |
| Excel（.xlsx） | 全データセット（1 データセット＝1 シート＋来歴シート） | openpyxl |
| GeoJSON | 施設、ランプ、区間、ルート | geopandas |
| GeoPackage | 全空間データセット（1 ファイル・複数レイヤー） | geopandas / pyogrio |
| Parquet（GeoParquet 1.1） | 全データセット | pyarrow |
| SQLite（SpatiaLite なしの属性＋WKB） | 全データセット | sqlite3 |
| PostGIS | P4 | — |

推奨データセット（要求仕様 §18.2 に準拠し、§9 のスキーマで出力）：IC/JCT マスター、ランプマスター、IC/JCT 間区間マスター、道路サンプルポイント。すべての出力に `run_id` と来歴ファイル（`provenance.json`）を同梱する。

#### 4.17.2 画像出力

- クライアント出力：現在の地図表示を PNG/JPEG で保存（MapLibre の canvas、`preserveDrawingBuffer`）。
- サーバー出力（一括・SVG 対応）：Matplotlib で道路 MRI 画像を描く。パラメータは、中心または範囲、縮尺（m/px）または画像サイズ、背景色、線幅倍率、レイヤー、色分け指標、ラベル有無、形式（PNG/JPEG/SVG）。
- 一括生成：検索結果や施設リストを入力にして、非同期ジョブで実行し、進捗を返す。ZIP で出力する。ファイル名は `{facility_id}_{ramp_id?}_{metric}.png`。
- 画像に出典表示（© OpenStreetMap contributors、使用 DEM 名）を描き込む。

#### 4.17.3 画像レポート

検索結果の各件について、次をまとめた HTML レポート（自己完結 HTML＋画像フォルダ。PDF はオプション）を生成する：道路 MRI 画像、通常の位置確認図（背景地図なし。周辺一般道とランドマークの名称ラベル付き）、標高プロファイル、勾配プロファイル、曲率プロファイル、基本諸元、品質・注意事項、来歴。PDF 化は Playwright の印刷機能で行う（任意依存）。

### 4.18 差分更新と変更監視（データ構造は MVP、処理は P4）

- 新しい PBF と前回の run を比較し、`change_event` を生成する：施設の新設・廃止、名称変更、道路形状の変化（ハウスドルフ距離 > 20 m）、ランプの新設・削除、未分類施設、過去の解析結果への影響（影響を受ける区間・保存クエリ・範囲定義）。
- ID の継承：施設は正規化名称＋路線＋代表点 500 m 以内で旧 ID を継承する。ランプは構成 Way の Jaccard ≥ 0.5 で継承する。継承できなかった手動修正は「孤立修正」として確認キューに入れる。
- P4 で pyosmium のレプリケーション差分（`.osc`）適用に対応する。

### 4.19 手動修正

#### 4.19.1 修正可能な項目

施設：形状分類、種別、確認状態、信頼度コメント、名称（表示名の上書き）、対象外指定。ランプ：所属施設、flow（流入・流出方向）、接続元、接続先、対象外指定。区間：対象外指定、採用する距離定義。

#### 4.19.2 保存と適用

- 修正は `manual_edit` テーブル（§9.8）に追記専用で保存する（更新・削除はしない。取り消しも新しい修正として記録する）。
- パイプラインは自動結果を生成した後、最新の有効な修正を重ねて適用する。自動結果の値も保持し、UI では「自動：トランペット型 0.68 → 手動：Y型」のように両方を示す。
- 修正者は OS のユーザー名（設定で変更可）、日時は ISO 8601（JST 表示）で記録する。

#### 4.19.3 人手確認ワークフロー

```
自動処理 → 信頼度 < 0.70 または品質「低」だけ抽出 → 確認キュー（IC/JCT 一覧の「要確認」フィルタ）
→ 地図・航空写真（外部リンク：地理院地図）・公式情報で確認 → 手動確定（確認済み）→ 次回 run で再利用
```

地理院地図への外部リンクは、施設の座標を URL パラメータに入れたリンクを表示するだけで、アプリから自動アクセスはしない。

### 4.20 公式情報との照合（P4 以降）

`official_reference` テーブル（出典名、URL、取得日、項目、値）を用意し、OSM 値との差を表示する。両方の値を保持し、採用した方を `adopted_source` に記録する。対象出典：NEXCO 各社、首都高速、阪神高速、国土交通省、地方道路公社、米国各州 DOT、US DOT、カナダ各州交通当局。MVP では CSV の手動インポートだけ対応する。

### 4.21 実走データ照合（P6、インターフェースのみ）

- 入力アダプタのインターフェース `ingest/drive_log.py::DriveLogReader` を定義する（CSV、GPX、GeoJSON、MDF/MF4（asammdf）、緯度経度付き CAN ログ）。
- マップマッチングは HMM 方式（最近傍道路、進行方向、速度、ネットワーク接続、時刻連続性）を想定し、関数シグネチャと出力スキーマ（`drive_match`）だけ定義する。
- 比較項目：GPS 高度と DEM 標高、実測勾配と DEM 勾配、速度と曲率、加速度と勾配、負荷と道路条件、推定経路と実走経路、OSM 形状と GPS 線形。
- 色分け指標「実走データ有無」用のフィールドを、ランプ・区間に予約しておく。

### 4.22 再現性と来歴

すべての run は `run_metadata` に次を記録し、すべての出力ファイルに `provenance.json` として同梱する：run_id（ULID）、実行日時、コードのバージョン（git commit）、OSM の取得日時・ソース・レプリケーション sequence・ファイルの sha256、DEM データセットと取得日、補間間隔、平滑化方式とパラメータ、勾配計算方式、曲率計算方式とパラメータ、分類ロジックの版、手動修正の件数と最終修正 ID、設定ファイルのハッシュ。

同じ入力・同じ設定では、出力（数値列）がビット単位で一致すること（§14.6）。

---

## 5. 非機能要件

### 5.1 費用

原則無料、API キー不要、ユーザー登録不要、従量課金なし、商用サービスへの依存なし。使用するデータのライセンスと帰属表示を守る（§13.2）。

### 5.2 オフライン動作

データ取得後、次がネットワークなしで動くこと：道路表示、IC/JCT 検索、区間距離計算、ランプ解析、標高・勾配表示、画像出力、CSV/GeoJSON 等の出力、類似検索、統計比較、レポート生成。フロントエンドの依存ライブラリはすべてバンドルし、CDN を使わない。フォントもローカルに同梱する。

### 5.3 軽量性

- 背景地図は不要。表示対象範囲のデータだけを読み込む。
- 表示用データ（簡略化済み）と解析用データ（高精度）を分離する。表示用は Douglas–Peucker 簡略化（ズーム別の許容誤差）を行う。MVP は FlatGeobuf、P4 は PMTiles。
- 生成済みの画像と解析結果をキャッシュする。

### 5.4 性能目標

計測環境：ノート PC（CPU 8 コア、RAM 16 GB、SSD）、Windows 11 または macOS。

| 項目 | 目標 |
|---|---|
| 単一 IC 周辺の表示 | 2 秒以内 |
| IC/JCT 検索 | 1 秒以内 |
| 計算済み区間の表示 | 1 秒以内 |
| 単一ランプの解析（再計算） | 5 秒以内 |
| 栃木県の IC/JCT 一覧生成（全パイプライン、DEM はキャッシュ済み） | 5 分以内 |
| 範囲統計比較（各範囲 ≤ 5,000 ランプ） | 3 秒以内（ブートストラップ込み） |
| 10 万件規模の道路セグメント検索 | 3 秒以内（DuckDB） |
| 全国解析 | バッチ処理を許容（目標：関東 1 時間以内） |
| 地図操作 | 60 fps を目標、30 fps 以上（栃木＋周辺の全レイヤー表示時） |

### 5.5 信頼性・保守性

- パイプラインは段階ごとに中間成果物（GeoParquet）を保存し、途中から再開できる（`--from-step`）。
- 型ヒント 100 %、mypy strict（`src/roadmri/` の core）、ruff、テストカバレッジ：コアロジック 85 % 以上、全体 70 % 以上。
- ログは構造化ログ（JSON 行）で `logs/` に出力する。

### 5.6 外部サービスへの配慮

- HTTP クライアントは共通の `ingest/http.py` を使い、User-Agent に `roadmri/<version>` を設定し、ホスト別のレート制限（Overpass：10 秒に 1 回、地理院タイル：同時 4 接続かつ 毎秒 20 タイル以下、その他：同時 4 接続）と指数バックオフを入れる。
- 社内ネットワークのプロキシ（`HTTPS_PROXY`）と社内 CA 証明書（`REQUESTS_CA_BUNDLE` / 設定）に対応する。
- ダウンロードしたものはすべてキャッシュし、同じものを 2 回取らない。

### 5.7 セキュリティ

- API サーバーは既定で `127.0.0.1` にだけバインドする。認証は MVP では不要（ローカル単一利用者）。複数ユーザー運用（P4 以降の PostGIS 構成）で認証を追加する。
- 外部へ送信するデータはない（取得リクエストを除く）。ユーザー名などの個人情報を外部 URL に含めない。

### 5.8 互換性

- OS：Windows 11、macOS 14 以降、Ubuntu 22.04 以降。
- ブラウザ：Chrome / Edge の最新 2 版。
- 画面：1280×800 以上を主対象。1024 幅でもレイアウトが崩れない。スマートフォンは閲覧のみ（MRI ビューと一覧）。

### 5.9 アクセシビリティ・表示

- UI 言語は日本語（文言は `web/src/i18n/ja.json` に集約し、英語化できる構造にする）。
- 色だけで意味を伝えない（凡例、数値、記号を併記する）。グラフの配色は色覚多様性に配慮する（A/B はシアン／オレンジ）。
- 単位は SI（m、km、%、km/h）。北米表示は設定で mph・ft に切替可。

---

## 6. 使用技術

### 6.1 バックエンド・データ処理（Python）

| 用途 | 技術 | 備考 |
|---|---|---|
| 言語・環境 | Python 3.12、uv（依存とロック） | `pyproject.toml` |
| CLI | Typer | `roadmri` コマンド |
| OSM 読込 | pyosmium、osmium-tool（範囲抽出） | Windows では osmium-tool を conda-forge で導入するか、pyosmium の `SimpleWriter` で代替 |
| 小規模取得 | Overpass API（httpx） | 試験用 |
| 地理処理 | GeoPandas 1.x、Shapely 2.x、pyproj、pyogrio | |
| グラフ | networkx（MVP）、rustworkx（P4 で置換可能） | インターフェースで抽象化 |
| DEM | rasterio、numpy、Pillow（地理院 PNG タイル） | |
| 数値計算 | numpy、scipy（signal.savgol_filter、stats） | |
| 保存 | DuckDB（spatial 拡張）＋ GeoParquet（pyarrow） | 単一ファイル `data/processed/roadmri.duckdb` |
| API | FastAPI、uvicorn、pydantic v2 | OpenAPI を自動生成 |
| 画像 | Matplotlib | 一括画像・SVG |
| Excel | openpyxl | |
| レポート | Jinja2（HTML）、Playwright（PDF、任意） | |
| 設定 | pydantic-settings ＋ YAML | |
| ジョブ | 標準の `concurrent.futures` ＋ DuckDB のジョブテーブル | MVP は単一プロセス。外部キューは使わない |
| テスト | pytest、hypothesis、pytest-benchmark、syrupy（スナップショット） | |
| 品質 | ruff、mypy | |

### 6.2 フロントエンド（TypeScript）

| 用途 | 技術 |
|---|---|
| ビルド | Vite、TypeScript 5（strict） |
| UI | React 18 |
| 地図 | MapLibre GL JS 4.x（背景タイルなし。GeoJSON / FlatGeobuf ソース、P4 で PMTiles） |
| グラフ | Apache ECharts 5（プロファイル、ヒストグラム、箱ひげ図、散布図、ECDF） |
| 表 | TanStack Table |
| 状態管理 | Zustand（UI 状態）、TanStack Query（サーバーデータ） |
| API 型 | openapi-typescript（FastAPI の OpenAPI から生成） |
| ルーティング | React Router（URL に画面・選択 ID・検索条件を保持） |
| テスト | Vitest、Testing Library、Playwright（E2E） |
| 品質 | ESLint、Prettier |

### 6.3 構成

```
[CLI: roadmri]──┐
                ├─> パイプライン（ingest → network → facilities → ramps → sections → geometry → elevation → grade → classify → quality → export）
                │        │ 中間成果物: data/interim/*.parquet
                │        ▼
                │   DuckDB（data/processed/roadmri.duckdb）＋ 表示用 FlatGeobuf（data/tiles/）
                │        ▲
[FastAPI 127.0.0.1:8765] ── /api/*（検索・統計・出力・手動修正・ジョブ）
                │        ▲
[Web UI（Vite ビルド成果物を FastAPI が静的配信）] ── MapLibre / ECharts
```

`roadmri serve` で API と UI を 1 プロセスで起動し、既定のブラウザで `http://127.0.0.1:8765` を開く。

---

## 7. 処理フロー（パイプライン）

| 段 | モジュール | 入力 | 出力 |
|---|---|---|---|
| 1 ingest_osm | `ingest/osm_pbf.py` | RangeSpec、PBF | `interim/osm_ways.parquet`、`osm_nodes.parquet` |
| 2 build_network | `network/graph.py` | 1 | `interim/graph.pkl`、`mainline_chains.parquet` |
| 3 extract_facilities | `network/junctions.py`, `dedupe.py`, `names.py` | 1, 2 | `facility`、`facility_osm_map` |
| 4 extract_ramps | `network/ramps.py` | 2, 3 | `ramp`、`ramp_way_map` |
| 5 build_sections | `network/sections.py`, `anomalies.py` | 2, 3, 4 | `section`、`section_distance` |
| 6 resample_geometry | `geometry/resample.py`, `curvature.py` | 4, 5 | `sample_point`（xy、κ） |
| 7 ingest_dem / attach_elevation | `ingest/dem.py`, `elevation/*` | 6 | `sample_point`（z 三系列）、`dem_dataset` |
| 8 compute_metrics | `elevation/grade.py`, `analysis/indicators.py`, `difficulty.py` | 6, 7 | ランプ・区間の指標列 |
| 9 classify | `classify/features.py`, `rules.py` | 3, 4, 8 | `classification_feature`、`facility` の分類列 |
| 10 quality | `quality/flags.py` | 全段 | `quality_flag`、品質レベル |
| 11 apply_manual_edits | `store/manual_edits.py` | 3〜10、`manual_edit` | 確定値 |
| 12 publish | `export/tiles.py`, `store/duckdb_store.py` | 11 | DuckDB への反映、表示用 FlatGeobuf、`run_metadata` |

CLI：

```
roadmri fetch   --range config/regions/tochigi.yaml        # OSM・DEM 取得（キャッシュ利用）
roadmri build   --range tochigi [--from-step ramps] [--to-step classify]
roadmri serve   [--port 8765] [--no-browser]
roadmri export  --dataset ramps --format gpkg --query presets/spec_example
roadmri images  --query <file|preset> --format png --size 1920x1080
roadmri stats   --a <range.json> --b <range.json> --out stats.csv
roadmri diff    --base <run_id> --new <run_id>             # P4
```

---

## 8. ディレクトリ構成

```
road-mri/
├─ CLAUDE.md                     # 開発規約（§16.3 を転記）
├─ README.md                     # セットアップ・使い方・出典表示
├─ pyproject.toml / uv.lock
├─ docs/
│  ├─ SPEC.md                    # 本書
│  ├─ mockup/road_mri_mockup.html  # UI の正解（数値はダミー）
│  ├─ adr/                       # 設計判断記録（0001-xxx.md）
│  └─ algorithms/                # 曲率・勾配・分類・統計の数式と検証メモ
├─ config/
│  ├─ settings.yaml              # パス、ポート、プロキシ、ユーザー名
│  ├─ regions/tochigi.yaml       # RangeSpec のプリセット
│  ├─ routes.yaml                # 路線の起点（上下判定用）・ref 別名
│  ├─ name_normalization.yaml
│  ├─ name_aliases.yaml
│  ├─ analysis.yaml              # 補間間隔、平滑化、曲率、勾配窓、閾値
│  ├─ classification_rules.yaml
│  ├─ difficulty.yaml
│  ├─ similarity.yaml
│  ├─ presets.yaml
│  ├─ stats.yaml                 # 指標の既定セット、ブートストラップ回数、有意水準
│  └─ dem_sources.yaml
├─ src/roadmri/
│  ├─ __init__.py / cli.py / config.py / provenance.py / logging.py
│  ├─ ingest/        http.py, osm_pbf.py, overpass.py, boundaries.py, dem.py, gsi_tiles.py, usgs.py, copernicus.py, drive_log.py(P6 IF)
│  ├─ network/       graph.py, mainline.py, junctions.py, dedupe.py, names.py, ramps.py, sections.py, anomalies.py, routing.py
│  ├─ geometry/      projection.py, resample.py, smoothing.py, curvature.py
│  ├─ elevation/     sampler.py, smoothing.py, structures.py, grade.py
│  ├─ classify/      features.py, rules.py, engine.py
│  ├─ analysis/      indicators.py, difficulty.py, presets.py, query.py, search.py, similarity.py, compare.py, metrics.py, stats.py, ranges.py
│  ├─ quality/       flags.py, levels.py
│  ├─ store/         schema.sql, duckdb_store.py, parquet_io.py, manual_edits.py, jobs.py, migrations/
│  ├─ export/        tabular.py, geo.py, excel.py, images.py, report.py, tiles.py, templates/
│  └─ api/           main.py, deps.py, routers/{meta,regions,facilities,ramps,sections,search,similar,compare,stats,route,export,images,reports,edits,jobs,quality,changes}.py, schemas/
├─ web/
│  ├─ package.json / vite.config.ts / tsconfig.json
│  ├─ public/fonts/              # Noto Sans JP、JetBrains Mono（同梱）
│  └─ src/
│     ├─ main.tsx / App.tsx / routes.tsx
│     ├─ api/                    # 生成型＋クライアント
│     ├─ state/                  # zustand ストア（layers, selection, compareTray, ranges, query）
│     ├─ styles/tokens.css       # §11.2 のデザイントークン
│     ├─ i18n/ja.json
│     ├─ components/
│     │  ├─ map/ RoadMriMap.tsx, layers.ts, colorScales.ts, LayerPanel.tsx, InfoPanel.tsx, ScaleBar.tsx, Legend.tsx
│     │  ├─ charts/ ProfileChart.tsx, CurvatureChart.tsx, HistogramChart.tsx, BoxPlotCell.tsx, ScatterChart.tsx, EcdfChart.tsx
│     │  ├─ tables/ DataTable.tsx
│     │  ├─ compare/ CompareTray.tsx, ComparePanel.tsx
│     │  ├─ similar/ SimilarFacilities.tsx, SimilarRamps.tsx, FeatureSquares.tsx
│     │  └─ common/ Kpi.tsx, Badge.tsx, ConfBar.tsx, Toast.tsx, QueryText.tsx
│     └─ pages/ Region.tsx, MriView.tsx, FacilityList.tsx, FacilityDetail.tsx, RampDetail.tsx, Sections.tsx, Search.tsx, RangeStats.tsx, CompareExport.tsx
├─ tests/
│  ├─ fixtures/  tochigi_small.osm.pbf（宇都宮IC〜鹿沼IC周辺）, synthetic/*.geojson, dem/*.tif, gsi_tile_*.png, golden/
│  ├─ unit/ integration/ api/ perf/
│  └─ conftest.py
├─ web/tests/   unit/（vitest）, e2e/（Playwright）
├─ scripts/      make_fixture.py, label_tool.py（分類の正解ラベル付け補助）
└─ data/         ※ git 管理外（.gitignore）
   ├─ raw/{osm,dem,boundaries}/  interim/  processed/  tiles/  cache/  exports/  logs/
```

---

## 9. データ構造

DuckDB のスキーマを正とし（`store/schema.sql`）、GeoParquet・GeoPackage の出力も同じ列名を使う。geometry は WGS84（EPSG:4326）、DuckDB では `GEOMETRY` 型。列名は英語の snake_case、UI の表示名は i18n で付ける。

### 9.1 facility（IC/JCT マスター）

| 列 | 型 | 説明 |
|---|---|---|
| facility_id | VARCHAR PK | `{CC}-F-{8hex}`。sha1(country, normalized_name, primary_route_ref) の先頭 8 桁。衝突時は代表点の geohash7 を付加 |
| country | VARCHAR | `JP` / `US` / `CA` |
| admin_code | VARCHAR | 都道府県コード（`09`）／州 FIPS |
| name / name_en | VARCHAR | 表示名（手動上書き後） |
| normalized_name | VARCHAR | 例 `佐野藤岡|IC` |
| name_variants | VARCHAR[] | 全表記 |
| facility_type | VARCHAR | `IC`/`JCT`/`SIC`/`SA`/`PA` |
| is_half_ic / is_jct_combined | BOOLEAN | |
| parent_facility_id | VARCHAR NULL | SA 併設スマートIC など |
| exit_number | VARCHAR NULL | |
| route_refs | VARCHAR[] | 主路線を先頭に |
| destinations | JSON | 方向別の方面・行先 |
| lon, lat | DOUBLE | 代表点 |
| geom | GEOMETRY(Point) | |
| hull | GEOMETRY(Polygon) | ランプ群の凸包（表示・範囲判定用） |
| ramp_count / loop_ramp_count | INTEGER | |
| connection_directions | INTEGER | |
| shape_class / shape_confidence | VARCHAR / DOUBLE | 確定値（手動優先） |
| shape_class_auto / shape_confidence_auto | VARCHAR / DOUBLE | 自動推定値 |
| shape_class_2 / shape_confidence_2 | VARCHAR / DOUBLE | 第2候補 |
| classification_reasons | JSON | 満たした・満たさない条件の配列（日本語文付き） |
| unclassifiable_reason | VARCHAR NULL | |
| review_status | VARCHAR | `unreviewed`/`needs_review`/`reviewed` |
| quality_level | VARCHAR | `high`/`medium`/`low`/`unusable` |
| osm_timestamp | TIMESTAMP | 構成要素の最新 |
| merge_reason | JSON | 統合根拠 |
| run_id | VARCHAR | |

### 9.2 facility_osm_map

facility_id、osm_type（node/way）、osm_id、role（`junction_up`/`junction_down`/`toll`/`services` 等）、carriageway。

### 9.3 ramp（ランプマスター）

| 列 | 型 | 説明 |
|---|---|---|
| ramp_id | VARCHAR PK | `{facility_id}-RAMP-{nnn}` |
| facility_id | VARCHAR FK | |
| related_facility_ids | VARCHAR[] | 集散路など |
| seq | INTEGER | |
| flow | VARCHAR | §4.4.3 |
| carriageway / branch_side / merge_diverge | VARCHAR | |
| from_ref / to_ref | JSON | `{kind: mainline|road|toll|services, route_ref, direction, name}` |
| geom | GEOMETRY(LineString) | |
| length_m | DOUBLE | |
| lanes / maxspeed_kmh | INTEGER NULL | |
| is_loop | BOOLEAN | |
| min_radius_m / mean_radius_m | DOUBLE | |
| max_curvature / mean_curvature | DOUBLE | 1/m |
| curvature_rate_max | DOUBLE | 1/m² |
| curve_length_m / straight_length_m | DOUBLE | |
| grade_inst_max / grade_inst_min | DOUBLE | % |
| grade_up_max_w10, _w50, _w100, _w500 | DOUBLE NULL | % |
| grade_down_max_w10, _w50, _w100, _w500 | DOUBLE NULL | %（正の値で保持） |
| grade_mean | DOUBLE | |
| elev_gain_m / elev_loss_m / elev_diff_m | DOUBLE | |
| z_max / z_min | DOUBLE | |
| cont_up_len_m / cont_down_len_m | DOUBLE | |
| tunnel_len_m / bridge_len_m / viaduct_len_m | DOUBLE | |
| accel_lane_len_m | DOUBLE NULL | |
| pre_merge_grade / end_grade | DOUBLE NULL | |
| composite | JSON | §4.15.1 の複合指標 |
| difficulty / difficulty_components | DOUBLE / JSON | |
| window_truncated | BOOLEAN | |
| osm_quality / dem_quality / quality_level | VARCHAR | |
| drive_data_available | BOOLEAN | P6 予約 |
| excluded | BOOLEAN | 手動の対象外 |
| run_id | VARCHAR | |

### 9.4 ramp_way_map

ramp_id、way_id、order、reversed（bool）。

### 9.5 section（区間マスター）

| 列 | 型 | 説明 |
|---|---|---|
| section_id | VARCHAR PK | `{route_ref}-{from}-{to}-{direction}` |
| route_ref | VARCHAR | |
| from_facility_id / to_facility_id | VARCHAR | |
| direction | VARCHAR | `up`/`down`/`inner`/`outer`/`a_to_b` |
| dist_standard_m | DOUBLE | 標準値 |
| geom | GEOMETRY(LineString) | |
| way_ids | BIGINT[] | |
| grade_mean / grade_up_max_w100 / grade_down_max_w100 / grade_up_max_w1000 | DOUBLE | |
| elev_gain_m / elev_loss_m / z_min / z_max | DOUBLE | |
| min_radius_m | DOUBLE | |
| tunnel_len_m / bridge_len_m | DOUBLE | |
| cont_up_len_m | DOUBLE | |
| passes_non_motorway | BOOLEAN | |
| anomaly_flags | VARCHAR[] | |
| quality_level | VARCHAR | |
| run_id | VARCHAR | |

### 9.6 section_distance

section_id、definition（§4.5.3 のキー）、distance_m、from_point（GEOMETRY）、to_point、note。

### 9.7 sample_point（道路サンプルポイント）

| 列 | 型 | 説明 |
|---|---|---|
| element_type | VARCHAR | `ramp`/`mainline` |
| element_id | VARCHAR | ramp_id または mainline chain_id |
| section_id | VARCHAR NULL | 本線の場合 |
| seq | INTEGER | |
| s_m | DOUBLE | 要素の始点からの距離 |
| lon, lat | DOUBLE | |
| x, y | DOUBLE | UTM |
| z_dem_raw / z_smoothed / z_corrected | DOUBLE | m |
| dem_source / dem_resolution_m | VARCHAR / DOUBLE | |
| grade_inst / grade_w100 | DOUBLE | % |
| curvature | DOUBLE | 1/m（符号付き、左正） |
| structure | VARCHAR | `none`/`bridge`/`viaduct`/`tunnel`/`cutting`/`embankment` |
| layer | INTEGER | |
| low_confidence | BOOLEAN | |

### 9.8 manual_edit（追記専用）

edit_id（ULID）、target_type（facility/ramp/section）、target_id、field、old_value（JSON）、new_value（JSON）、comment、user_name、created_at、osm_anchor（JSON：構成 OSM ID。ID 継承の照合用）、superseded_by（NULL または edit_id）、status（`active`/`orphaned`/`reverted`）。

### 9.9 来歴・データソース

- `run_metadata`：run_id、created_at、git_commit、range_spec（JSON）、params（JSON：§4.22 の全項目）、config_hash、classifier_version、manual_edit_count、last_edit_id、status、duration_s。
- `dataset_source`：source_id、kind（osm/dem/boundary/official）、name、url、retrieved_at、sha256、license、attribution、osm_replication_seq、osm_timestamp。
- `dem_dataset`：dem_id、name、resolution_m、crs、vertical_datum、retrieved_at、nominal_error_m、missing_ratio、coverage（GEOMETRY）、source_id。

### 9.10 その他

- `classification_feature`：facility_id、run_id、features（JSON）、classifier_version。
- `quality_flag`：flag_id、target_type、target_id、code、severity、message_ja、detail（JSON）、run_id。
- `change_event`（P4）：event_id、base_run_id、new_run_id、kind（`facility_added`/`facility_removed`/`renamed`/`geometry_changed`/`ramp_added`/`ramp_removed`/`unclassified`/`impact`）、target_id、detail。
- `official_reference`（P4）：ref_id、target_type、target_id、source_name、url、retrieved_at、field、value、adopted_source。
- `saved_query`：query_id、name、target、query（JSON）、created_at。
- `saved_range`：range_id、name、spec（JSON）、created_at。
- `job`：job_id、kind、params、status、progress、result_path、error、created_at、finished_at。

### 9.11 RangeSpec（JSON）

```json
{
  "type": "route | admin | preset_area | bbox | circle | polygon | shape | query | facilities | union",
  "route_refs": ["E4"],
  "admin_codes": ["09"],
  "bbox": [139.30, 36.25, 140.20, 37.10],
  "circle": {"lon": 139.835, "lat": 36.605, "radius_km": 40},
  "polygon": {"type": "Polygon", "coordinates": []},
  "shape_classes": ["トランペット型"],
  "query": {"$ref": "QueryDSL"},
  "facility_ids": ["JP-F-1a2b3c4d"],
  "members": [{"$ref": "RangeSpec"}],
  "buffer_km": 0,
  "label": "東北道沿い"
}
```

`type` に応じて使う項目だけを持つ。`union` は複数の範囲の和集合。

### 9.12 QueryDSL（JSON）

```json
{
  "target": "ramp",
  "where": {
    "all": [
      {"field": "flow", "op": "eq", "value": "entry"},
      {"field": "length_m", "op": "gte", "value": 500, "unit": "m"},
      {"field": "min_radius_m", "op": "lte", "value": 100, "unit": "m"},
      {"field": "grade_up_max_w100", "op": "gte", "value": 5, "unit": "%"},
      {"field": "dem_quality", "op": "gte", "value": "medium"}
    ],
    "any": []
  },
  "range": {"$ref": "RangeSpec"},
  "sort": [{"field": "difficulty", "dir": "desc"}],
  "limit": 500
}
```

演算子：`eq`、`ne`、`in`、`gte`、`lte`、`gt`、`lt`、`between`、`exists`、`contains`。順序付きカテゴリ（品質：low < medium < high）は `gte` で比較できる。フィールドの許可リストは `analysis/query.py` のレジストリで管理し、SQL インジェクションを防ぐため、値はすべてパラメータバインドする。

### 9.13 統計比較 API の入出力

```json
// POST /api/stats/compare
{
  "range_a": {"$ref": "RangeSpec"}, "range_b": {"$ref": "RangeSpec"},
  "metrics": ["ramp.min_radius_m", "ramp.grade_up_max_w100", "..."],
  "overlap": "keep_warn | exclude_both | keep_in_a",
  "include_toll_link": false,
  "section_direction": "down | both",
  "hit_query": {"$ref": "QueryDSL"},
  "bootstrap": 2000, "seed": 42
}
// 応答
{
  "run_id": "...", "a": {"label": "...", "counts": {}}, "b": {"...": "..."},
  "overlap": {"facilities": 2, "ramps": 7},
  "summary": [{"key": "loop_ratio", "a": 0.172, "b": 0.211, "a_ci": [0.1, 0.28], "b_ci": [], "p": 0.62, "q": 0.81}],
  "metrics": [{
    "key": "ramp.min_radius_m", "label": "ランプ最小曲率半径", "unit": "m", "target": "ramp",
    "a": {"n": 58, "mean": 229, "sd": 120, "median": 237, "q1": 0, "q3": 0, "p10": 0, "p90": 399, "min": 0, "max": 482},
    "b": {},
    "median_diff": -4, "median_diff_ci": [-38, 29],
    "cliffs_delta": -0.06, "cohens_d": -0.10,
    "test": "mannwhitney_asymptotic", "p": 0.689, "q": 0.81,
    "reference_only": false,
    "hist": {"edges": [], "a": [], "b": []}, "ecdf": {"a": [], "b": []}
  }],
  "categorical": [{"key": "shape_class", "a": {}, "b": {}, "test": "chi2", "p": 0.3}],
  "insights_ja": ["..."],
  "provenance": {}
}
```

---

## 10. API 仕様（FastAPI、プレフィックス `/api`）

| メソッド・パス | 説明 |
|---|---|
| GET `/meta` | アプリの版、現在の run、データソース、出典表示文 |
| GET/POST `/regions`、GET `/regions/{id}/status` | 範囲プリセット、取得・処理状態 |
| POST `/pipeline/run` → `{job_id}` | パイプライン実行（範囲、開始段、終了段） |
| GET `/jobs/{id}` | 進捗・結果 |
| GET `/layers/{name}.fgb?bbox=&zoom=` | 表示用ベクタ（mainline、ramps、general、facilities、tolls、structures） |
| GET `/facilities` | 一覧（フィルタ、ソート、ページング） |
| GET `/facilities/{id}` | 詳細（ランプ、隣接区間、分類根拠、品質、来歴） |
| GET `/facilities/{id}/similar?limit=` | 類似 IC/JCT（§4.12.2） |
| GET `/facilities/{id}/history` | 手動修正履歴 |
| GET `/ramps/{id}`、GET `/ramps/{id}/profile?step=` | 詳細、プロファイル（サンプル点列） |
| GET `/ramps/{id}/similar?limit=` | 類似ランプ |
| GET `/sections?route=&direction=`、GET `/sections/{id}`、GET `/sections/{id}/profile` | 区間 |
| POST `/route` | ルート解析（§4.16） |
| POST `/search/{target}` | QueryDSL 検索（target = ramps / facilities / sections） |
| POST `/search/text` | QueryDSL → 日本語文 |
| GET `/presets`、GET/POST/DELETE `/saved-queries`、GET/POST/DELETE `/saved-ranges` | |
| POST `/compare/facilities` | 2 地点比較（§4.13） |
| POST `/stats/compare` | 範囲統計比較（§9.13） |
| POST `/edits`、POST `/edits/{id}/revert` | 手動修正 |
| GET `/quality/flags?target=&severity=` | 品質フラグ |
| GET `/review-queue` | 要確認キュー |
| POST `/export` → `{job_id}` | データ出力（dataset、format、query または range） |
| POST `/images` → `{job_id}` | 画像出力（単発・一括） |
| POST `/reports` → `{job_id}` | 画像レポート |
| GET `/changes?base=&new=` | 変更監視（P4） |

エラーは RFC 9457（Problem Details）形式で返し、`detail` は日本語にする。

---

## 11. UI 仕様

### 11.1 全体構成

- ヘッダー（高さ 50 px）：ロゴ「ROAD MRI」、サブタイトル、データチップ（OSM の日付、DEM 名、オフライン状態、現在の run）、施設名のグローバル検索（表記ゆれ対応のインクリメンタル検索）。
- 左ナビ（幅 196 px、1024 px 未満では上部の横スクロールタブ）：01 地域・路線選択、02 道路MRIビュー、03 IC/JCT一覧、04 IC/JCT詳細、05 ランプ詳細、06 IC/JCT間区間、07 条件検索、08 範囲統計比較、09 比較・出力。ナビ下部に対象範囲と件数のサマリーを出す。
- URL に状態を持たせる（例 `/facility/JP-F-1a2b3c4d`、`/search?mode=ramp&q=<base64>`、`/stats?a=<range>&b=<range>`）。ブラウザの戻る・進むで画面状態が戻ること。
- 共通部品：KPI カード、バッジ（品質：高=緑、中=琥珀、低=赤、種別：IC=琥珀、JCT=紫、SA/PA=緑）、信頼度バー、並べ替えと固定ヘッダー付きの表、トースト、読込中のスケルトン、空状態の文言、エラー表示。

### 11.2 デザイントークン（`web/src/styles/tokens.css`）

モックアップの値をそのまま使う。

```
--bg:#05070a; --panel:#0c1015; --panel2:#11161d; --line:#1b232d; --line2:#28323e;
--tx:#d7dee7; --mut:#7d8b9a; --dim:#4a5663;
--cyan:#38e1ff; --amber:#ffb53d; --yellow:#ffd84a; --red:#ff5b4f; --orange:#ff7a2e;
--green:#47e08f; --violet:#a98bff; --white:#f4f7fb;
フォント: 本文 "Noto Sans JP"（同梱）, 数値 "JetBrains Mono"（同梱）, 13px / line-height 1.55
角丸: カード 7px, 入力 5px, バッジ 3px
範囲比較色: A = --cyan, B = --orange, 重複 = #e8e8ff
```

アプリはダークテーマ固定（MRI 表現が前提のため）。画像出力では背景色を選べる。

### 11.3 画面01 地域・路線選択

- 国の切替（日本／北米）。都道府県・州の一覧と取得状態バッジ（処理済み／取得済み／キャッシュあり／未取得）。
- 範囲指定方法のタブ（都道府県、市区町村、州・郡、矩形、中心＋半径、路線名、IC/JCT 名、GeoJSON、地図で選択）と各入力フォーム。「範囲を適用」でパイプラインジョブを起動し、進捗を表示する。
- 選択範囲のプレビュー地図（範囲枠を破線で表示）。
- データ取得状態の表（データ種別、出典・版、取得日、規模、状態）と DEM メタデータ（座標系、標高基準、推定誤差、欠損率）。
- 処理パイプラインの段ごとの状態と件数。開発フェーズの表示。

### 11.4 画面02 道路MRIビュー

- 全画面の地図。背景タイルなし、背景は `#000`。
- 描画スタイル（MapLibre の line レイヤーを重ねて発光を表現）：

| レイヤー | 色 | 表現 |
|---|---|---|
| 本線 | `#eef3f9` | 3 層（幅 7・不透明度 0.10・blur 4／幅 3・0.22／幅 1.2・1.0）。ズーム 13 以上で上下線を分けて描画 |
| ランプ | `#38e1ff`（JCT 連絡は `#ffd84a`） | 本線と同じ 3 層。「ランプ発光」オフで 1 層 |
| 一般道 | `rgba(110,125,140,.30)` | 1 層、幅 0.9〜1.3 |
| 解析対象（検索結果） | `#ff7a2e` | 幅 2.2 の 3 層。最前面 |
| 選択中 | `#ffffff` | |
| IC / JCT / SA・PA | `#ffb53d` / `#a98bff` / `#47e08f` | 放射状グラデーションの発光点（circle レイヤー 3 重＋blur） |
| 料金所 | `#ffb53d` | 5 px の四角 |
| 橋梁 | `rgba(169,139,255,.55)` | 幅 5 の下敷き |
| トンネル | `rgba(170,180,190,.9)` | 破線 4-4 |
| 品質警告 | `#ff5b4f`（低）/ `#ffb53d`（要確認） | 三角記号 |
| ラベル | `#aebccb`（JCT は `#c9b8ff`） | 11 px、黒の縁取り。ズーム 11 未満では JCT だけ |

- フロートパネル：左上にレイヤー切替（§3.4 REQ-V02 の 12 種）と色分け指標の選択。上中央にツールバー（＋、－、全体、ランプ発光、検索結果を強調、画像出力）。右上に情報パネル。下部に縮尺バー、カーソル座標、凡例。
- 色分け指標とカラースケール（`colorScales.ts`、モックアップと同じ stop 値）：
  - 勾配：−8 % 青 `rgb(60,120,255)` → シアン → 0 % 白 → 琥珀 → オレンジ → +8 % 赤 `rgb(255,60,60)`
  - 曲率半径（対数）：R30 赤 → R3km 白
  - 標高：30 m 紫 → 青 → シアン → 550 m 白（範囲はデータの P2〜P98 に自動調整）
  - データ信頼度：高 緑／中 琥珀／低 赤
  - 制限速度、車線数、ランプ種別、IC/JCT 分類：カテゴリ色（凡例付き）
  - 実走データ有無：P6
- 「曲率による線幅変更」オプション：|κ| に応じてランプの線幅を 1〜3 倍にする（要求仕様 21.1）。
- 操作：ホイールでズーム、ドラッグで移動、ホバーでツールチップ（名称・形状／ランプ ID・区分・長さ）、クリックで情報パネルを開く。
- 情報パネル（施設）：名称、種別、路線、出口番号、形状分類、信頼度、第2候補、確認状態、品質、OSM 更新日、隣接 IC/JCT（方向、距離、最大勾配）、隣接区間の標高ミニプロファイル（ホバーで地図にカーソル表示）、ランプ一覧（クリックでランプの情報パネル）、品質警告文、ボタン「詳細を開く」「区間解析」。
- 情報パネル（ランプ）：ID、区分、接続元→接続先、KPI（長さ、最小 R、100 m 平均上り、標高差）、ミニプロファイル、品質警告、ボタン「ランプ詳細」「施設へ戻る」。

### 11.5 画面03 IC/JCT 一覧

フィルタ（種別、路線、信頼度の下限、確認状態、品質、「要確認だけ」トグル）、件数表示、CSV 出力。列：名称、種別、路線、出口番号、推定分類、信頼度バー、第2候補（信頼度）、ランプ数、品質、確認状態、緯度経度。列ヘッダーで並べ替え。行クリックで詳細へ。

### 11.6 画面04 IC/JCT 詳細

- ヘッダー：名称、種別・品質・確認状態バッジ、路線、出口番号、OSM ID、前へ・次へ、MRI で表示。
- 左：周辺道路形状の地図（半径 1.8 km、ランプ一覧の行ホバーでそのランプを強調）。
- 右上：形状分類カード（推定分類、信頼度、第2候補、分類ロジックの版、特徴量の表〈値と出典タグ〉、低信頼の場合はその理由文）。
- 右下：手動修正カード（分類、種別、確認状態、コメント、保存）と修正履歴（自動処理を含む時系列）。
- 下段：基本情報（施設 ID、正規化名称、表記ゆれ、座標、DEM 標高、方面・行先、元 OSM オブジェクト、OSM 取得日）、接続関係（路線、方向、次の施設、道路距離、最大上り、最小 R。クリックで区間画面へ）、ランプ一覧（ID、区分、方向、接続元→接続先、長さ、最小 R、100 m 上り、最大下り、標高差、品質、DEM 品質。クリックでランプ詳細へ）。
- 最下段：**類似 IC/JCT カード**（§4.12.2。順位、施設、種別、形状分類〈形状違いの注記〉、類似度バー、特徴量ごとの近さ〈7 マスの濃淡、ホバーで値〉、ランプ数、最小 R、最大上り、ボタン「詳細」「比較」）。「比較」は条件検索の比較パネルを A=この施設、B=選択施設で開く。「条件検索で探す」ボタン。

### 11.7 画面05 ランプ詳細

- ヘッダー：ランプ ID、区分、方向、OSM 品質、DEM 品質バッジ、同じ施設内のランプ切替（前・次・選択）、施設へ、**「類似ランプ」「類似IC/JCTを検索」ボタン**。
- KPI：ランプ長、最小曲率半径、最大上り（瞬間）、100 m 平均上り、最大下り（100 m）、標高差、累積上昇／下降、難易度候補値。
- 左：ランプを白で強調した地図。右：標高プロファイル（DEM 生値の灰色線、補正後の勾配色分け線、橋梁・トンネル・DEM 低信頼区間の帯、最大上り・最大下り地点のマーカー、接続元・接続先・合流・分流位置）、曲率プロファイル（1/R、左カーブ正、R の読み値）。両グラフのカーソルは地図と連動する。
- 下段：属性（車線数、制限速度、加速車線長、平均曲率半径、標高の最高・最低、構造物、補間間隔、平滑化、勾配計算方式）と、難易度候補値の内訳（5 要素の重み付き寄与バー、「スコアは目安」の注記）、品質・注意事項。
- 窓幅の切替（10/50/100/500 m）で、プロファイル上の窓平均勾配線を重ねて表示できる。

### 11.8 画面06 IC/JCT 間区間

- タブ：「区間」「ルート」。
- 区間タブ：路線、起点、終点の選択と方向反転ボタン。方向バッジ（上り／下り＋方面）。KPI（標準距離、平均勾配、最大上り・下り〈100 m〉、累積上昇／下降、最高／最低標高、最小 R、トンネル／橋梁延長）。標高・勾配プロファイル（DEM 生値の点線、施設位置のマーカー、トンネル帯、最大上り地点。地図とカーソル連動）。区間を強調した地図。距離の定義別の表。経路異常の一覧（要対応／警告／情報）。路線の全区間の表（方向別。クリックで切替）。
- ルートタブ：起点、終点、経由地 → 候補経路（最大 3）の比較表、選択経路のプロファイル、通過施設とランプの一覧、GeoJSON・CSV 出力。

### 11.9 画面07 条件検索

- モード切替：**ランプ検索 / IC/JCT 検索 / 区間検索**。
- ランプ検索：目的別プリセットボタン、条件フォーム（地域・路線、ランプ区分、長さの上下限、最小 R の上限、100 m 平均上り・下りの下限、窓幅の選択、最高標高の下限、DEM 信頼度、OSM 品質、IC/JCT 形状、詳細条件〈複合指標・AND/OR〉の展開）、検索式の日本語表示。最大瞬間勾配で条件を組んだ場合は警告を出す。
- IC/JCT 検索：地域・路線、種別、形状分類、信頼度の下限、ランプ数の下限、ループ有無、最小 R の上限、最大上りの下限。
- 結果表：件数と母数、ボタン（施設を範囲Aへ、範囲Bへ、MRI で強調表示、CSV、GeoJSON、条件を保存）。ランプ行の列：ID、施設、区分、形状、長さ、最小 R、100 m 上り、100 m 下り、標高差、最高標高、DEM 品質、難易度候補値（5 色の積み上げバー＋値）、操作（類似、＋比較）。IC/JCT 行の列：施設（A/B 登録済みの表示）、種別、形状、信頼度、ランプ数、ループ、平均ランプ長、最小 R、最大 100 m 上り、最大高低差、品質、操作（類似、＋比較）。
- **比較トレイと比較パネル**（§4.13）：A・B のチップ（× で解除）、入れ替え、比較表の開閉、比較・出力画面へ。開いたときは 2 枚の地図、構造類似度、比較表。
- **類似地点パネル**：基準のランプまたは施設と、類似一覧。

### 11.10 画面08 範囲統計比較

- 比較プリセット（例：東北道 vs 北関東道、県南 vs 県北、西部 vs 東部、東北道 vs 日光宇都宮道路、検索結果 vs 東北道全体。北米対応後は「関東 vs ミシガン州」）と A⇄B 入れ替え。
- 範囲 A・B のカード（上辺がそれぞれシアン・オレンジ）：範囲の種類、値、個別選択時はチェックリスト、件数（施設、ランプ、区間）、含まれる施設名の抜粋。重複がある場合の警告と扱いの選択。
- 地図（A・B・重複の塗り分け。区間もその色で強調）。
- 集計サマリー表（施設構成、ランプ数、ループ率、区間延長、ランプ密度、低信頼分類率、品質低率、DEM 低信頼率、検索条件の該当率。割合には 95 %CI）。
- 主な差異（自動抽出文、検定方法の注記、n<10 の注記）。
- 指標別統計表：指標（対象）、単位、n（A/B）、平均、中央値、P90、最大、中央値差［95 %CI］、Cliff's δ、Cohen's d、p、q、分布（箱ひげ図。A 上段、B 下段）。p<0.05・q<0.10・|δ|≥0.33 を強調。行クリックで分布図を切替。
- 分布図（ヒストグラム⇄ECDF 切替、中央値の破線）と散布図（最小 R × 100 m 上り。範囲外の点は灰色。ホバーで名称、クリックでランプ詳細）。
- 形状分類構成の帯グラフ（カテゴリ検定の結果を併記）。
- 出力：統計表 CSV、統計レポート、範囲定義の保存。

### 11.11 画面09 比較・出力

- IC/JCT 構造比較（条件検索の比較トレイと状態を共有）。
- データ出力：データセット（IC/JCT マスター、ランプマスター、区間マスター、サンプルポイント、ルート）× 形式（CSV、Excel、GeoJSON、GeoPackage、Parquet、SQLite）。対象は全件、現在の検索結果、範囲のいずれかを選ぶ。
- 画像出力：サイズ（現在表示、1920×1080、3840×2160、1200×1200、任意）、縮尺（m/px）、背景色、線幅倍率、形式（PNG、JPEG、SVG）、出典の描き込み。単発と一括（ジョブ進捗表示、ZIP ダウンロード）。
- 画像レポート生成。
- 再現性情報（§4.22 の全項目）の表示。

### 11.12 状態と例外の表示

各画面で、読込中（スケルトン）、0 件（条件を緩める案内）、API エラー（再試行ボタン）、データ未取得（画面01へ誘導）の表示を実装する。

---

## 12. 設定ファイル（既定値）

`config/analysis.yaml` の例：

```yaml
resample:
  ramp_step_m: 5
  mainline_step_m: 10
  internal_ramp_step_m: 1
  internal_mainline_step_m: 5
geometry_smoothing:
  method: savgol
  ramp_window_m: 15
  mainline_window_m: 51
  order: 2
curvature:
  method: three_point
  ramp_chord_m: 10
  mainline_chord_m: 30
  min_arc_m: 15
  curve_threshold_radius_m: 3000
elevation:
  smoothing: {method: savgol, ramp_window_m: 25, mainline_window_m: 250, order: 2, outlier_mad: 3.0}
  structure_correction: {method: hermite, anchor_len_m: 20}
grade:
  windows_m: [10, 50, 100, 500, 1000]
  default_window_m: 100
  gain_hysteresis_m: 0.3
  continuous_threshold_pct: 1.0
facility:
  merge_max_distance_m: {IC: 2000, JCT: 3000, default: 2000}
  ramp_search_radius_m: 2500
  general_road_radius_m: 2000
classification:
  low_confidence_threshold: 0.70
  softmax_temperature: 0.5
quality:
  dem_low_ratio_high: 0.10
  dem_low_ratio_medium: 0.40
  extreme_grade_pct: 12
  extreme_radius_m: 15
  detour_ratio: 1.8
```

`config/stats.yaml`：`bootstrap: 2000`、`seed: 42`、`alpha: 0.05`、`fdr_q: 0.10`、`min_n_reference: 10`、`insight_min_abs_delta: 0.33`、`insight_max: 5`、`default_metrics: [...]`。

---

## 13. 制約条件

### 13.1 データ取得の制約

- ユーザー登録、API キー、従量課金が必要なデータソースは既定で使わない（§4.7.1 の除外ソースを参照）。ユーザーが自分で用意したローカルファイルの読み込みは可。
- Overpass API は小規模試験にだけ使う。県以上の規模は PBF をローカル処理する。
- 地理院タイルは、利用規約に従い出典を明示し、レート制限を守る。
- OSM の形状分類タグは前提にしない。
- 実際の路面高は取得できないため、構造物区間の標高は推定値であり、低信頼として扱う。

### 13.2 ライセンスと帰属表示

| データ | ライセンス | 表示・注意 |
|---|---|---|
| OpenStreetMap | ODbL 1.0 | 画面と画像に「© OpenStreetMap contributors」。本システムが作る DB は OSM の派生データベースとなるため、**社外に公開・配布する場合は ODbL の継承義務が生じる**。社内利用の範囲では問題ない。README に明記する |
| 国土地理院 標高タイル | 国土地理院コンテンツ利用規約（出典明示） | 「出典：国土地理院（標高タイル）」 |
| USGS 3DEP | パブリックドメイン | 出典表示を推奨 |
| NRCan DEM | Open Government Licence – Canada | 出典表示 |
| Copernicus DEM | Copernicus 利用規約 | 所定のクレジット文を表示 |

出典表示文は `/api/meta` から取得し、地図右下・画像・レポート・出力ファイルの来歴に必ず入れる。

### 13.3 技術的な制約

- 単一 PC・単一ユーザーのローカル運用（MVP）。サーバー常駐や外部 DB は必須にしない。
- MapLibre には加算合成がないため、発光は重ねたレイヤーと blur で近似する（モックアップの canvas 加算合成とわずかに見た目が異なることを許容する）。
- 全国（P4）を超える規模では、networkx と FlatGeobuf からの移行（rustworkx、PMTiles、PostGIS）を前提にする。MVP ではインターフェースの分離だけ行う。
- 社内 PC 環境（プロキシ、証明書、管理者権限なし）でセットアップできること。native 依存は wheel か conda-forge で入るものに限る。tippecanoe（PMTiles 生成）は P4 で Docker/WSL 経由とする。

---

## 14. テスト条件

### 14.1 単体テスト（`tests/unit/`、すべてオフライン）

| 対象 | テスト内容 | 合格基準 |
|---|---|---|
| 曲率 | 半径 R＝50、100、300、1000 m の合成円弧（ノイズ ±0.3 m を加えたもの、OSM 風の不等間隔点列） | 推定最小 R の誤差 ≤ 5 %（R≤300）、≤ 10 %（R=1000） |
| 曲率の符号 | 左カーブ・右カーブ・S 字 | 符号が正しい |
| 緩和曲線 | 合成クロソイド | 曲率変化率の誤差 ≤ 10 % |
| 勾配 | 一定勾配 5 % の合成プロファイル（ノイズ ±0.5 m） | g_100 の最大の誤差 ≤ 0.3 ポイント |
| 窓の打ち切り | 長さ 60 m の要素で w=100 | 端点間平均を返し、`window_truncated=true` |
| 平滑化 | 5 方式それぞれ | 既知の解析解（正弦波）で振幅の減衰が期待範囲内 |
| 構造物補正 | 橋梁で地表が −7 m 窪む合成データ | 補正後の勾配が真値 ±0.3 ポイント |
| 累積上昇 | 階段状・振動ノイズ | ヒステリシスで振動を除外 |
| 地理院 PNG の復元 | 既知の RGB → 標高、無効値 (128,0,0) | 完全一致 |
| 名称正規化 | 付録 B の表記ゆれ 40 例 | すべて期待どおり |
| 重複統合 | 上下線の junction 合成ケース、同名の別施設（距離 > 閾値） | 正しく統合・非統合 |
| ランプ縮約 | 分岐・合流・料金所を含む合成グラフ | 論理ランプ数と端点が期待どおり |
| ID の決定性 | 同じ入力を 2 回 | 同じ ID |
| 種別判定 | IC、JCT、IC＋JCT、スマートIC、SA、ハーフIC の合成ネットワーク | すべて正しく判定 |
| 区間距離 | 直線 10 km の本線 2 施設 | 標準距離の誤差 < 1 m |
| 経路異常 | 各フラグを起こす合成ケース | フラグが立つ |
| 分類ルール | 典型形（トランペット、ダイヤモンド、クローバー、タービン、Y）の合成ネットワーク | top1 が正解 |
| QueryDSL | 全演算子、順序付きカテゴリ、ネスト、不正フィールド | 正しい結果。不正は 422 |
| 類似度 | 自分自身との類似度 = 1、特徴量の単調性 | |
| 統計 | Mann–Whitney、Cliff's δ、BH 補正、Wilson CI、χ²、Fisher を scipy・既知値と照合（hypothesis で乱数入力） | 誤差 < 1e-9（scipy と同じ実装を使う箇所は完全一致） |
| ブートストラップ | シード固定 | 2 回の実行で同値 |
| 範囲の重複処理 | 3 つのオプション | 件数が仕様どおり |

### 14.2 結合テスト（`tests/integration/`）

- `tests/fixtures/tochigi_small.osm.pbf`（宇都宮IC〜鹿沼IC周辺、数 MB）と同梱 DEM で、全パイプラインを実行する。
- 期待値（ゴールデン、`tests/fixtures/golden/`）と比較する：施設数、ランプ数、区間数、代表的な区間の標準距離、分類結果。数値は許容誤差つきで比較し、件数は完全一致とする。
- 手動修正の保持：修正を入れて再 build しても修正が適用されたままであること。構成 Way を変えた fixture で、ID 継承と孤立修正の検出を確認する。

### 14.3 実データ検証（`@pytest.mark.network`、MVP の受け入れ時に実施）

| 項目 | 基準 |
|---|---|
| 栃木県＋周辺の IC/JCT 抽出 | 東北道（佐野藤岡IC〜那須IC）、北関東道（県内区間）、日光宇都宮道路の全施設が抽出され、取りこぼしと重複がない（NEXCO 東日本の公開施設一覧と目視照合し、照合結果を `docs/validation/tochigi.md` に記録） |
| 区間距離 | NEXCO 公開の IC 間距離（キロポスト差）との差が、全区間で ±3 % 以内、中央値で ±1 % 以内 |
| 形状分類 | 人が付けた正解ラベル（`scripts/label_tool.py` で 30 施設以上）に対し、top1 正解率 ≥ 80 %、top2 正解率 ≥ 95 % |
| 勾配の妥当性 | 本線の 100 m 窓勾配が設計基準上の上限を大きく超える箇所（> 7 %）がすべて構造物・DEM 低信頼区間として説明できる |
| 代表クエリ | 仕様書の例のクエリが実行でき、結果の上位 5 件を航空写真で目視確認して、形状・長さの判定が妥当 |

### 14.4 API テスト（`tests/api/`）

全エンドポイントの正常系・異常系（404、422、ジョブ失敗）、OpenAPI スキーマのスナップショット、応答時間（§5.4）。

### 14.5 フロントエンド

- Vitest：カラースケール、QueryDSL→日本語文、比較トレイのストア（3 件目で押し出し）、範囲ストア。
- Playwright E2E（fixture データで起動したサーバーに対して実行）：
  1. MRI ビューで宇都宮IC をクリック → 情報パネル → 詳細を開く
  2. 色分けを勾配に変更 → 凡例が切り替わる
  3. ランプ詳細のプロファイルにホバー → 地図上にカーソルマーカーが出る
  4. 条件検索で「仕様書の例」プリセット → 結果件数が API と一致 → MRI で強調表示
  5. IC/JCT 検索で 2 施設を＋比較 → 比較パネルが表示され、差のある行が強調される
  6. IC/JCT 詳細の類似一覧から「比較」→ 条件検索の比較パネルが A/B 設定済みで開く
  7. ランプ詳細の「類似IC/JCTを検索」→ 条件検索の類似パネルに基準施設が表示される
  8. 検索結果を「範囲Aへ」→ 範囲統計比較で A が個別選択として反映される
  9. 範囲統計比較でプリセット切替 → 統計表・地図・分布図が更新される → 統計表 CSV のダウンロード
  10. 手動修正の保存 → 履歴に表示 → 再読込後も保持
  11. 画像出力（PNG、SVG）と CSV、GeoPackage のダウンロード
  12. ブラウザの戻る・進むで画面と選択状態が戻る
- 視覚回帰：主要 9 画面のスクリーンショットを保存し、意図しない変化を検出する（しきい値 0.2 %）。

### 14.6 再現性テスト

同じ fixture・同じ設定で 2 回 build し、全テーブルの数値列のハッシュが一致すること。設定を 1 つ変えると `config_hash` と `run_id` が変わること。

### 14.7 性能テスト（`tests/perf/`、pytest-benchmark）

§5.4 の目標を、栃木県の実データ（キャッシュ済み）で計測し、結果を `docs/validation/perf.md` に記録する。10 万セグメント検索は合成データで計測する。

### 14.8 オフラインテスト

ネットワークを遮断した状態（`pytest --disable-socket` と、ブラウザのオフラインモード）で、§5.2 の全機能の E2E が通ること。外部 CDN へのリクエストが 0 件であること（Playwright でリクエストを監視）。

---

## 15. 完成条件（Definition of Done）

### 15.1 MVP 完成条件（すべて満たすこと）

1. `uv sync` と `npm ci`（web）、`roadmri fetch --range tochigi`、`roadmri build --range tochigi`、`roadmri serve` の 4 手順で、社内 PC（Windows 11）に環境構築と起動ができる。README の手順どおりに第三者が再現できる。
2. 栃木県＋周辺で §14.3 の実データ検証基準をすべて満たし、検証記録が `docs/validation/` にある。
3. 9 画面すべてが §11 の仕様どおりに動き、モックアップ v2 と見た目・操作が同等以上である（ダミー値ではなく実データで）。
4. 仕様書の代表クエリが UI と CLI の両方で実行でき、結果を CSV・GeoJSON・画像・レポートで出力できる。
5. 類似ランプ検索、類似 IC/JCT 検索（3 か所から起動）、条件検索内の 2 地点比較、範囲統計比較（5 プリセットと個別選択、統計表、分布図、散布図、形状構成、CSV）が動く。
6. 手動修正が履歴付きで保存され、再 build 後も保持される。要確認キューが機能する。
7. 全出力に `run_id` と `provenance.json` が付き、§14.6 の再現性テストが通る。
8. §14.1〜§14.8 の全テストが CI（GitHub Actions：Windows と Ubuntu）で通る。カバレッジがコア 85 %、全体 70 % 以上。
9. §5.4 の性能目標を満たす（全国解析を除く）。
10. オフラインで全機能が動く（§14.8）。
11. 出典表示が地図・画像・レポート・出力ファイルに入っている（§13.2）。
12. ruff・mypy・eslint の指摘が 0 件。`docs/adr/` に主要な設計判断が記録されている。

### 15.2 フェーズごとの完了条件

| フェーズ | 完了条件 |
|---|---|
| P1 | 施設一覧（重複統合済み）、ランプ、区間距離、MRI 表示、CSV・画像出力が栃木で動き、区間距離が §14.3 を満たす |
| P2 | DEM 接合、3 系列の標高、勾配・曲率指標、プロファイル表示（地図連動）、条件検索、品質判定が動く |
| P3 | 分類（信頼度・第2候補・根拠）、手動修正と履歴、要確認キューが動き、分類精度が §14.3 を満たす |
| P3+ | 類似検索、2 地点比較、範囲統計比較が動き、統計テストが通る |

### 15.3 拡張性の確認（MVP 時点）

- 範囲を `kanto` に変えて build でき（時間がかかってもよい）、地図表示と検索が破綻しない。
- DEM ソースの追加が `config/dem_sources.yaml` と 1 つのアダプタクラスの追加だけで済む。
- 統計指標の追加がレジストリへの 1 エントリ追加だけで済む。
- 分類ルールの変更が YAML の編集だけで済み、`classifier_version` が変わる。

---

## 16. 開発計画（Claude Code の作業順序）

### 16.1 マイルストーン

| M | 内容 | 主な成果物 | 完了の確認 |
|---|---|---|---|
| M0 | リポジトリの雛形、CI、設定読込、ログ、来歴、DuckDB スキーマ、fixture 作成スクリプト | §8 の構成、`schema.sql`、GitHub Actions | 空のパイプラインが通る |
| M1 | OSM 取得（PBF・Overpass）、グラフ、本線チェーン、施設抽出・種別判定・名称正規化・重複統合 | facility、facility_osm_map | 単体テスト、fixture のゴールデン |
| M2 | ランプ縮約・ID・方向分類、区間・距離定義・経路異常、ルート解析 | ramp、section、section_distance | §14.1 の該当テスト、NEXCO 照合 |
| M3 | 再サンプリング・曲率、DEM 取得（地理院タイル・Copernicus）、標高 3 系列、構造物補正、勾配指標、複合指標、難易度 | sample_point、指標列 | 合成データテスト |
| M4 | 形状分類（特徴量・ルール・信頼度・根拠）、品質フラグとレベル、手動修正の適用 | classification、quality_flag、manual_edit | 正解ラベル照合 |
| M5 | FastAPI 全エンドポイント、QueryDSL、類似検索、2 地点比較、統計比較、出力（表形式・地理形式・Excel）、画像（Matplotlib）、レポート、ジョブ | `/api/*` | API テスト |
| M6 | Web UI：共通部品・トークン、画面02（MRI）、03、04、05 | MapLibre の発光スタイル、プロファイル連動 | E2E 1〜3、10 |
| M7 | Web UI：画面06、07（3 モード、比較トレイ、類似パネル）、08（統計）、09、01 | | E2E 4〜9、11、12 |
| M8 | 実データ検証、性能測定と最適化、オフライン検証、視覚回帰、README・ADR の整備 | `docs/validation/*` | §15.1 の全項目 |

### 16.2 進め方

- 各マイルストーンの最初に、そのマイルストーンのテストを先に書く（合成データで期待値が明確なものは特に）。
- アルゴリズムの数式と検証結果は `docs/algorithms/` に残す。
- UI はモックアップの HTML を開いて見比べながら実装する。モックアップの JS は参考にとどめ、ロジックの移植はしない。

### 16.3 CLAUDE.md に転記する開発規約

```
- 仕様の正: docs/SPEC.md。UI の正: docs/mockup/road_mri_mockup.html（数値はダミー、流用禁止）。
- Python: 3.12 / uv / ruff / mypy strict (src/roadmri) / pytest。関数は型付き、純粋関数を優先。
- 数値パラメータはハードコード禁止。config/*.yaml から読む。
- 単位: 内部はすべて SI（m, rad, 1/m, %は勾配表示のみ）。列名に単位接尾辞（_m, _pct, _kmh）。
- 座標: 保存は WGS84、計算は UTM。投影は geometry/projection.py に集約。
- 乱数は seed 固定。出力に run_id と provenance を必ず付与。
- 外部アクセスは ingest/http.py 経由のみ（レート制限・キャッシュ・プロキシ対応）。テストはオフラインで通すこと。
- 公開 API に大量アクセスしない。Overpass は試験用途のみ。
- DB スキーマ変更は store/migrations/ に追加。manual_edit は追記専用。
- フロント: TypeScript strict / API 型は openapi-typescript で生成 / CDN 禁止（全てバンドル）。
- コミット前: ruff, mypy, pytest -m "not network", npm run lint, npm test を通す。
- 判断に迷ったら SPEC §17 の暫定決定に従い、docs/adr/ に記録。
```

---

## 17. 未決事項と暫定決定

| # | 未決事項 | 暫定決定（これで実装を進める） | 確定の担当・時期 |
|---|---|---|---|
| 1 | 区間距離の標準値の定義 | §4.5.3 の `standard`（分流点と合流点の中点どうし） | NEXCO 照合（M2）の結果を見て確定 |
| 2 | `trunk` を本線に含める範囲 | `motorroad=yes` または `config/routes.yaml` に列挙した ref のみ | P1 レビュー |
| 3 | 形状分類の正解データ | 栃木＋周辺の 30 施設以上に人がラベルを付ける（駆動系 DX 担当） | M4 の前 |
| 4 | 難易度候補値の重み | §4.15.2 の既定値 | 車両性能チームのレビュー後に調整 |
| 5 | プリセットの閾値 | §4.15.3 の既定値 | 同上 |
| 6 | 統計比較の重複要素の既定の扱い | 含めたまま警告 | P3+ レビュー |
| 7 | 統計比較の区間方向 | 片方向（down）だけ | 同上 |
| 8 | 北米の分類体系の差 | ルールファイルに country 別の分岐を用意するだけ（P5 で中身を作る） | P5 |
| 9 | 複数ユーザー運用 | MVP はローカル単一ユーザー。共有は出力ファイルで行う | P4 |
| 10 | PDF レポート | 任意機能（Playwright がある環境のみ） | — |
| 11 | 加速車線長の算出方法 | OSM の `lanes` 変化点から推定。取れない場合は null | M3 |
| 12 | 位置確認図（通常地図）の背景 | 背景タイルを使わず、周辺一般道と地名ラベルで代替（オフライン・登録不要を優先） | — |

---

## 付録 A. 主要な数式

- 三点外接円の半径：A、B、C の 3 点で、a=|BC|、b=|CA|、c=|AB|、面積 S とすると R = abc / (4S)。符号は外積 (B−A)×(C−B) の z 成分の符号。
- 窓平均勾配：g_w(s) = [z_c(s+w/2) − z_c(s−w/2)] / w × 100。z_c は弧長に対して線形補間する。
- Cliff's δ = [#(b_j > a_i) − #(b_j < a_i)] / (n_a n_b)。|δ| < 0.147 negligible、< 0.33 small、< 0.474 medium、それ以上 large。
- Cohen's d = (mean_b − mean_a) / s_pooled、s_pooled = √[((n_a−1)s_a² + (n_b−1)s_b²)/(n_a+n_b−2)]。
- Wilson 区間：(p̂ + z²/2n ± z√(p̂(1−p̂)/n + z²/4n²)) / (1 + z²/n)。
- BH 補正：p 値を昇順 p_(i) に並べ、q_(i) = min_{k≥i}(m p_(k)/k)。
- IC/JCT 類似度：S = (Σ_i w_i exp(−|a_i−b_i|/σ_i) + w_shape·1[shape一致] + w_type·1[type一致]) / (Σ w_i + w_shape + w_type)。既定は w_i=1、w_shape=1.5、w_type=0.8。

## 付録 B. 名称正規化のテストケース（抜粋。40 例をテストに含める）

| 入力 | 正規化名称 |
|---|---|
| 佐野藤岡IC / 佐野藤岡ＩＣ / 佐野藤岡インターチェンジ / 佐野藤岡 I.C. | `佐野藤岡|IC` |
| 栃木都賀JCT / 栃木都賀ジャンクション / 栃木都賀 J.C.T. | `栃木都賀|JCT` |
| 上河内SA / 上河内サービスエリア | `上河内|SA` |
| 上河内スマートIC / 上河内スマートインターチェンジ | `上河内|SIC` |
| 宇都宮IC（上り） | `宇都宮|IC` |
| 西那須野塩原IC / 西那須野ｰ塩原IC（誤記ハイフン） | `西那須野塩原|IC` |
| Sano-Fujioka IC（name:en） | name_en=`sano-fujioka ic` |

## 付録 C. 分類ルール初版（`config/classification_rules.yaml` の構造）

```yaml
version: rules-1.0.0
classes:
  - id: trumpet_ic
    label: トランペット型
    applies_to: [IC]
    required:
      - {feature: mainline_count, op: eq, value: 1}
      - {feature: loop_ramp_count, op: gte, value: 1}
      - {feature: general_road_connections, op: eq, value: 1}
    score:
      - {feature: loop_ramp_count, op: eq, value: 1, weight: 2.0, reason_ja: "ループランプが1本"}
      - {feature: ramp_count, op: between, value: [3, 5], weight: 1.0, reason_ja: "ランプが3〜5本"}
      - {feature: toll_on_general_side, op: eq, value: true, weight: 1.0, reason_ja: "料金所が一般道側にある"}
  - id: diamond
    label: ダイヤモンド型
    applies_to: [IC]
    required:
      - {feature: loop_ramp_count, op: eq, value: 0}
      - {feature: general_road_connections, op: gte, value: 2}
    score:
      - {feature: ramp_count, op: eq, value: 4, weight: 2.0, reason_ja: "ランプ4本が交差道路に直結"}
      - {feature: crossing_road_count, op: eq, value: 1, weight: 1.0, reason_ja: "交差道路が1本"}
  # 変形ダイヤモンド, クローバーリーフ, 部分クローバー, シングルポイント, 平面交差接続, ハーフIC, スマートIC,
  # Y型, T型, JCTトランペット, JCTクローバーリーフ, タービン, スタック, 風車, 複合 を同様に定義
fallback:
  label: 分類不能
  reason_template_ja: "必須条件を満たす分類がありません（{unmet}）"
country_overrides: {US: {}, CA: {}}   # P5
```

## 付録 D. モックアップとの対応

| モックアップ v2 の要素 | 本書の節 |
|---|---|
| 9 画面とナビ | §11.1、§11.3〜§11.11 |
| MRI の発光表現、色分け、レイヤー | §11.4 |
| プロファイルと地図のカーソル連動 | §11.7、§11.8 |
| 難易度候補値の内訳バー | §4.15.2、§11.7 |
| 類似ランプ、類似 IC/JCT（7マス表示） | §4.12、§11.6、§11.9 |
| 条件検索内の比較トレイ・比較パネル | §4.13、§11.9 |
| 範囲統計比較（Mann–Whitney、効果量、箱ひげ、ヒストグラム、散布図、形状構成） | §4.14、§11.10（完成版では Cliff's δ、ブートストラップ CI、BH 補正、ECDF を追加） |
| ダミーデータ（座標近似、乱数生成の値） | 流用禁止（§0） |
