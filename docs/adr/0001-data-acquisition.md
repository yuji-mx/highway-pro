# ADR-0001 データ取得とテスト fixture

- 状況：既定の取得方式は Geofabrik の地域 PBF（§4.1.2）。開発環境から Geofabrik と Overpass に接続できなかった。
- 決定：本番経路（`roadmri fetch` → Geofabrik PBF、`--source overpass` → Overpass）はそのまま実装する。
  結合テスト用の実データ fixture は OSM API 0.6 の `map` 呼び出しを 3 秒間隔で 32 回（0.03°×0.02° 程度のタイル）だけ
  実行して取得し、`scripts/make_fixture.py osm` で道路・施設関連だけを抽出した PBF（226 KB）にした。
  DEM は地理院標高タイル（DEM5A z15 / DEM10B z14）の本線・ランプ周辺のみ 143 枚を同梱した。
- 影響：fixture は宇都宮IC・大谷PA・鹿沼IC と日光宇都宮道路の接続部を含む。栃木県全域の検証（§14.3）は
  `@pytest.mark.network` のテストとして用意し、受け入れ時に PBF を取得して実施する。
