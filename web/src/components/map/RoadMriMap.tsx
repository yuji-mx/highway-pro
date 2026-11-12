import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useCursor, useLayers } from "../../state/stores";
import { useSettings } from "../../state/units";
import { DATA_LAYERS, facilityLayers, lineLayers, loadLayer, type DataLayer } from "./layers";

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

export default function RoadMriMap(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const cursorMarker = useRef<maplibregl.Marker | null>(null);
  const [ready, setReady] = useState(false);
  const [zoomTick, setZoomTick] = useState(0);
  const [data, setData] = useState<Partial<Record<DataLayer, FeatureCollection>>>({});
  const L = useLayers();
  const cursor = useCursor((s) => s.pos);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const runId = meta.data?.run?.run_id ?? "";

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    Promise.all(DATA_LAYERS.map((n) => loadLayer(n, runId).then((fc) => [n, fc] as const)))
      .then((pairs) => alive && setData(Object.fromEntries(pairs)))
      .catch(() => alive && setData({}));
    return () => {
      alive = false;
    };
  }, [runId]);

  const zr = useMemo<[number, number]>(() => {
    const zs = [...(data.mainline?.features ?? []), ...(data.ramps?.features ?? [])]
      .map((f) => f.properties?.z)
      .filter((z): z is number => typeof z === "number")
      .sort((a, b) => a - b);
    if (!zs.length) return [30, 550];
    return [zs[Math.floor(zs.length * 0.02)], zs[Math.floor(zs.length * 0.98)]];
  }, [data]);
  const shapes = useMemo(
    () => [...new Set((data.facilities?.features ?? []).map((f) => String(f.properties?.shape_class)))].sort(),
    [data],
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
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ソースとレイヤーの構築（スタイル変更時は作り直す）
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const style = m.getStyle();
    for (const l of style.layers ?? []) if (l.id !== "bg") m.removeLayer(l.id);
    for (const s of Object.keys(style.sources ?? {})) m.removeSource(s);
    for (const n of DATA_LAYERS) m.addSource(n, { type: "geojson", data: (data[n] ?? EMPTY) as any });
    const v = L.visible;
    const opts = { metric: L.metric, glow: L.glow, widthByCurvature: L.widthByCurvature, zr, shapes };
    const add = (l: any, on: boolean) => m.addLayer({ ...l, layout: { ...(l.layout ?? {}), visibility: on ? "visible" : "none" } });
    add({ id: "general", type: "line", source: "general", paint: { "line-color": "rgba(110,125,140,0.30)",
      "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.9, 15, 1.3] } }, v.general);
    add({ id: "bridges", type: "line", source: "structures", filter: ["in", ["get", "kind"], ["literal", ["bridge", "viaduct"]]],
      paint: { "line-color": "rgba(169,139,255,0.55)", "line-width": 5 } }, v.bridges);
    for (const l of lineLayers("mainline", opts)) add(l, v.mainline);
    for (const l of lineLayers("ramps", opts)) add(l, v.ramps);
    add({ id: "tunnels", type: "line", source: "structures", filter: ["==", ["get", "kind"], "tunnel"],
      paint: { "line-color": "rgba(170,180,190,0.9)", "line-width": 2, "line-dasharray": [4, 4] } }, v.tunnels);
    const tgt = p.targetRampIds ?? [];
    [[7, 0.15, 4], [3, 0.35, 0], [2.2, 1, 0]].forEach(([w, o, b], i) =>
      add({ id: `target-${i}`, type: "line", source: "ramps", filter: ["in", ["get", "ramp_id"], ["literal", tgt]],
        paint: { "line-color": "#ff7a2e", "line-width": w, "line-opacity": o, "line-blur": b } }, v.target && L.highlightTargets));
    add({ id: "selected", type: "line", source: "ramps", filter: ["==", ["get", "ramp_id"], p.selectedRampId ?? "__none__"],
      paint: { "line-color": "#ffffff", "line-width": 3.2 } }, true);
    const facFilter: any = ["any",
      ...(v.ic ? [["in", ["get", "facility_type"], ["literal", ["IC", "SIC"]]]] : []),
      ...(v.jct ? [["==", ["get", "facility_type"], "JCT"]] : []),
      ...(v.sapa ? [["in", ["get", "facility_type"], ["literal", ["SA", "PA"]]]] : [])];
    for (const l of facilityLayers()) add({ ...l, filter: facFilter }, true);
    add({ id: "fac-selected", type: "circle", source: "facilities", filter: ["==", ["get", "facility_id"], p.selectedFacilityId ?? "__none__"],
      paint: { "circle-radius": 7, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } }, true);
    add({ id: "tolls", type: "circle", source: "tolls", paint: { "circle-radius": 2.5, "circle-color": "#ffb53d" } }, v.tolls);
    for (const o of p.overlays ?? []) {
      m.addSource(`ov-${o.id}`, { type: "geojson", data: o.data as any });
      if (o.kind === "circle") {
        m.addLayer({ id: `ov-${o.id}`, type: "circle", source: `ov-${o.id}`, paint: { "circle-radius": o.width ?? 9,
          "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": o.color, "circle-stroke-width": 2 } });
      } else {
        m.addLayer({ id: `ov-${o.id}-g`, type: "line", source: `ov-${o.id}`, paint: { "line-color": o.color, "line-width": (o.width ?? 3) * 3, "line-opacity": 0.2, "line-blur": 3 } });
        m.addLayer({ id: `ov-${o.id}`, type: "line", source: `ov-${o.id}`, paint: { "line-color": o.color, "line-width": o.width ?? 3 } });
      }
    }
    // ラベル・品質警告は HTML マーカー（オフラインでフォントグリフを使わないため）
    markers.current.forEach((x) => x.remove());
    markers.current = [];
    const z = m.getZoom();
    for (const f of (data.facilities?.features ?? []) as Feature[]) {
      const pr = f.properties ?? {};
      const [lon, lat] = (f.geometry as any).coordinates;
      if (v.labels && (p.showLabels ?? true) && (z >= 11 || pr.facility_type === "JCT")) {
        const d = document.createElement("div");
        d.className = "maplabel";
        d.textContent = pr.name ?? "";
        d.style.cssText = `color:${pr.facility_type === "JCT" ? "#c9b8ff" : "#aebccb"};font-size:11px;text-shadow:0 0 3px #000,0 0 3px #000;transform:translateY(-14px);pointer-events:none;white-space:nowrap`;
        markers.current.push(new maplibregl.Marker({ element: d, anchor: "bottom" }).setLngLat([lon, lat]).addTo(m));
      }
      if (v.warnings && (pr.quality === "low" || pr.quality === "unusable" || pr.review_status === "needs_review")) {
        const d = document.createElement("div");
        d.textContent = "▲";
        d.title = pr.quality === "low" ? "品質「低」" : "要確認";
        d.style.cssText = `color:${pr.quality === "low" || pr.quality === "unusable" ? "#ff5b4f" : "#ffb53d"};font-size:12px;transform:translate(10px,-10px)`;
        markers.current.push(new maplibregl.Marker({ element: d }).setLngLat([lon, lat]).addTo(m));
      }
    }
  }, [ready, data, L, p.targetRampIds, p.selectedRampId, p.selectedFacilityId, p.overlays, p.showLabels, zr, shapes, zoomTick]);

  // 表示範囲
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !ready) return;
    const facs = data.facilities ?? EMPTY;
    const countries = new Set(facs.features.map((f) => String(f.properties?.facility_id ?? "").slice(0, 2)));
    const want = p.country ?? (countries.size > 1 ? [...countries].sort()[0] : null);
    const byCountry = want
      ? boundsOf({ type: "FeatureCollection", features: facs.features.filter((f) => String(f.properties?.facility_id ?? "").startsWith(`${want}-`)) })
      : null;
    const padded = byCountry ? ([byCountry[0] - 0.05, byCountry[1] - 0.04, byCountry[2] + 0.05, byCountry[3] + 0.04] as [number, number, number, number]) : null;
    const b = p.focus ?? (p.center ? null : padded ?? boundsOf(data.mainline ?? EMPTY) ?? boundsOf(facs));
    if (b) m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, animate: false, maxZoom: 16 });
    else if (p.center) m.jumpTo({ center: p.center, zoom: p.zoom ?? 14 });
  }, [ready, p.focus, p.center, p.zoom, p.country, data.mainline, data.facilities]);

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

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    let last = Math.floor(m.getZoom());
    const onZoom = () => {
      const z = Math.floor(m.getZoom());
      if ((z >= 11) !== (last >= 11)) setZoomTick((x) => x + 1);
      last = z;
    };
    m.on("zoomend", onZoom);
    return () => {
      m.off("zoomend", onZoom);
    };
  }, [ready]);

  return <div ref={el} className="mapbox" style={{ height: p.height ?? 360 }} data-testid={p.testId ?? "map"} />;
}
