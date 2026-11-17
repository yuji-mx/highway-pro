-- 加速車線の判定結果（ADR-0016）。accel_lane_len_m は測定できたものだけ値が入り、除外は理由を残す。
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_lane_status VARCHAR;      -- measured / excluded（対象外の種類は NULL）
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_lane_reason VARCHAR;      -- 除外理由のコード
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_lane_method VARCHAR;      -- geometry / lanes / geometry+lanes
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_lane_confidence VARCHAR;  -- high / medium / low
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_lane_notes VARCHAR;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_parallel_m DOUBLE;        -- 始点〜合流ノード（ランプ上の並走部）
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_added_lane_m DOUBLE;      -- 合流ノード〜終点（本線の増えた車線）
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_start_lon DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_start_lat DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_end_lon DOUBLE;
ALTER TABLE ramp ADD COLUMN IF NOT EXISTS accel_end_lat DOUBLE;
