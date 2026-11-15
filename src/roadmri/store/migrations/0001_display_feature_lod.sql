-- 地図の低ズーム用に、連続する表示要素をまとめた版（まとめ数）を区別する列（ADR-0015）。1 が元の要素。
ALTER TABLE display_feature ADD COLUMN IF NOT EXISTS lod INTEGER DEFAULT 1;
