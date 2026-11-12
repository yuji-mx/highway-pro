# ADR-0011 手動修正の適用状態

- manual_edit は追記専用（更新・削除しない）。取り消しは `field="__revert__"` の新しい行として記録する。
- 再 build 時の解決結果（対象 ID の継承、孤立修正）は `manual_edit_resolution`（run ごとに作り直す）に保存し、
  manual_edit の status 列は書き換えない。施設は ID 一致 → 正規化名称＋代表点 500 m 以内、ランプは構成 Way の
  Jaccard ≥ 0.5、区間は路線・起終点・方向の一致で継承する。継承できない修正は ORPHANED_EDIT フラグを立て、
  要確認キュー（`/api/review-queue`）に出す。
- API での修正は結果テーブルにも即時反映する（自動値は *_auto 列に保持）。
