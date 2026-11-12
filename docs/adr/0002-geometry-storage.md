# ADR-0002 DuckDB での geometry 保存形式

- 状況：§9 は DuckDB の `GEOMETRY` 型を想定している。spatial 拡張は初回にネットワークからの導入が必要で、
  社内 PC・オフライン環境（§5.2、§13.3）では導入できない場合がある。
- 決定：geometry は WGS84 の WKB を `BLOB` 列に保存する。表示用レイヤーは `display_feature` に WKB と bbox 列を持たせ、
  bbox 条件で絞り込む。出力（GeoPackage・GeoParquet・GeoJSON）では geopandas で通常の geometry に変換する。
- 影響：spatial 拡張が使える環境では `ST_GeomFromWKB(geom)` で同じデータを空間演算に使える。
