# ROAD MRI — 高速道路ネットワーク解析・可視化システム

OpenStreetMap と公的 DEM（国土地理院 標高タイル等）から、高速道路の IC・JCT・ランプ・区間を抽出して DuckDB に蓄積し、
距離・曲率・標高・勾配・形状分類を解析する。背景地図なしの「道路 MRI」表示、条件検索、類似地点検索、2 地点比較、
範囲統計比較、データ・画像・レポート出力を、ローカル PC だけ（取得後はオフライン、API キー・登録不要）で行う。

仕様：[docs/SPEC.md](docs/SPEC.md)。設計判断：[docs/adr/](docs/adr/)。アルゴリズム：[docs/algorithms/](docs/algorithms/)。
検証記録：[docs/validation/](docs/validation/)。

## セットアップ（4 手順）

前提：Python 3.12 を入れられる [uv](https://docs.astral.sh/uv/)、Node.js 20 以上。管理者権限は不要。
社内ネットワークでは `HTTPS_PROXY` と `REQUESTS_CA_BUNDLE`（または `config/settings.yaml` の `http.ca_bundle`）を設定する。

```bash
uv sync
```

```bash
cd web && npm ci && npm run build && cd ..
```

```bash
uv run roadmri fetch --range tochigi
```

```bash
uv run roadmri build --range tochigi
```

```bash
uv run roadmri serve
```

`roadmri serve` が API と UI を 1 プロセスで起動し、`http://127.0.0.1:8765` を開く（127.0.0.1 にだけバインド）。

ネットワークなしで試すときは、同梱の実データ fixture（宇都宮IC〜鹿沼IC 周辺、OSM＋地理院 DEM タイル）を使う：

```bash
uv run roadmri build --range tochigi_small --offline
```

合成ネットワーク（典型的な IC/JCT 形状）は `--range synthetic`。

## 主なコマンド

| コマンド | 内容 |
|---|---|
| `roadmri fetch --range <id> [--source overpass] [--force]` | OSM（Geofabrik PBF、または小規模試験用の Overpass）を取得してキャッシュ。同じものは再取得しない |
| `roadmri build --range <id> [--from-step ramps] [--to-step classify] [--offline]` | パイプライン（ingest → network → facilities → ramps → sections → metrics → classify → quality → edits → publish）。DEM タイルは必要な分だけ取得してキャッシュ |
| `roadmri serve [--port 8765] [--no-browser]` | API と UI |
| `roadmri search --query spec_example` | QueryDSL（JSON ファイルまたはプリセット名）で検索 |
| `roadmri export --dataset ramps --format gpkg --query spec_example` | CSV / xlsx / GeoJSON / GeoPackage / Parquet / SQLite（ZIP、provenance.json 同梱） |
| `roadmri images --query spec_example --format png --size 1920x1080` | 道路 MRI 画像の一括生成（ZIP） |
| `roadmri report --query spec_example` | 画像レポート（自己完結 HTML ＋画像） |
| `roadmri stats --a a.json --b b.json --out stats.csv` | 2 範囲の統計比較（RangeSpec JSON） |
| `roadmri regions` | 地域プリセット一覧（`config/regions/*.yaml`） |

数値パラメータはすべて `config/*.yaml`（補間間隔・平滑化・曲率・勾配窓・閾値・分類ルール・難易度の重み・プリセット・統計設定・DEM ソース）。

## テスト

```bash
uv run pytest
```

```bash
uv run ruff check src tests scripts && uv run mypy
```

```bash
cd web && npm run lint && npm test && npx playwright test
```

`pytest` は既定で `-m "not network"`（すべてオフラインで通る。`--disable-socket --allow-unix-socket` でも確認済み）。
実データの受け入れ検証は `pytest -m network`（docs/validation/tochigi.md）。

## 出典表示とライセンス

- 地図データ：© OpenStreetMap contributors（ODbL 1.0）。**本システムが作るデータベースは OSM の派生データベースです。
  社外に公開・配布する場合は ODbL の継承義務（同じライセンスでの提供）が生じます。社内利用の範囲では問題ありません。**
- 標高：出典：国土地理院（標高タイル）。Copernicus DEM を使った場合は所定のクレジット（config/dem_sources.yaml）。
- 出典表示文は `/api/meta` から取得し、地図右下・画像・レポート・出力ファイルの来歴（provenance.json）に入る。
- 同梱フォント：Noto Sans JP（SIL OFL 1.1、`src/roadmri/export/fonts/OFL.txt`）。Web UI のフォントは npm（@fontsource）からバンドル。

## 現状の制約

- UI モックアップ（docs/mockup/road_mri_mockup.html）は未提供のため、SPEC §11 の文章仕様とデザイントークンで実装した。
- 栃木県全域での実データ検証（NEXCO 照合、分類精度）は未実施（docs/validation/tochigi.md）。
- P4（全国・差分更新・PMTiles）、P5（北米）、P6（実走データ）は設計・インターフェースのみ。
