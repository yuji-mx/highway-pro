# 統計（§4.14、付録 A）

- 連続量：記述統計、両側 Mann–Whitney U（n ≤ 20 かつ同順位なしは正確検定、それ以外は連続性補正付き正規近似。scipy）、
  Cliff's δ（B が大きいほど正）、Cohen's d、中央値差の 95 % パーセンタイル・ブートストラップ CI（2,000 回、seed 42。大標本は ADR-0009）。
- 割合：Wilson 95 % CI、2 標本比率の χ²（期待度数 < 5 は Fisher の正確検定）。
- カテゴリ：χ²（期待度数 < 5 のセルが 20 % 超なら Fisher–Freeman–Halton をモンテカルロ 10,000 回、seed 固定で近似）。
- 密度：区間延長で正規化し、ポアソン率の比の検定（条件付き二項検定）。
- 多重比較：表示する全指標の p に Benjamini–Hochberg（q 値）。n < 10 は参考値として自動抽出から除外。
- 主な差異：|δ| ≥ 0.33 または q < 0.10 を |δ| の降順に最大 5 件。検索条件の該当率の比較文を必ず付ける。

検証（tests/unit/test_stats.py）：hypothesis で Cliff's δ（総当たり）、Mann–Whitney（scipy と完全一致）、BH（`scipy.stats.false_discovery_control`）、
Cohen's d を照合。Wilson の既知値、Fisher/χ² の scipy 一致、ブートストラップの再現性、重複処理 3 方式の件数。
