import { useMemo } from "react";
import type { SamplePoint } from "../../api/types";
import { useCursor } from "../../state/stores";
import { gradeColor } from "../map/colorScales";
import EChart, { AXIS, TOOLTIP } from "./EChart";

export interface Marker {
  s: number;
  label: string;
  color?: string;
}

/** 標高プロファイル（DEM 生値の灰色線、補正後を勾配色分け、構造物・低信頼帯、最大上り・下り地点、カーソル連動）。 */
export default function ProfileChart({
  points, markers = [], window: w, height = 260, testId = "profile-chart", dashedRaw = false,
}: {
  points: SamplePoint[];
  markers?: Marker[];
  window?: number | null;
  height?: number;
  testId?: string;
  dashedRaw?: boolean;
}) {
  const setPos = useCursor((s) => s.setPos);
  const option = useMemo(() => {
    const raw = points.map((p) => [p.s_m, p.z_dem_raw]);
    const segs = points.slice(1).map((p, i) => ({
      coords: [[points[i].s_m, points[i].z_corrected], [p.s_m, p.z_corrected]],
      lineStyle: { color: gradeColor(p.grade_w100 ?? p.grade_inst) },
    }));
    const bands: any[] = [];
    let start: number | null = null;
    let kind = "";
    points.forEach((p, i) => {
      const on = p.low_confidence || p.structure !== "none";
      if (on && start == null) {
        start = p.s_m;
        kind = p.structure;
      }
      if ((!on || i === points.length - 1) && start != null) {
        bands.push([{ xAxis: start, itemStyle: { color: kind === "tunnel" ? "rgba(170,180,190,.12)" : kind === "none" ? "rgba(255,91,79,.08)" : "rgba(169,139,255,.14)" }, name: kind === "none" ? "DEM低信頼" : kind }, { xAxis: p.s_m }]);
        start = null;
      }
    });
    const zs = points.map((p) => p.z_corrected ?? p.z_dem_raw).filter((z): z is number => z != null);
    const lo = zs.length ? Math.floor(Math.min(...zs) - 2) : 0;
    const hi = zs.length ? Math.ceil(Math.max(...zs) + 2) : 1;
    let iu = -1, idn = -1;
    points.forEach((p, i) => {
      const g = p.grade_w100;
      if (g == null) return;
      if (iu < 0 || g > (points[iu].grade_w100 ?? -1e9)) iu = i;
      if (idn < 0 || g < (points[idn].grade_w100 ?? 1e9)) idn = i;
    });
    const mk: any[] = [];
    if (iu >= 0) mk.push({ coord: [points[iu].s_m, points[iu].z_corrected], name: `最大上り ${points[iu].grade_w100?.toFixed(1)}%`, itemStyle: { color: "#ff5b4f" } });
    if (idn >= 0) mk.push({ coord: [points[idn].s_m, points[idn].z_corrected], name: `最大下り ${points[idn].grade_w100?.toFixed(1)}%`, itemStyle: { color: "#3c78ff" } });
    const series: any[] = [
      { name: "DEM生値", type: "line", data: raw, showSymbol: false, lineStyle: { color: "#7d8b9a", width: 1, type: dashedRaw ? "dashed" : "solid" }, z: 1,
        markArea: { silent: true, label: { color: "#7d8b9a", fontSize: 9 }, data: bands } },
      { name: "補正後", type: "lines", coordinateSystem: "cartesian2d", polyline: false, data: segs, lineStyle: { width: 2.4 }, z: 3 },
      { name: "地点", type: "scatter", data: [], z: 4, markPoint: { symbol: "pin", symbolSize: 26, label: { fontSize: 8, formatter: "{b}", show: false }, data: mk } },
    ];
    if (w) {
      series.push({ name: `${w}m窓勾配`, type: "line", yAxisIndex: 1, showSymbol: false, data: points.map((p) => [p.s_m, windowGrade(points, p.s_m, w)]),
        lineStyle: { color: "#ffd84a", width: 1, type: "dashed" } });
    }
    return {
      backgroundColor: "transparent",
      grid: { left: 46, right: w ? 46 : 14, top: 18, bottom: 28 },
      tooltip: { ...TOOLTIP, trigger: "axis", formatter: (ps: any) => {
        const x = Array.isArray(ps) ? ps[0]?.axisValue : ps.axisValue;
        const p = nearest(points, Number(x));
        return p ? `${p.s_m.toFixed(0)} m<br>標高 ${p.z_corrected?.toFixed(1) ?? "—"} m（生 ${p.z_dem_raw?.toFixed(1) ?? "—"}）<br>勾配 ${p.grade_w100?.toFixed(2) ?? "—"} %<br>${p.structure !== "none" ? p.structure : ""}` : "";
      } },
      xAxis: { type: "value", name: "距離 [m]", min: 0, max: points.length ? Math.ceil(points[points.length - 1].s_m) : 1, ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => String(Math.round(v)) } },
      yAxis: [{ type: "value", name: "標高 [m]", min: lo, max: hi, ...AXIS }, ...(w ? [{ type: "value", name: "%", ...AXIS, splitLine: { show: false } }] : [])],
      series: [
        ...series,
        { name: "接続", type: "line", data: [], markLine: { symbol: "none", silent: true, label: { color: "#aebccb", fontSize: 9, formatter: "{b}" },
          lineStyle: { color: "#4a5663", type: "dashed" }, data: markers.map((m) => ({ xAxis: m.s, name: m.label, lineStyle: { color: m.color ?? "#4a5663" } })) } },
      ],
    } as any;
  }, [points, markers, w, dashedRaw]);
  const onEvent = useMemo(() => ({
    "zr:mousemove": (e: any, c: any) => {
      const x = c.convertFromPixel({ seriesIndex: 0 }, [e.offsetX, e.offsetY])?.[0];
      const p = nearest(points, Number(x));
      if (p) setPos([p.lon, p.lat]);
    },
    "zr:globalout": () => setPos(null),
  }), [points, setPos]);
  return <EChart option={option} height={height} onEvent={onEvent} testId={testId} />;
}

export function nearest(points: SamplePoint[], s: number): SamplePoint | null {
  if (!points.length || !Number.isFinite(s)) return null;
  let best = points[0];
  for (const p of points) if (Math.abs(p.s_m - s) < Math.abs(best.s_m - s)) best = p;
  return best;
}

export function windowGrade(points: SamplePoint[], s: number, w: number): number | null {
  const z = (x: number) => {
    for (let i = 1; i < points.length; i++) {
      if (points[i].s_m >= x) {
        const a = points[i - 1], b = points[i];
        if (a.z_corrected == null || b.z_corrected == null) return null;
        const t = (x - a.s_m) / Math.max(1e-9, b.s_m - a.s_m);
        return a.z_corrected + t * (b.z_corrected - a.z_corrected);
      }
    }
    return null;
  };
  const total = points.length ? points[points.length - 1].s_m : 0;
  if (total < w) return points.length > 1 && points[0].z_corrected != null && points[points.length - 1].z_corrected != null
    ? ((points[points.length - 1].z_corrected! - points[0].z_corrected!) / total) * 100 : null;
  if (s - w / 2 < 0 || s + w / 2 > total) return null;
  const a = z(s - w / 2), b = z(s + w / 2);
  return a == null || b == null ? null : ((b - a) / w) * 100;
}
