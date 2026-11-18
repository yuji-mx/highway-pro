import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { displayName } from "../common/names";
import { fmt } from "../common/ui";
import { t } from "../../i18n";
import { useCursor, useLayers } from "../../state/stores";
import { HoverTip, pickTarget, targetKey, type PickOpts, type TipTiming } from "./hoverTip";
import { placeLabels, shortLabel } from "./labelLayout";
import { useSettings } from "../../state/units";
import {
  GEOJSON_LAYERS, GLOW_LEVELS, TILE_LAYERS, WARNING_FILTER, facilityLayers, lineLayerIds, lineLayers, linePaint, loadLayer, warningLayer,
  type DataLayer, type StyleOpts,
} from "./layers";

export interface MapClick {
  kind: "facility" | "ramp";
  id: string;
  props: Record<string, any>;
}

export interface Overlay {
  id: string;
  data: FeatureCollection;
  color: string;
  width?: number;
  kind?: "line" | "circle";
}

interface Props {
  height?: number | string;
  focus?: [number, number, number, number] | null;
  center?: [number, number] | null;
  zoom?: number;
  targetRampIds?: string[];
  selectedRampId?: string | null;
  selectedFacilityId?: string | null;
  overlays?: Overlay[];
  onClick?: (c: MapClick) => void;
  onCursor?: (lonlat: [number, number]) => void;
  onMap?: (m: MlMap) => void;
  interactive?: boolean;
  showLabels?: boolean;
  testId?: string;
  /** 複数国のデータがあるとき、初期表示をこの国の施設範囲に合わせる（"JP" / "US" など）。 */
  country?: string | null;
}

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

