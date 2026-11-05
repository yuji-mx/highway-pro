# ROAD MRI 開発規約

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

## よく使うコマンド

```bash
uv sync                                   # Python 依存
(cd web && npm ci && npm run build)       # Web UI のビルド（FastAPI が web/dist を配信）
uv run roadmri build --range tochigi_small --offline   # 同梱 fixture で全パイプライン
uv run roadmri serve                      # http://127.0.0.1:8765
uv run pytest                             # オフラインの全テスト（network マーカーは除外）
uv run ruff check src tests scripts && uv run mypy
(cd web && npm run lint && npm test && npx playwright test)
```

## 構成メモ

- パイプライン：`src/roadmri/pipeline.py`（段＝ingest → network → facilities → ramps → sections → metrics → classify → quality → edits → publish）。
  段ごとに `data/interim/<region>/<step>.pkl` を保存し `--from-step` で再開できる。
- テスト fixture：`tests/fixtures/tochigi_small.osm.pbf`（宇都宮IC〜鹿沼IC、OSM API から取得して道路だけ抽出）、
  `tests/fixtures/dem/gsi/`（地理院 DEM5A/DEM10B タイル）、`tests/fixtures/synthetic/`（典型形状の合成ネットワーク）。
  ゴールデンは `scripts/make_golden.py` で再生成する（意図した変更のときだけ）。
