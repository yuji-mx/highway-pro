-- 加速車線の手動確認・修正（ADR-0017）。自動の値（accel_lane_* / accel_start_* など）は上書きせず、別の列に持つ。
-- 修正の履歴は manual_edit（追記専用、field = 'accel_lane'）にあり、ここは最新の有効な修正を反映した値。
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_status VARCHAR;     -- confirmed（自動値を確認）/ corrected（修正）/ not_accel_lane（加速車線ではない）
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_len_m DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_start_lon DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_start_lat DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_end_lon DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_end_lat DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_note VARCHAR;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_edit_id VARCHAR;   -- 反映した manual_edit
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_user VARCHAR;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_manual_at TIMESTAMP;
