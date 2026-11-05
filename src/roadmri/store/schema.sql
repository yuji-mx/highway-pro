-- ROAD MRI DuckDB スキーマ（§9）。geometry は WGS84 の WKB（BLOB）。spatial 拡張はオフライン導入できないため使わない（ADR-0002）。
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER, applied_at TIMESTAMP);

CREATE TABLE IF NOT EXISTS facility (
  facility_id VARCHAR PRIMARY KEY, country VARCHAR, admin_code VARCHAR, name VARCHAR, name_en VARCHAR,
  normalized_name VARCHAR, name_variants VARCHAR[], facility_type VARCHAR, is_half_ic BOOLEAN,
  is_jct_combined BOOLEAN, parent_facility_id VARCHAR, exit_number VARCHAR, route_refs VARCHAR[],
  destinations JSON, lon DOUBLE, lat DOUBLE, geom BLOB, hull BLOB, ramp_count INTEGER, loop_ramp_count INTEGER,
  connection_directions INTEGER, shape_class VARCHAR, shape_confidence DOUBLE, shape_class_auto VARCHAR,
  shape_confidence_auto DOUBLE, shape_class_2 VARCHAR, shape_confidence_2 DOUBLE, classification_reasons JSON,
  unclassifiable_reason VARCHAR, review_status VARCHAR, quality_level VARCHAR, osm_timestamp TIMESTAMP,
  merge_reason JSON, in_range BOOLEAN, excluded BOOLEAN, facility_type_auto VARCHAR, name_auto VARCHAR,
  mean_ramp_length_m DOUBLE, min_radius_m DOUBLE, grade_up_max_w100 DOUBLE, max_elev_diff_m DOUBLE,
  design_speed_kmh DOUBLE, z_dem DOUBLE, osm_ids JSON, comment VARCHAR, run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS facility_osm_map (
  facility_id VARCHAR, osm_type VARCHAR, osm_id BIGINT, role VARCHAR, carriageway VARCHAR
);
CREATE TABLE IF NOT EXISTS ramp (
  ramp_id VARCHAR PRIMARY KEY, facility_id VARCHAR, related_facility_ids VARCHAR[], seq INTEGER, flow VARCHAR,
  carriageway VARCHAR, branch_side VARCHAR, merge_diverge VARCHAR, from_ref JSON, to_ref JSON, geom BLOB,
  length_m DOUBLE, lanes INTEGER, maxspeed_kmh INTEGER, is_loop BOOLEAN, min_radius_m DOUBLE, mean_radius_m DOUBLE,
  max_curvature DOUBLE, mean_curvature DOUBLE, curvature_rate_max DOUBLE, curve_length_m DOUBLE,
  straight_length_m DOUBLE, grade_inst_max DOUBLE, grade_inst_min DOUBLE,
  grade_up_max_w10 DOUBLE, grade_up_max_w50 DOUBLE, grade_up_max_w100 DOUBLE, grade_up_max_w500 DOUBLE,
  grade_down_max_w10 DOUBLE, grade_down_max_w50 DOUBLE, grade_down_max_w100 DOUBLE, grade_down_max_w500 DOUBLE,
  grade_mean DOUBLE, elev_gain_m DOUBLE, elev_loss_m DOUBLE, elev_diff_m DOUBLE, abs_elev_diff_m DOUBLE,
  z_max DOUBLE, z_min DOUBLE, cont_up_len_m DOUBLE, cont_down_len_m DOUBLE, tunnel_len_m DOUBLE, bridge_len_m DOUBLE,
  viaduct_len_m DOUBLE, accel_lane_len_m DOUBLE, pre_merge_grade DOUBLE, end_grade DOUBLE, composite JSON,
  difficulty DOUBLE, difficulty_components JSON, window_truncated BOOLEAN, osm_quality VARCHAR, dem_quality VARCHAR,
  quality_level VARCHAR, drive_data_available BOOLEAN, excluded BOOLEAN, flow_auto VARCHAR, facility_id_auto VARCHAR,
  shape_class VARCHAR, curve_count INTEGER, turn_deg DOUBLE, dem_low_conf_ratio DOUBLE, curves JSON, run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS ramp_way_map (ramp_id VARCHAR, way_id BIGINT, "order" INTEGER, reversed BOOLEAN);
CREATE TABLE IF NOT EXISTS section (
  section_id VARCHAR PRIMARY KEY, route_ref VARCHAR, from_facility_id VARCHAR, to_facility_id VARCHAR,
  direction VARCHAR, dist_standard_m DOUBLE, geom BLOB, way_ids BIGINT[], grade_mean DOUBLE,
  grade_up_max_w100 DOUBLE, grade_down_max_w100 DOUBLE, grade_up_max_w1000 DOUBLE, elev_gain_m DOUBLE,
  elev_loss_m DOUBLE, z_min DOUBLE, z_max DOUBLE, min_radius_m DOUBLE, tunnel_len_m DOUBLE, bridge_len_m DOUBLE,
  cont_up_len_m DOUBLE, gain_per_km DOUBLE, tunnel_ratio DOUBLE, passes_non_motorway BOOLEAN,
  anomaly_flags VARCHAR[], quality_level VARCHAR, excluded BOOLEAN, adopted_definition VARCHAR,
  drive_data_available BOOLEAN, run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS section_distance (
  section_id VARCHAR, definition VARCHAR, distance_m DOUBLE, from_point BLOB, to_point BLOB, note VARCHAR
);
CREATE TABLE IF NOT EXISTS sample_point (
  element_type VARCHAR, element_id VARCHAR, section_id VARCHAR, seq INTEGER, s_m DOUBLE, lon DOUBLE, lat DOUBLE,
  x DOUBLE, y DOUBLE, z_dem_raw DOUBLE, z_smoothed DOUBLE, z_corrected DOUBLE, dem_source VARCHAR,
  dem_resolution_m DOUBLE, grade_inst DOUBLE, grade_w100 DOUBLE, curvature DOUBLE, structure VARCHAR,
  layer INTEGER, low_confidence BOOLEAN
);
CREATE TABLE IF NOT EXISTS classification_feature (
  facility_id VARCHAR, run_id VARCHAR, features JSON, classifier_version VARCHAR
);
CREATE TABLE IF NOT EXISTS quality_flag (
  flag_id VARCHAR, target_type VARCHAR, target_id VARCHAR, code VARCHAR, severity VARCHAR, message_ja VARCHAR,
  detail JSON, run_id VARCHAR
);
CREATE TABLE IF NOT EXISTS display_feature (
  layer VARCHAR, feature_id VARCHAR, props JSON, geom BLOB, minx DOUBLE, miny DOUBLE, maxx DOUBLE, maxy DOUBLE
);
CREATE TABLE IF NOT EXISTS run_metadata (
  run_id VARCHAR, created_at TIMESTAMP, git_commit VARCHAR, range_spec JSON, params JSON, config_hash VARCHAR,
  classifier_version VARCHAR, manual_edit_count INTEGER, last_edit_id VARCHAR, status VARCHAR, duration_s DOUBLE,
  region_id VARCHAR, provenance JSON
);
CREATE TABLE IF NOT EXISTS dataset_source (
  source_id VARCHAR, kind VARCHAR, name VARCHAR, url VARCHAR, retrieved_at TIMESTAMP, sha256 VARCHAR,
  license VARCHAR, attribution VARCHAR, osm_replication_seq BIGINT, osm_timestamp TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dem_dataset (
  dem_id VARCHAR, name VARCHAR, resolution_m DOUBLE, crs VARCHAR, vertical_datum VARCHAR, retrieved_at TIMESTAMP,
  nominal_error_m DOUBLE, missing_ratio DOUBLE, coverage BLOB, source_id VARCHAR, attribution VARCHAR
);
-- 以下は再 build でも消さない（追記・保存系）
CREATE TABLE IF NOT EXISTS manual_edit (
  edit_id VARCHAR PRIMARY KEY, target_type VARCHAR, target_id VARCHAR, field VARCHAR, old_value JSON,
  new_value JSON, comment VARCHAR, user_name VARCHAR, created_at TIMESTAMP, osm_anchor JSON, superseded_by VARCHAR,
  status VARCHAR
);
CREATE TABLE IF NOT EXISTS manual_edit_resolution (
  edit_id VARCHAR, run_id VARCHAR, status VARCHAR, resolved_target_id VARCHAR
);
CREATE TABLE IF NOT EXISTS change_event (
  event_id VARCHAR, base_run_id VARCHAR, new_run_id VARCHAR, kind VARCHAR, target_id VARCHAR, detail JSON
);
CREATE TABLE IF NOT EXISTS official_reference (
  ref_id VARCHAR, target_type VARCHAR, target_id VARCHAR, source_name VARCHAR, url VARCHAR, retrieved_at TIMESTAMP,
  field VARCHAR, value VARCHAR, adopted_source VARCHAR
);
CREATE TABLE IF NOT EXISTS saved_query (query_id VARCHAR PRIMARY KEY, name VARCHAR, target VARCHAR, query JSON,
  created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS saved_range (range_id VARCHAR PRIMARY KEY, name VARCHAR, spec JSON, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS job (
  job_id VARCHAR PRIMARY KEY, kind VARCHAR, params JSON, status VARCHAR, progress DOUBLE, message VARCHAR,
  result_path VARCHAR, error VARCHAR, created_at TIMESTAMP, finished_at TIMESTAMP
);
