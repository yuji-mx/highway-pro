import { useMemo } from "react";
import EChart, { AXIS, TOOLTIP } from "./EChart";

const A = "#38e1ff";
const B = "#ff7a2e";

/** ヒストグラム重ね合わせ（構成比、中央値の破線）。 */
export function HistogramChart({ m }: { m: any }) {
  const option = useMemo(() => {
    const edges: number[] = m.hist.edges;
    const na = m.a.n || 1, nb = m.b.n || 1;
    const centers = edges.slice(0, -1).map((e, i) => (e + edges[i + 1]) / 2);
    return {
      backgroundColor: "transparent", grid: { left: 44, right: 12, top: 24, bottom: 28 },
      legend: { data: ["A", "B"], textStyle: { color: "#7d8b9a" }, right: 10 },
      tooltip: { ...TOOLTIP, trigger: "axis" },
      xAxis: { type: "value", name: m.unit, min: edges[0], max: edges[edges.length - 1], ...AXIS },
      yAxis: { type: "value", name: "構成比", ...AXIS },
      series: [
        { name: "A", type: "bar", barGap: "-100%", barWidth: "90%", data: centers.map((c, i) => [c, m.hist.a[i] / na]), itemStyle: { color: A, opacity: 0.5 },
          markLine: { symbol: "none", data: m.a.median != null ? [{ xAxis: m.a.median }] : [], lineStyle: { color: A, type: "dashed" }, label: { show: false } } },
        { name: "B", type: "bar", barWidth: "90%", data: centers.map((c, i) => [c, m.hist.b[i] / nb]), itemStyle: { color: B, opacity: 0.5 },
          markLine: { symbol: "none", data: m.b.median != null ? [{ xAxis: m.b.median }] : [], lineStyle: { color: B, type: "dashed" }, label: { show: false } } },
      ],
    } as any;
  }, [m]);
  return <EChart option={option} height={240} testId="hist-chart" />;
}

export function EcdfChart({ m }: { m: any }) {
  const option = useMemo(() => ({
    backgroundColor: "transparent", grid: { left: 44, right: 12, top: 24, bottom: 28 },
    legend: { data: ["A", "B"], textStyle: { color: "#7d8b9a" }, right: 10 }, tooltip: { ...TOOLTIP, trigger: "axis" },
    xAxis: { type: "value", name: m.unit, scale: true, ...AXIS }, yAxis: { type: "value", min: 0, max: 1, ...AXIS },
    series: [
      { name: "A", type: "line", step: "end", showSymbol: false, data: m.ecdf.a, lineStyle: { color: A } },
      { name: "B", type: "line", step: "end", showSymbol: false, data: m.ecdf.b, lineStyle: { color: B } },
    ],
  }) as any, [m]);
  return <EChart option={option} height={240} testId="ecdf-chart" />;
}

/** 散布図（最小 R × 100 m 上り勾配。対数 x 軸、閾値線、範囲外は灰色）。 */
export function ScatterChart({ a, b, onClick }: { a: any[]; b: any[]; onClick?: (rampId: string) => void }) {
  const option = useMemo(() => ({
    backgroundColor: "transparent", grid: { left: 48, right: 14, top: 24, bottom: 34 },
    legend: { data: ["A", "B"], textStyle: { color: "#7d8b9a" }, right: 10 },
    tooltip: { ...TOOLTIP, formatter: (p: any) => `${p.data[2]}<br>R ${Math.round(p.data[0])} m / ${p.data[1]?.toFixed(2)} %` },
    xAxis: { type: "log", name: "最小R [m]", min: 10, ...AXIS }, yAxis: { type: "value", name: "100m上り [%]", ...AXIS },
    series: [
      { name: "A", type: "scatter", symbolSize: 7, data: a.filter((p) => p.y != null).map((p) => [p.x, p.y, p.ramp_id]), itemStyle: { color: A } },
      { name: "B", type: "scatter", symbolSize: 7, data: b.filter((p) => p.y != null).map((p) => [p.x, p.y, p.ramp_id]), itemStyle: { color: B },
        markLine: { symbol: "none", silent: true, lineStyle: { color: "#ffd84a", type: "dashed" }, label: { color: "#ffd84a", fontSize: 9 },
          data: [{ xAxis: 100, name: "R100" }, { yAxis: 5, name: "5%" }] } },
    ],
  }) as any, [a, b]);
  const onEvent = useMemo(() => ({ click: (p: any) => p.data?.[2] && onClick?.(p.data[2]) }), [onClick]);
  return <EChart option={option} height={280} onEvent={onEvent} testId="scatter-chart" />;
}

/** 形状分類構成の帯グラフ。 */
export function CompositionBar({ a, b, palette }: { a: Record<string, number>; b: Record<string, number>; palette: (k: string) => string }) {
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
  const ta = Object.values(a).reduce((x, y) => x + y, 0) || 1;
  const tb = Object.values(b).reduce((x, y) => x + y, 0) || 1;
  const option = useMemo(() => ({
    backgroundColor: "transparent", grid: { left: 30, right: 14, top: 28, bottom: 10 },
    legend: { textStyle: { color: "#7d8b9a", fontSize: 10 }, top: 0, type: "scroll" },
    tooltip: { ...TOOLTIP, formatter: (p: any) => `${p.seriesName}：${(p.value * 100).toFixed(1)}%` },
    xAxis: { type: "value", max: 1, show: false }, yAxis: { type: "category", data: ["B", "A"], ...AXIS },
    series: keys.map((k) => ({ name: k, type: "bar", stack: "s", data: [(b[k] ?? 0) / tb, (a[k] ?? 0) / ta], itemStyle: { color: palette(k) } })),
  }) as any, [a, b, keys, ta, tb, palette]);
  return <EChart option={option} height={130} testId="composition-chart" />;
}

/** 箱ひげ図（A 上段、B 下段）。インライン SVG。 */
export function BoxPlotCell({ a, b, min, max }: { a: any; b: any; min: number; max: number }) {
  const W = 140, H = 26;
  const x = (v: number) => (max > min ? ((v - min) / (max - min)) * (W - 4) + 2 : W / 2);
  const box = (d: any, y: number, c: string) =>
    d?.n ? (
      <g>
        <line x1={x(d.min)} x2={x(d.max)} y1={y} y2={y} stroke={c} strokeWidth={1} />
        <rect x={x(d.q1)} y={y - 4} width={Math.max(1, x(d.q3) - x(d.q1))} height={8} fill="none" stroke={c} />
        <line x1={x(d.median)} x2={x(d.median)} y1={y - 4} y2={y + 4} stroke="#fff" />
      </g>
    ) : null;
  return (
    <svg className="boxcell" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="箱ひげ図">
      {box(a, 7, A)}
      {box(b, 19, B)}
    </svg>
  );
}
