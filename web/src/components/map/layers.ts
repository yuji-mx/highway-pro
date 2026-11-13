/** MapLibre レイヤー定義（§11.4。加算合成がないため、重ねた線レイヤーと blur で発光を近似する）。 */
import type { FeatureCollection } from "geojson";
import { deserialize } from "flatgeobuf/lib/mjs/geojson.js";
import { api } from "../../api/client";
import {
  FLOW_COLORS, LANE_COLORS, QUALITY_COLORS, SHAPE_PALETTE, SPEED_COLORS, elevExpression, gradeExpression,
  matchExpression, radiusExpression,
} from "./colorScales";

export const DATA_LAYERS = ["mainline", "ramps", "general", "facilities", "tolls", "structures"] as const;
export type DataLayer = (typeof DATA_LAYERS)[number];

const cache = new Map<string, Promise<FeatureCollection>>();

/** 表示用ベクタ（FlatGeobuf）を取得して GeoJSON に変換する。 */
export function loadLayer(name: DataLayer, runId = ""): Promise<FeatureCollection> {
  const key = `${name}:${runId}`;
  if (!cache.has(key)) {
    cache.set(
      key,
      api
        .bytes(`/layers/${name}.fgb`)
        .then((bytes) => (bytes.length ? (deserialize(bytes) as unknown as FeatureCollection) : { type: "FeatureCollection", features: [] }))
        .catch((e) => {
          cache.delete(key);
          throw e;
        }) as Promise<FeatureCollection>,
    );
  }
  return cache.get(key)!;
}

export function clearLayerCache(): void {
  cache.clear();
}

export function lineColor(metric: string, kind: "mainline" | "ramps", zr: [number, number], shapes: string[]): any {
  switch (metric) {
    case "grade":
      return gradeExpression();
    case "radius":
      return radiusExpression();
    case "elevation":
      return elevExpression(zr[0], zr[1]);
    case "maxspeed":
      return matchExpression("maxspeed", SPEED_COLORS, "#7d8b9a");
    case "lanes":
      return matchExpression("lanes", LANE_COLORS, "#7d8b9a");
    case "quality":
      return kind === "ramps"
        ? matchExpression("quality", QUALITY_COLORS, "#7d8b9a")
        : ["case", ["==", ["get", "low_conf"], true], QUALITY_COLORS.low, QUALITY_COLORS.high];
    case "flow":
      return kind === "ramps" ? matchExpression("flow", FLOW_COLORS, "#7d8b9a") : "#eef3f9";
    case "shape":
      return kind === "ramps"
        ? matchExpression("shape_class", Object.fromEntries(shapes.map((s, i) => [s, SHAPE_PALETTE[i % SHAPE_PALETTE.length]])), "#7d8b9a")
        : "#eef3f9";
    default:
      return kind === "ramps" ? ["case", ["==", ["get", "flow"], "jct_link"], "#ffd84a", "#38e1ff"] : "#eef3f9";
  }
}

const GLOW: [number, number, number][] = [
  [7, 0.1, 4],
  [3, 0.22, 0],
  [1.2, 1.0, 0],
];
/** 発光の重ね描き（GLOW の先頭 2 段）。最後の 1 段が本体の線。 */
export const GLOW_LEVELS = GLOW.length - 1;

export interface StyleOpts {
  metric: string;
  glow: boolean;
  widthByCurvature: boolean;
  zr: [number, number];
  shapes: string[];
}

function widthScale(src: "mainline" | "ramps", o: StyleOpts): any {
  return src === "ramps" && o.widthByCurvature
    ? ["interpolate", ["linear"], ["abs", ["coalesce", ["get", "curvature"], 0]], 0, 1, 0.005, 1.5, 0.02, 3]
    : 1;
}

/** 線レイヤーの段 i（0〜2）の色と太さ。作成時と、指標・太さの切替時（setPaintProperty）の両方で使う。 */
export function linePaint(src: "mainline" | "ramps", i: number, o: StyleOpts): { color: any; width: any } {
  const scale = widthScale(src, o);
  const w = GLOW[i][0];
  return { color: lineColor(o.metric, src, o.zr, o.shapes), width: scale === 1 ? w : ["*", w, scale] };
}

/** 本線・ランプの線レイヤー（変種 × 3 段）の ID。 */
export function lineLayerIds(src: "mainline" | "ramps"): { id: string; level: number }[] {
  const vks = src === "mainline" ? ["lo", "hi"] : ["all"];
  return vks.flatMap((vk) => GLOW.map((_, i) => ({ id: `${src}-${vk}-${i}`, level: i })));
}

/** 本線・ランプの線レイヤー。発光の段（0, 1）は glowMinZoom 未満では描かない（広域の描画負荷を抑える）。
 * 指標・発光・太さの切替はレイヤーを作り直さず、linePaint と表示切替で更新する。 */
export function lineLayers(src: "mainline" | "ramps", o: StyleOpts, glowMinZoom: number): any[] {
  const out: any[] = [];
  const variants: [string, any, number | undefined, number | undefined][] =
    src === "mainline"
      ? [
          // 低ズームでは片方向だけ描く（日本は下り、米国・カナダは南行き・西行き）
          ["lo", ["in", ["get", "direction"], ["literal", ["down", "south", "west", "outer", "unknown"]]], undefined, 13],
          ["hi", true, 13, undefined],
        ]
      : [["all", true, undefined, undefined]];
  for (const [vk, filter, minzoom, maxzoom] of variants) {
    GLOW.forEach(([, op, blur], i) => {
      const paint = linePaint(src, i, o);
      const lyr: any = {
        id: `${src}-${vk}-${i}`,
        type: "line",
        source: src,
        filter,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": paint.color, "line-width": paint.width, "line-opacity": op, "line-blur": blur },
      };
      const minz = i < GLOW_LEVELS ? Math.max(minzoom ?? 0, glowMinZoom) : minzoom;
      if (minz != null && minz > 0) lyr.minzoom = minz;
      if (maxzoom != null) lyr.maxzoom = maxzoom;
      out.push(lyr);
    });
  }
  return out;
}

export const FAC_COLORS: Record<string, string> = { IC: "#ffb53d", SIC: "#ffb53d", JCT: "#a98bff", SA: "#47e08f", PA: "#47e08f" };

/** 品質「低」・要確認の施設の印（以前は施設ごとの HTML 要素。GPU で描く円の輪に置き換え）。 */
export const WARNING_FILTER: any = ["any",
  ["in", ["get", "quality"], ["literal", ["low", "unusable"]]],
  ["==", ["get", "review_status"], "needs_review"]];

export function warningLayer(): any {
  return {
    id: "fac-warn",
    type: "circle",
    source: "facilities",
    filter: WARNING_FILTER,
    paint: {
      "circle-radius": 6.5,
      "circle-color": "rgba(0,0,0,0)",
      "circle-stroke-width": 1.6,
      "circle-stroke-color": ["case", ["in", ["get", "quality"], ["literal", ["low", "unusable"]]], "#ff5b4f", "#ffb53d"],
      "circle-translate": [9, -9],
    },
  };
}

export function facilityLayers(): any[] {
  const color = matchExpression("facility_type", FAC_COLORS, "#ffb53d");
  return [
    [14, 0.08, 1],
    [8, 0.22, 0.6],
    [3.5, 1.0, 0],
  ].map(([r, op, blur], i) => ({
    id: `facilities-${i}`,
    type: "circle",
    source: "facilities",
    paint: { "circle-radius": r, "circle-color": color, "circle-opacity": op, "circle-blur": blur },
  }));
}
