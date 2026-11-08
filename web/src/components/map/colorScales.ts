import { convert } from "../../state/units";
/** 色分けのカラースケール（§11.4。stop 値はモックアップと同じ）。 */
export type RGB = [number, number, number];

export const GRADE_STOPS: [number, RGB][] = [
  [-8, [60, 120, 255]],
  [-4, [56, 225, 255]],
  [0, [255, 255, 255]],
  [3, [255, 181, 61]],
  [5.5, [255, 122, 46]],
  [8, [255, 60, 60]],
];
export const RADIUS_STOPS: [number, RGB][] = [
  [Math.log10(30), [255, 60, 60]],
  [Math.log10(150), [255, 122, 46]],
  [Math.log10(600), [255, 181, 61]],
  [Math.log10(3000), [255, 255, 255]],
];
export const ELEV_STOPS: [number, RGB][] = [
  [0, [120, 60, 200]],
  [0.35, [60, 120, 255]],
  [0.7, [56, 225, 255]],
  [1, [255, 255, 255]],
];
export const QUALITY_COLORS: Record<string, string> = { high: "#47e08f", medium: "#ffb53d", low: "#ff5b4f", unusable: "#ff5b4f" };
export const FLOW_COLORS: Record<string, string> = {
  entry: "#38e1ff", exit: "#ff7a2e", jct_link: "#ffd84a", collector_distributor: "#a98bff",
  facility_link: "#47e08f", toll_link: "#ffb53d", two_way: "#7d8b9a",
};
export const SPEED_COLORS: Record<string, string> = { "40": "#ff5b4f", "60": "#ffb53d", "80": "#ffd84a", "100": "#38e1ff", "120": "#f4f7fb" };
export const LANE_COLORS: Record<string, string> = { "1": "#ff7a2e", "2": "#ffd84a", "3": "#38e1ff", "4": "#f4f7fb" };
export const SHAPE_PALETTE = ["#38e1ff", "#ff7a2e", "#a98bff", "#47e08f", "#ffd84a", "#ff5b4f", "#f4f7fb", "#7d8b9a", "#4fa3ff", "#ff9bd2"];

export function interp(stops: [number, RGB][], v: number): RGB {
  if (!Number.isFinite(v)) return [125, 139, 154];
  if (v <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (v >= last[0]) return last[1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [a, ca] = stops[i];
    const [b, cb] = stops[i + 1];
    if (v >= a && v <= b) {
      const t = (v - a) / (b - a);
      return [0, 1, 2].map((k) => Math.round(ca[k] + (cb[k] - ca[k]) * t)) as RGB;
    }
  }
  return last[1];
}

export const rgb = (c: RGB): string => `rgb(${c[0]},${c[1]},${c[2]})`;

export function gradeColor(g: number | null | undefined): string {
  return g == null ? "#7d8b9a" : rgb(interp(GRADE_STOPS, g));
}
export function radiusColor(r: number | null | undefined): string {
  return r == null ? "#ffffff" : rgb(interp(RADIUS_STOPS, Math.log10(Math.max(1, r))));
}
export function elevColor(z: number | null | undefined, lo: number, hi: number): string {
  if (z == null) return "#7d8b9a";
  return rgb(interp(ELEV_STOPS, (z - lo) / Math.max(1e-6, hi - lo)));
}

/** MapLibre の interpolate 式（勾配）。 */
export function gradeExpression(prop = "grade"): any {
  return ["case", ["==", ["get", prop], null], "#7d8b9a",
    ["interpolate", ["linear"], ["get", prop], ...GRADE_STOPS.flatMap(([v, c]) => [v, rgb(c)])]];
}
export function radiusExpression(prop = "curvature"): any {
  return ["case", ["any", ["==", ["get", prop], null], ["==", ["get", prop], 0]], "#ffffff",
    ["interpolate", ["linear"], ["log10", ["/", 1, ["max", ["abs", ["get", prop]], 0.0001]]],
      ...RADIUS_STOPS.flatMap(([v, c]) => [v, rgb(c)])]];
}
export function elevExpression(lo: number, hi: number, prop = "z"): any {
  return ["case", ["==", ["get", prop], null], "#7d8b9a",
    ["interpolate", ["linear"], ["get", prop], ...ELEV_STOPS.flatMap(([v, c]) => [lo + v * (hi - lo), rgb(c)])]];
}
export function matchExpression(prop: string, colors: Record<string, string>, fallback: string): any {
  const pairs = Object.entries(colors).flatMap(([k, v]) => [k, v]);
  return ["match", ["to-string", ["get", prop]], ...pairs, fallback];
}

export interface LegendItem {
  label: string;
  color: string;
}

/** 凡例（色だけで意味を伝えないよう、数値・名称を併記）。 */
export function legendFor(metric: string, zr: [number, number] = [30, 550], shapes: string[] = []): LegendItem[] {
  switch (metric) {
    case "grade":
      return [-8, -4, 0, 3, 5.5, 8].map((v) => ({ label: `${v > 0 ? "+" : ""}${v}%`, color: gradeColor(v) }));
    case "radius":
      return [30, 150, 600, 3000].map((r) => {
        const [v, u] = convert(r, " m");
        return { label: `R${Math.round(v)}${u}`, color: radiusColor(r) };
      });
    case "elevation":
      return [0, 0.35, 0.7, 1].map((t) => {
        const [v, u] = convert(zr[0] + t * (zr[1] - zr[0]), " m");
        return { label: `${Math.round(v)}${u}`, color: rgb(interp(ELEV_STOPS, t)) };
      });
    case "quality":
      return [["高", "high"], ["中", "medium"], ["低", "low"]].map(([l, k]) => ({ label: l, color: QUALITY_COLORS[k] }));
    case "flow":
      return Object.entries(FLOW_COLORS).map(([k, c]) => ({ label: k, color: c }));
    case "maxspeed":
      return Object.entries(SPEED_COLORS).map(([k, c]) => {
        const [v, u] = convert(Number(k), " km/h");
        return { label: `${Math.round(v)}${u}`, color: c };
      });
    case "lanes":
      return Object.entries(LANE_COLORS).map(([k, c]) => ({ label: `${k} 車線`, color: c }));
    case "shape":
      return shapes.map((s, i) => ({ label: s, color: SHAPE_PALETTE[i % SHAPE_PALETTE.length] }));
    default:
      return [
        { label: "本線", color: "#eef3f9" },
        { label: "ランプ", color: "#38e1ff" },
        { label: "JCT連絡", color: "#ffd84a" },
        { label: "一般道", color: "rgba(110,125,140,.6)" },
        { label: "解析対象", color: "#ff7a2e" },
      ];
  }
}