function boundsOf(fc: FeatureCollection): [number, number, number, number] | null {
  let w = 180, s = 90, e = -180, n = -90;
  const visit = (c: any) => {
    if (typeof c[0] === "number") {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(visit);
  };
  fc.features.forEach((f: any) => f.geometry && visit(f.geometry.coordinates));
  return w <= e ? [w, s, e, n] : null;
}

/** /api/meta の map（config/settings.yaml）。 */
/** 一般道の色（凡例と共通）。 */
export const GENERAL_COLOR = "rgba(150,166,184,0.62)";

/** 施設名の高さ（px）と、幅を測るためのキャンバス（地図の名称と同じ書体・大きさ）。 */
const LABEL_H = 14;
let _measure: CanvasRenderingContext2D | null = null;
function measureCtx(): CanvasRenderingContext2D {
  if (!_measure) {
    _measure = document.createElement("canvas").getContext("2d")!;
    _measure.font = `11px ${getComputedStyle(document.body).fontFamily || "sans-serif"}`;
  }
  return _measure;
}

/** ツールチップの設定（config/settings.yaml の map.tooltip）。 */
type TipCfg = TipTiming & PickOpts & { offset_px: number };

/** ツールチップの中身（文字列として入れる。OSM の名称などを HTML として解釈しない）。 */
function tipContent(f: maplibregl.MapGeoJSONFeature): HTMLElement {
  const pr = f.properties as any;
  const d = document.createElement("div");
  d.dataset.testid = "map-tooltip";
  const title = document.createElement("b");
  const sub = document.createElement("div");
  if (f.source === "facilities") {
    title.textContent = displayName(pr.name);
    sub.textContent = [pr.facility_type, pr.shape_class].filter((x) => x && x !== "null").join("・");
  } else {
    title.textContent = String(pr.ramp_id ?? "");
    title.className = "mono";
    sub.textContent = [t(`flow.${pr.flow}`, pr.flow), pr.length_m != null ? fmt(Number(pr.length_m), 0, " m") : null].filter(Boolean).join("・");
  }
  d.append(title, sub);
  return d;
}

export interface MapSettings {
  label_min_zoom: number;
  jct_label_min_zoom: number;
  label_max_count: number;
  label_update_delay_ms: number;
  label_max_chars: number;
  label_gap_px: number;
  label_mark_px: number;
  glow_min_zoom: number;
  tooltip: TipCfg;
  tiles: { max_zoom: number; layers: Record<string, { min_zoom: number }> };
}

/** /api/meta の imagery（航空写真の取得元。URL は含まない）。 */
interface Imagery {
  id: string;
  name: string;
  max_zoom: number;
  bounds: [number, number, number, number];
  attribution: string;
}

/** /api/layers/summary（全要素を送らずに、色分けの範囲・初期表示に使う要約）。 */
interface Summary {
  run_id: string;
  z_range: [number, number] | null;
  shapes: string[];
  bounds: [number, number, number, number] | null;
  country_bounds: Record<string, [number, number, number, number]>;
}

type Layer = { id: string; visible: boolean };

export default function RoadMriMap(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const cursorMarker = useRef<maplibregl.Marker | null>(null);
  const built = useRef(false);
  const overlayIds = useRef<string[]>([]);
  const [ready, setReady] = useState(false);
  const [data, setData] = useState<Partial<Record<DataLayer, FeatureCollection>>>({});
  const L = useLayers();
  const cursor = useCursor((s) => s.pos);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const runId = meta.data?.run?.run_id ?? "";
  const cfg: MapSettings | undefined = meta.data?.map;
  const imagery: Imagery[] = useMemo(() => meta.data?.imagery ?? [], [meta.data]);

  // 施設・料金所は件数が少ないので全件（GeoJSON）。道路はベクタタイルで表示範囲だけを取得する
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    setData({});
    for (const n of GEOJSON_LAYERS) {
      loadLayer(n, runId)
        .then((fc) => alive && setData((d) => ({ ...d, [n]: fc })))
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, [runId]);
  const summary = useQuery({
    queryKey: ["layers-summary", runId],
    queryFn: () => api.get<Summary>("/layers/summary"),
    enabled: !!runId,
  });

  const zr = useMemo<[number, number]>(() => summary.data?.z_range ?? [30, 550], [summary.data]);
  const shapes = useMemo(() => summary.data?.shapes ?? [], [summary.data]);
  const opts: StyleOpts = useMemo(
    () => ({ metric: L.metric, glow: L.glow, widthByCurvature: L.widthByCurvature, zr, shapes }),
    [L.metric, L.glow, L.widthByCurvature, zr, shapes],
  );

  // 地図の生成
  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#000000" } }] },
      center: p.center ?? [139.85, 36.55],
      zoom: p.zoom ?? 11,
      attributionControl: false,
      preserveDrawingBuffer: true,
      interactive: p.interactive ?? true,
    } as any);
    mapRef.current = m;
    (window as any).__roadmriMaps = { ...((window as any).__roadmriMaps ?? {}), [p.testId ?? "map"]: m };
    m.on("load", () => setReady(true));
    if (p.interactive ?? true) m.addControl(new maplibregl.ScaleControl({ unit: useSettings.getState().units === "imperial" ? "imperial" : "metric" }), "bottom-left");
    p.onMap?.(m);
    return () => {
      markers.current.forEach((x) => x.remove());
      m.remove();
      mapRef.current = null;
      built.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ソースとレイヤーは 1 回だけ作る。以後はデータ（setData）・色（setPaintProperty）・表示・絞り込みだけを更新する
  // （以前は表示切替や選択のたびに全ソースを作り直し、全データを描画スレッドへ送り直していた）
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !cfg || !runId || built.current) return;
    built.current = true;
    for (const n of TILE_LAYERS) {
      m.addSource(n, {
        type: "vector",
        tiles: [`${window.location.origin}/api/tiles/${n}/{z}/{x}/{y}.pbf?v=${encodeURIComponent(runId)}`],
        minzoom: cfg.tiles.layers[n]?.min_zoom ?? 0,
        maxzoom: cfg.tiles.max_zoom,
      });
    }
    for (const n of GEOJSON_LAYERS) m.addSource(n, { type: "geojson", data: EMPTY as any });
    // 航空写真（加速車線の確認用。/api/imagery 経由で取得・保存。最大ズームより先は拡大表示）
    for (const im of imagery) {
      m.addSource(`img-${im.id}`, {
        type: "raster",
        tiles: [`${window.location.origin}/api/imagery/${im.id}/{z}/{x}/{y}`],
        tileSize: 256,
        maxzoom: im.max_zoom,
        bounds: im.bounds,
      });
    }
    // ベクタタイルのソースを使うレイヤーには source-layer（タイル内のレイヤー名＝ソース名）を付ける
    const add = (l: any) => m.addLayer({
      ...l,
      ...((TILE_LAYERS as readonly string[]).includes(l.source) ? { "source-layer": l.source } : {}),
      layout: { ...(l.layout ?? {}), visibility: "none" },
    });
    for (const im of imagery) add({ id: `img-${im.id}`, type: "raster", source: `img-${im.id}`, paint: { "raster-opacity": 0.9 } });
    // 一般道：黒い背景でも見分けられる明るさ。本線（白）・ランプ（水色）より暗く細くして区別する
    add({ id: "general", type: "line", source: "general", paint: { "line-color": GENERAL_COLOR,
      "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.8, 13, 1.3, 16, 2.2] } });
    add({ id: "bridges", type: "line", source: "structures", filter: ["in", ["get", "kind"], ["literal", ["bridge", "viaduct"]]],
      paint: { "line-color": "rgba(169,139,255,0.55)", "line-width": 5 } });
    for (const l of lineLayers("mainline", opts, cfg.glow_min_zoom)) add(l);
    for (const l of lineLayers("ramps", opts, cfg.glow_min_zoom)) add(l);
    add({ id: "tunnels", type: "line", source: "structures", filter: ["==", ["get", "kind"], "tunnel"],
      paint: { "line-color": "rgba(170,180,190,0.9)", "line-width": 2, "line-dasharray": [4, 4] } });
    [[7, 0.15, 4], [3, 0.35, 0], [2.2, 1, 0]].forEach(([w, o, b], i) =>
      add({ id: `target-${i}`, type: "line", source: "ramps", filter: ["in", ["get", "ramp_id"], ["literal", []]],
        paint: { "line-color": "#ff7a2e", "line-width": w, "line-opacity": o, "line-blur": b } }));
    add({ id: "selected", type: "line", source: "ramps", filter: ["==", ["get", "ramp_id"], "__none__"],
      paint: { "line-color": "#ffffff", "line-width": 3.2 } });
    for (const l of facilityLayers()) add(l);
    add(warningLayer());
    add({ id: "fac-selected", type: "circle", source: "facilities", filter: ["==", ["get", "facility_id"], "__none__"],
      paint: { "circle-radius": 7, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
    add({ id: "tolls", type: "circle", source: "tolls", paint: { "circle-radius": 2.5, "circle-color": "#ffb53d" } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, cfg, runId]);
  const [builtTick, setBuiltTick] = useState(0);
  useEffect(() => {
    if (ready && cfg && runId) setBuiltTick((x) => x + 1);
  }, [ready, cfg, runId]);

  // データの反映（変わったソースだけ）
  const sent = useRef<Partial<Record<DataLayer, FeatureCollection>>>({});
  // 公開 ID が変わったら（再 build 後）タイルを取り直す
  const tileRun = useRef("");
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !built.current || !runId) return;
    if (tileRun.current && tileRun.current !== runId) {
      for (const n of TILE_LAYERS) {
        (m.getSource(n) as maplibregl.VectorTileSource | undefined)?.setTiles([
          `${window.location.origin}/api/tiles/${n}/{z}/{x}/{y}.pbf?v=${encodeURIComponent(runId)}`,
        ]);
      }
    }
    tileRun.current = runId;
  }, [runId, builtTick]);
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !built.current) return;
    for (const n of GEOJSON_LAYERS) {
      const fc = data[n] ?? EMPTY;
      if (sent.current[n] === fc) continue;
      (m.getSource(n) as maplibregl.GeoJSONSource | undefined)?.setData(fc as any);
      sent.current[n] = fc;
    }
  }, [data, builtTick]);

  // 色・太さ（指標の切替など）
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !built.current) return;
    for (const src of ["mainline", "ramps"] as const) {
      for (const { id, level } of lineLayerIds(src)) {
        const pt = linePaint(src, level, opts);
        m.setPaintProperty(id, "line-color", pt.color);
        m.setPaintProperty(id, "line-width", pt.width);
      }
    }
  }, [opts, builtTick]);

  // 表示・非表示と絞り込み
  const tgt = p.targetRampIds;
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !built.current) return;
    const v = L.visible;
    const vis: Layer[] = [
      ...imagery.map((im) => ({ id: `img-${im.id}`, visible: v.aerial })),
      { id: "general", visible: v.general },
      { id: "bridges", visible: v.bridges },
      { id: "tunnels", visible: v.tunnels },
      { id: "tolls", visible: v.tolls },
      { id: "fac-warn", visible: v.warnings },
      { id: "selected", visible: true },
      { id: "fac-selected", visible: true },
      ...[0, 1, 2].map((i) => ({ id: `facilities-${i}`, visible: true })),
      ...[0, 1, 2].map((i) => ({ id: `target-${i}`, visible: v.target && L.highlightTargets })),
      ...(["mainline", "ramps"] as const).flatMap((src) =>
        lineLayerIds(src).map(({ id, level }) => ({ id, visible: v[src] && (level === GLOW_LEVELS || L.glow) })),
      ),
    ];
    for (const { id, visible } of vis) if (m.getLayer(id)) m.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
    const facFilter: any = ["any",
      ...(v.ic ? [["in", ["get", "facility_type"], ["literal", ["IC", "SIC"]]]] : []),
      ...(v.jct ? [["==", ["get", "facility_type"], "JCT"]] : []),
      ...(v.sapa ? [["in", ["get", "facility_type"], ["literal", ["SA", "PA"]]]] : [])];
    for (const i of [0, 1, 2]) m.setFilter(`facilities-${i}`, facFilter);
    m.setFilter("fac-warn", ["all", facFilter, WARNING_FILTER]);
    for (const i of [0, 1, 2]) m.setFilter(`target-${i}`, ["in", ["get", "ramp_id"], ["literal", tgt ?? []]]);
    m.setFilter("selected", ["==", ["get", "ramp_id"], p.selectedRampId ?? "__none__"]);
    m.setFilter("fac-selected", ["==", ["get", "facility_id"], p.selectedFacilityId ?? "__none__"]);
  }, [L.visible, L.glow, L.highlightTargets, tgt, p.selectedRampId, p.selectedFacilityId, builtTick, imagery]);

  // 重ね描き（比較・統計画面の強調表示など。件数が少ないので差し替える）
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !built.current) return;
    for (const id of overlayIds.current) {
      for (const l of [id, `${id}-g`]) if (m.getLayer(l)) m.removeLayer(l);
      if (m.getSource(id)) m.removeSource(id);
    }
    overlayIds.current = [];
    for (const o of p.overlays ?? []) {
      const id = `ov-${o.id}`;
      m.addSource(id, { type: "geojson", data: o.data as any });
      if (o.kind === "circle") {
        m.addLayer({ id, type: "circle", source: id, paint: { "circle-radius": o.width ?? 9,
          "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": o.color, "circle-stroke-width": 2 } });
      } else {
        m.addLayer({ id: `${id}-g`, type: "line", source: id, paint: { "line-color": o.color, "line-width": (o.width ?? 3) * 3, "line-opacity": 0.2, "line-blur": 3 } });
        m.addLayer({ id, type: "line", source: id, paint: { "line-color": o.color, "line-width": o.width ?? 3 } });
      }
      overlayIds.current.push(id);
    }
  }, [p.overlays, builtTick]);

  // 名称（HTML 要素。オフラインでフォントグリフを使わないため）。表示範囲内のものだけ、上限件数まで作る
  // （以前は全施設分を作っていたため、広域データでは数千個の要素を毎フレーム動かしていた）
  // 施設の種類のチェック（IC・JCT・SA/PA）を外した施設は、記号と同じく名称も出さない
  const typeOn = (ft: unknown) =>
    ft === "IC" || ft === "SIC" ? L.visible.ic : ft === "JCT" ? L.visible.jct : ft === "SA" || ft === "PA" ? L.visible.sapa : false;
  const labelState = useRef({ data: data.facilities, on: false, cfg, typeOn });
  labelState.current = { data: data.facilities, on: L.visible.labels && (p.showLabels ?? true), cfg, typeOn };
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const update = () => {
      const { data: fc, on, cfg: c, typeOn: shown } = labelState.current;
      markers.current.forEach((x) => x.remove());
      markers.current = [];
      if (!on || !fc || !c) return;
      const z = m.getZoom();
      if (z < Math.min(c.label_min_zoom, c.jct_label_min_zoom)) return;
      const b = m.getBounds();
      const center = m.getCenter();
      const cand = (fc.features as Feature[])
        .map((f) => ({ pr: f.properties ?? {}, ll: (f.geometry as any).coordinates as [number, number] }))
        .filter(({ pr, ll }) => shown(pr.facility_type) && b.contains(ll)
          && z >= (pr.facility_type === "JCT" ? c.jct_label_min_zoom : c.label_min_zoom))
        .sort((x, y) => Math.hypot(x.ll[0] - center.lng, x.ll[1] - center.lat) - Math.hypot(y.ll[0] - center.lng, y.ll[1] - center.lat))
        .slice(0, c.label_max_count);
      // 重なりを避けて配置（JCT を優先、次に表示範囲の中心に近い順）。置けない名称は出さない
      cand.sort((x, y) => Number(y.pr.facility_type === "JCT") - Number(x.pr.facility_type === "JCT"));
      const ctx2d = measureCtx();
      const items = cand.map(({ pr, ll }, i) => {
        const text = shortLabel(displayName(pr.name), c.label_max_chars);
        const p2 = m.project(ll);
        return { key: String(i), x: p2.x, y: p2.y, w: Math.ceil(ctx2d.measureText(text).width) + 4, h: LABEL_H, text, pr, ll };
      });
      const canvas = m.getCanvas();
      const placed = placeLabels(items, { w: canvas.clientWidth, h: canvas.clientHeight }, c.label_gap_px, c.label_mark_px);
      for (const pl of placed) {
        const it = items[Number(pl.key)];
        const d = document.createElement("div");
        d.className = "maplabel";
        d.textContent = it.text;
        d.title = displayName(it.pr.name);
        d.style.cssText = `color:${it.pr.facility_type === "JCT" ? "#c9b8ff" : "#aebccb"};font-size:11px;line-height:${LABEL_H}px;`
          + "text-shadow:0 0 3px #000,0 0 3px #000;pointer-events:none;white-space:nowrap";
        markers.current.push(new maplibregl.Marker({ element: d, anchor: "top-left", offset: [pl.dx, pl.dy] }).setLngLat(it.ll).addTo(m));
      }
    };
    const later = () => {
      clearTimeout(timer);
      timer = setTimeout(update, labelState.current.cfg?.label_update_delay_ms ?? 0);
    };
    update();
    m.on("moveend", later);
    return () => {
      clearTimeout(timer);
      m.off("moveend", later);
    };
  }, [ready, data.facilities, L.visible.labels, L.visible.ic, L.visible.jct, L.visible.sapa, p.showLabels, cfg]);

  // 表示範囲（要約の範囲を使う。国が複数あるときは指定の国、なければ最初の国）
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const sm = summary.data;
    const countries = Object.keys(sm?.country_bounds ?? {}).sort();
    const want = p.country ?? (countries.length > 1 ? countries[0] : null);
    const byCountry = want ? sm?.country_bounds?.[want] ?? null : null;
    const padded = byCountry ? ([byCountry[0] - 0.05, byCountry[1] - 0.04, byCountry[2] + 0.05, byCountry[3] + 0.04] as [number, number, number, number]) : null;
    const b = p.focus ?? (p.center ? null : padded ?? sm?.bounds ?? boundsOf(data.facilities ?? EMPTY));
    if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, animate: false, maxZoom: 16 });
    else if (p.center) m.jumpTo({ center: p.center, zoom: p.zoom ?? 14 });
  }, [ready, p.focus, p.center, p.zoom, p.country, summary.data, data.facilities]);

  // クリック・ホバー・カーソル。コールバックは参照で持ち、イベントは地図ごとに 1 回だけ付ける
  // （以前は props 全体に依存して再描画のたびに付け直し、そのたびにツールチップを消していた）
  const cb = useRef({ onClick: p.onClick, onCursor: p.onCursor });
  cb.current = { onClick: p.onClick, onCursor: p.onCursor };
  const tipCfg: TipCfg | undefined = cfg?.tooltip;
  const tipRef = useRef<HoverTip<maplibregl.LngLat> | null>(null);
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready || !tipCfg) return;
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "mri-pop", offset: tipCfg.offset_px, maxWidth: "280px" });
    let current: maplibregl.MapGeoJSONFeature | null = null;
    let suppress: string | null = null; // クリックした対象：一度離れるまでツールチップを出さない
    const tip = new HoverTip<maplibregl.LngLat>(
      tipCfg,
      (_key, at) => { if (current) popup.setLngLat(at).setDOMContent(tipContent(current)).addTo(m); },
      () => popup.remove(),
    );
    tipRef.current = tip;
    const move = (e: maplibregl.MapMouseEvent) => {
      cb.current.onCursor?.([e.lngLat.lng, e.lngLat.lat]);
      const f = pickTarget(m, e.point.x, e.point.y, tipCfg);
      const key = targetKey(f);
      m.getCanvas().style.cursor = f ? "pointer" : "";
      if (key !== suppress) suppress = null;
      if (key !== null && key === suppress) return;
      if (key !== tip.shown) current = f;
      tip.move(key, e.lngLat);
    };
    const click = (e: maplibregl.MapMouseEvent) => {
      const f = pickTarget(m, e.point.x, e.point.y, tipCfg);
      tip.cancel(); // 情報パネルを開くときはツールチップを閉じる
      suppress = targetKey(f);
      if (!f || !cb.current.onClick) return;
      const pr = f.properties as any;
      cb.current.onClick(f.source === "facilities" ? { kind: "facility", id: pr.facility_id, props: pr } : { kind: "ramp", id: pr.ramp_id, props: pr });
    };
    const leave = () => {
      suppress = null; // 地図の外（情報パネルなど）へ出たら、クリックした対象の抑止も解く
      tip.move(null, m.getCenter());
    };
    const stop = () => tip.cancel(); // 地図の移動・ズーム中は出さない
    m.on("mousemove", move);
    m.on("click", click);
    m.on("mouseout", leave);
    m.on("movestart", stop);
    return () => {
      m.off("mousemove", move);
      m.off("click", click);
      m.off("mouseout", leave);
      m.off("movestart", stop);
      tip.cancel();
      popup.remove();
      tipRef.current = null;
    };
  }, [ready, tipCfg]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    if (!cursor) {
      cursorMarker.current?.remove();
      cursorMarker.current = null;
      return;
    }
    if (!cursorMarker.current) {
      const d = document.createElement("div");
      d.dataset.testid = "map-cursor";
      d.style.cssText = "width:12px;height:12px;border-radius:50%;background:#fff;box-shadow:0 0 10px #38e1ff,0 0 4px #fff";
      cursorMarker.current = new maplibregl.Marker({ element: d });
    }
    cursorMarker.current.setLngLat(cursor).addTo(m);
  }, [cursor, ready]);

  return (
    <div style={{ position: "relative", height: p.height ?? 360 }}>
      <div ref={el} className="mapbox" style={{ height: "100%" }} data-testid={p.testId ?? "map"} />
      {L.visible.aerial && imagery.length > 0 && (
        <div className="map-attr" data-testid="aerial-attribution">{imagery.map((im) => im.attribution).join(" ／ ")}</div>
      )}
    </div>
  );
}
