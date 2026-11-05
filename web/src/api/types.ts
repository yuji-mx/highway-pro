/** 主要な API 型（OpenAPI から生成した schema.d.ts を補う手書き型）。 */
export type Quality = "high" | "medium" | "low" | "unusable";

export interface Facility {
  facility_id: string;
  name: string | null;
  name_en?: string | null;
  normalized_name: string | null;
  name_variants?: string[];
  facility_type: string;
  route_refs: string[];
  exit_number: string | null;
  lon: number;
  lat: number;
  shape_class: string;
  shape_confidence: number;
  shape_class_auto?: string;
  shape_confidence_auto?: number;
  shape_class_2: string | null;
  shape_confidence_2: number | null;
  ramp_count: number;
  loop_ramp_count: number;
  connection_directions: number;
  quality_level: Quality;
  review_status: string;
  mean_ramp_length_m: number | null;
  min_radius_m: number | null;
  grade_up_max_w100: number | null;
  max_elev_diff_m: number | null;
  is_half_ic?: boolean;
  is_jct_combined?: boolean;
  in_range?: boolean;
  [k: string]: any;
}

export interface Ramp {
  ramp_id: string;
  facility_id: string;
  flow: string;
  carriageway: string;
  length_m: number;
  min_radius_m: number | null;
  grade_up_max_w100: number | null;
  grade_down_max_w100: number | null;
  elev_diff_m: number | null;
  z_max: number | null;
  difficulty: number | null;
  difficulty_components?: { components: Record<string, number>; contributions: Record<string, number>; weights: Record<string, number> };
  dem_quality: Quality;
  osm_quality: Quality;
  quality_level: Quality;
  [k: string]: any;
}

export interface SamplePoint {
  s_m: number;
  lon: number;
  lat: number;
  z_dem_raw: number | null;
  z_smoothed: number | null;
  z_corrected: number | null;
  grade_inst: number | null;
  grade_w100: number | null;
  curvature: number | null;
  structure: string;
  low_confidence: boolean;
}

export interface QueryDSL {
  target: "ramp" | "facility" | "section";
  where: { all?: any[]; any?: any[] };
  range?: RangeSpec | null;
  sort?: { field: string; dir: "asc" | "desc" }[];
  limit?: number;
}

export interface RangeSpec {
  type: string;
  label?: string;
  route_refs?: string[];
  admin_codes?: string[];
  bbox?: number[];
  circle?: { lon: number; lat: number; radius_km: number };
  polygon?: any;
  shape_classes?: string[];
  query?: QueryDSL;
  facility_ids?: string[];
  members?: RangeSpec[];
  buffer_km?: number;
}

/** OpenAPI から生成した型（`npm run gen:api` で更新）。 */
export type { paths as ApiPaths } from "./schema";
