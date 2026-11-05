/** QueryDSL → 日本語文（バックエンドの to_text と同じ規則。§4.11.5）。 */
import type { QueryDSL } from "../../api/types";

export const FIELD_LABELS: Record<string, [string, string]> = {
  flow: ["区分", ""], length_m: ["長さ", " m"], min_radius_m: ["最小R", " m"], max_curvature: ["最大曲率", " 1/m"],
  curve_length_m: ["カーブ長", " m"], grade_mean: ["平均勾配", "%"], grade_inst_max: ["最大瞬間勾配", "%"],
  cont_up_len_m: ["連続上り距離", " m"], elev_diff_m: ["標高差", " m"], abs_elev_diff_m: ["|標高差|", " m"],
  z_max: ["最高標高", " m"], lanes: ["車線数", ""], maxspeed_kmh: ["制限速度", " km/h"], accel_lane_len_m: ["加速車線長", " m"],
  pre_merge_grade: ["合流直前勾配", "%"], end_grade: ["終端勾配", "%"], difficulty: ["難易度候補値", ""],
  dem_quality: ["DEM品質", ""], osm_quality: ["OSM品質", ""], quality_level: ["品質", ""], shape_class: ["IC/JCT形状", ""],
  facility_type: ["施設種別", ""], route_ref: ["路線", ""], name: ["施設名", ""], shape_confidence: ["信頼度", ""],
  ramp_count: ["ランプ数", ""], loop_ramp_count: ["ループ数", ""], has_loop: ["ループあり", ""], is_half_ic: ["ハーフIC", ""],
  mean_ramp_length_m: ["平均ランプ長", " m"], max_elev_diff_m: ["最大高低差", " m"], connection_directions: ["接続方向数", ""],
  review_status: ["確認状態", ""], dist_standard_m: ["距離（標準）", " m"], gain_per_km: ["累積上昇/km", " m/km"],
  elev_gain_m: ["累積上昇", " m"], tunnel_len_m: ["トンネル延長", " m"], bridge_len_m: ["橋梁延長", " m"],
  grade_up_max_w1000: ["最大上り(1000m)", "%"], is_loop: ["ループ", ""], small_r_long: ["小Rかつ長いランプ", ""],
  direction: ["方向", ""],
};
for (const w of [10, 50, 100, 500]) {
  FIELD_LABELS[`grade_up_max_w${w}`] = [`${w}m上り`, "%"];
  FIELD_LABELS[`grade_down_max_w${w}`] = [`${w}m下り`, "%"];
}
const TARGET_JA: Record<string, string> = { ramp: "ランプ", facility: "IC/JCT", section: "区間" };
const OP_JA: Record<string, string> = { eq: "＝", ne: "≠", gte: "≥", lte: "≤", gt: "＞", lt: "＜", in: "∈" };
const QJ: Record<string, string> = { high: "高", medium: "中", low: "低", unusable: "使用不可" };
const FLOW_JA: Record<string, string> = {
  entry: "流入", exit: "流出", jct_link: "JCT連絡", collector_distributor: "集散路", facility_link: "SA・PA連絡",
  toll_link: "料金所連絡", two_way: "双方向",
};
const ORDINAL = new Set(["dem_quality", "osm_quality", "quality_level"]);

function val(field: string, v: any): string {
  if (Array.isArray(v)) return v.map((x) => val(field, x)).join("・");
  if (field === "flow") return FLOW_JA[v] ?? String(v);
  if (ORDINAL.has(field)) return QJ[v] ?? String(v);
  if (typeof v === "boolean") return v ? "はい" : "いいえ";
  const unit = FIELD_LABELS[field]?.[1] ?? "";
  return `${v}${unit}`;
}

export function condText(c: any): string {
  if (c.all || c.any) return `（${groupText(c)}）`;
  const label = FIELD_LABELS[c.field]?.[0] ?? c.field;
  const op = c.op ?? "eq";
  if (op === "between") return `${label} ${val(c.field, c.value[0])}〜${val(c.field, c.value[1])}`;
  if (op === "exists") return `${label}${c.value === false ? "なし" : "あり"}`;
  if (op === "contains") return `${label}に「${c.value}」を含む`;
  if (ORDINAL.has(c.field) && op === "gte") return `${label} ${val(c.field, c.value)}以上`;
  return `${label} ${OP_JA[op] ?? op} ${val(c.field, c.value)}`;
}

export function groupText(w: { all?: any[]; any?: any[] }): string {
  const parts: string[] = [];
  const alls = (w.all ?? []).map(condText);
  const anys = (w.any ?? []).map(condText);
  if (alls.length) parts.push(alls.join(" かつ "));
  if (anys.length) parts.push(alls.length ? `（${anys.join(" または ")}）` : anys.join(" または "));
  return parts.length ? parts.join(" かつ ") : "条件なし";
}

export function toText(q: QueryDSL): string {
  let s = `${TARGET_JA[q.target] ?? q.target}：${groupText(q.where ?? {})}`;
  const sorts = (q.sort ?? []).map((so) => `${FIELD_LABELS[so.field]?.[0] ?? so.field}の${so.dir === "desc" ? "降順" : "昇順"}`);
  if (sorts.length) s += `、${sorts.join("・")}`;
  if (q.limit) s += `、上限 ${q.limit} 件`;
  return s;
}

export function usesInstantGrade(q: QueryDSL): boolean {
  const walk = (w: any): boolean => [...(w.all ?? []), ...(w.any ?? [])].some((c: any) => (c.all || c.any ? walk(c) : c.field === "grade_inst_max"));
  return walk(q.where ?? {});
}

export const b64 = {
  enc: (o: unknown) => btoa(unescape(encodeURIComponent(JSON.stringify(o)))),
  dec: <T,>(s: string): T | null => {
    try {
      return JSON.parse(decodeURIComponent(escape(atob(s)))) as T;
    } catch {
      return null;
    }
  },
};
