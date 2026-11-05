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

export interface StyleOpts {
  metric: string;
  glow: boolean;
  widthByCurvature: boolean;
  zr: [number, number];
  shapes: string[];
}

export function lineLayers(src: "mainline" | "ramps", o: StyleOpts): any[] {
  const color = lineColor(o.metric, src, o.zr, o.shapes);
  const scale = src === "ramps" && o.widthByCurvature
    ? ["interpolate", ["linear"], ["abs", ["coalesce", ["get", "curvature"], 0]], 0, 1, 0.005, 1.5, 0.02, 3]
    : 1;
  const glows = o.glow ? GLOW : [GLOW[2]];
  const out: any[] = [];
  const variants: [string, any, number | undefined, number | undefined][] =
    src === "mainline"
      ? [
          ["lo", ["==", ["get", "direction"], "down"], undefined, 13],
          ["hi", true, 13, undefined],
        ]
      : [["all", true, undefined, undefined]];
  for (const [vk, filter, minzoom, maxzoom] of variants) {
    glows.forEach(([w, op, blur], i) => {
      const lyr: any = {
        id: `${src}-${vk}-${i}`,
        type: "line",
        source: src,
        filter,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": color,
          "line-width": ["*", w, scale] as any,
          "line-opacity": op,
          "line-blur": blur,
        },
      };
      if (minzoom != null) lyr.minzoom = minzoom;
      if (maxzoom != null) lyr.maxzoom = maxzoom;
      if (scale === 1) lyr.paint["line-width"] = w;
      out.push(lyr);
    });
  }
  return out;
}

export const FAC_COLORS: Record<string, string> = { IC: "#ffb53d", SIC: "#ffb53d", JCT: "#a98bff", SA: "#47e08f", PA: "#47e08f" };

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
