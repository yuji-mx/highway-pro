import { useMemo } from "react";
import type { SamplePoint } from "../../api/types";
import { useCursor } from "../../state/stores";
import { convert, unitLabel } from "../../state/units";
import { fmt } from "../common/ui";
import EChart, { AXIS, TOOLTIP } from "./EChart";
import { nearest } from "./ProfileChart";

/** 曲率プロファイル（1/R、左カーブ正、R の読み値）。 */
export default function CurvatureChart({ points, height = 200 }: { points: SamplePoint[]; height?: number }) {
  const setPos = useCursor((s) => s.setPos);
  const option = useMemo(() => ({
    backgroundColor: "transparent",
    grid: { left: 52, right: 14, top: 14, bottom: 28 },
    tooltip: { ...TOOLTIP, trigger: "axis", formatter: (ps: any) => {
      const k = ps[0]?.data?.[1];
      return `${fmt(Number(ps[0]?.axisValue), 0, " m")}<br>κ ${k == null ? "—" : k.toFixed(5)} 1/m<br>R ${k ? fmt(1 / Math.abs(k), 0, " m") + "（" + (k > 0 ? "左" : "右") + "）" : "—"}`;
    } },
    xAxis: { type: "value", name: `距離 [${unitLabel("m")}]`, min: 0, max: points.length ? Math.ceil(points[points.length - 1].s_m) : 1, ...AXIS, axisLabel: { ...AXIS.axisLabel, formatter: (v: number) => String(Math.round(convert(v, "m")[0])) } },
    yAxis: { type: "value", name: "1/R [1/m]", ...AXIS },
    series: [{ type: "line", showSymbol: false, data: points.map((p) => [p.s_m, p.curvature]), lineStyle: { color: "#a98bff", width: 1.6 },
      areaStyle: { color: "rgba(169,139,255,.12)" }, markLine: { symbol: "none", silent: true, data: [{ yAxis: 0 }], lineStyle: { color: "#28323e" } } }],
  }) as any, [points]);
  const onEvent = useMemo(() => ({
    "zr:mousemove": (e: any, c: any) => {
      const x = c.convertFromPixel({ seriesIndex: 0 }, [e.offsetX, e.offsetY])?.[0];
      const p = nearest(points, Number(x));
      if (p) setPos([p.lon, p.lat]);
    },
    "zr:globalout": () => setPos(null),
  }), [points, setPos]);
  return <EChart option={option} height={height} onEvent={onEvent} testId="curvature-chart" />;
}
