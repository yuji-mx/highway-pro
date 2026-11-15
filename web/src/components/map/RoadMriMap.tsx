import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useCursor, useLayers } from "../../state/stores";
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
export interface MapSettings {
  label_min_zoom: number;
  jct_label_min_zoom: number;
  label_max_count: number;
  label_update_delay_ms: number;
  glow_min_zoom: number;
  tiles: { max_zoom: number; layers: Record<string, { min_zoom: number }> };
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
    // ベクタタイルのソースを使うレイヤーには source-layer（タイル内のレイヤー名＝ソース名）を付ける
    const add = (l: any) => m.addLayer({
      ...l,
      ...((TILE_LAYERS as readonly string[]).includes(l.source) ? { "source-layer": l.source } : {}),
      layout: { ...(l.layout ?? {}), visibility: "none" },
    });
    add({ id: "general", type: "line", source: "general", paint: { "line-color": "rgba(110,125,140,0.30)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.9, 15, 1.3] } });
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
  }, [L.visible, L.glow, L.highlightTargets, tgt, p.selectedRampId, p.selectedFacilityId, builtTick]);

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
  const labelState = useRef({ data: data.facilities, on: false, cfg });
  labelState.current = { data: data.facilities, on: L.visible.labels && (p.showLabels ?? true), cfg };
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const update = () => {
      const { data: fc, on, cfg: c } = labelState.current;
      markers.current.forEach((x) => x.remove());
      markers.current = [];
      if (!on || !fc || !c) return;
      const z = m.getZoom();
      if (z < Math.min(c.label_min_zoom, c.jct_label_min_zoom)) return;
      const b = m.getBounds();
      const center = m.getCenter();
      const cand = (fc.features as Feature[])
        .map((f) => ({ pr: f.properties ?? {}, ll: (f.geometry as any).coordinates as [number, number] }))
        .filter(({ pr, ll }) => b.contains(ll) && z >= (pr.facility_type === "JCT" ? c.jct_label_min_zoom : c.label_min_zoom))
        .sort((x, y) => Math.hypot(x.ll[0] - center.lng, x.ll[1] - center.lat) - Math.hypot(y.ll[0] - center.lng, y.ll[1] - center.lat))
        .slice(0, c.label_max_count);
      for (const { pr, ll } of cand) {
        const d = document.createElement("div");
        d.className = "maplabel";
        d.textContent = pr.name ?? "";
        d.style.cssText = `color:${pr.facility_type === "JCT" ? "#c9b8ff" : "#aebccb"};font-size:11px;text-shadow:0 0 3px #000,0 0 3px #000;transform:translateY(-14px);pointer-events:none;white-space:nowrap`;
        markers.current.push(new maplibregl.Marker({ element: d, anchor: "bottom" }).setLngLat(ll).addTo(m));
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
  }, [ready, data.facilities, L.visible.labels, p.showLabels, cfg]);

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

  // クリック・ホバー・カーソル
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "mri-pop" });
    const pick = (pt: maplibregl.Point) => {
      const layers = ["facilities-2", "ramps-all-2", "ramps-all-0"].filter((id) => m.getLayer(id));
      const fs = m.queryRenderedFeatures([[pt.x - 6, pt.y - 6], [pt.x + 6, pt.y + 6]], { layers });
      const fac = fs.find((f) => f.source === "facilities");
      return fac ?? fs.find((f) => f.source === "ramps");
    };
    const move = (e: maplibregl.MapMouseEvent) => {
      p.onCursor?.([e.lngLat.lng, e.lngLat.lat]);
      const f = pick(e.point);
      m.getCanvas().style.cursor = f ? "pointer" : "";
      if (!f) return popup.remove();
      const pr = f.properties as any;
      const html = f.source === "facilities"
        ? `<b>${pr.name ?? "名称不明"}</b><br>${pr.facility_type}・${pr.shape_class}`
        : `<b>${pr.ramp_id}</b><br>${pr.flow}・${Math.round(Number(pr.length_m ?? 0))} m`;
      popup.setLngLat(e.lngLat).setHTML(html).addTo(m);
    };
    const click = (e: maplibregl.MapMouseEvent) => {
      const f = pick(e.point);
      if (!f || !p.onClick) return;
      const pr = f.properties as any;
      p.onClick(f.source === "facilities" ? { kind: "facility", id: pr.facility_id, props: pr } : { kind: "ramp", id: pr.ramp_id, props: pr });
    };
    m.on("mousemove", move);
    m.on("click", click);
    m.on("mouseout", () => popup.remove());
    return () => {
      m.off("mousemove", move);
      m.off("click", click);
      popup.remove();
    };
  }, [ready, p]);

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

  return <div ref={el} className="mapbox" style={{ height: p.height ?? 360 }} data-testid={p.testId ?? "map"} />;
}
