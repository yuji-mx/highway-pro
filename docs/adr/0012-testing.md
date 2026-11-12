# ADR-0012 テスト構成

- Python：`tests/unit`（合成データでアルゴリズムを検証）、`tests/integration`（実データ fixture と合成ネットワークの
  全パイプライン、ゴールデン照合、手動修正、ID 継承、再現性、--from-step）、`tests/api`（全エンドポイント、Problem Details、
  OpenAPI のパス一覧スナップショット、応答時間）、`tests/perf`（pytest-benchmark）。既定は `-m "not network"`。
  `pytest --disable-socket --allow-unix-socket` でもすべて通る（オフライン動作の確認）。
- Web：Vitest（カラースケール、QueryDSL→日本語文、比較トレイ、範囲ストア）、Playwright（§14.5 の 12 シナリオ、
  外部リクエスト 0 件、主要 9 画面の視覚回帰。基準画像は OS ごとに `--update-snapshots` で作る）。
- 形状分類の実データ精度（§14.3、30 施設以上の正解ラベル）は §17-3 の担当が付ける正解ラベル待ち。
