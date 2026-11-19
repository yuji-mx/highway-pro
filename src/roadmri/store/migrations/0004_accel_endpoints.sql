-- 加速車線の端点ごとの根拠と、判定保留（ADR-0020）。以前の DB では空（画面・API では「未評価」）。
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_start_method VARCHAR;      -- osm_parallel / osm_merge_node
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_start_confidence VARCHAR;  -- high / medium / low
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_start_pending VARCHAR;     -- 始点を確定できない理由
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_end_method VARCHAR;        -- osm_lane_drop / osm_merge_node
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_end_confidence VARCHAR;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_end_pending VARCHAR;       -- 終点を確定できない理由
-- 手動の確認：対象外の理由（lane_addition / auxiliary_to_exit / other）と、判定保留の端点・理由（JSON）
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_reason VARCHAR;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_pending VARCHAR;
