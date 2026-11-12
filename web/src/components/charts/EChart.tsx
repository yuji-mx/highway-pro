import * as echarts from "echarts";
import { useEffect, useRef } from "react";

/** ECharts のラッパー。onEvent でマウスイベントを受け取る。 */
export default function EChart({
  option, height = 240, onEvent, testId, className,
}: {
  option: echarts.EChartsOption;
  height?: number | string;
  onEvent?: Record<string, (p: any, chart: echarts.ECharts) => void>;
  testId?: string;
  className?: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      c.dispose();
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption({ animation: false, ...option }, true);
  }, [option]);
  useEffect(() => {
    const c = chart.current;
    if (!c || !onEvent) return;
    const zr = c.getZr();
    const handlers = Object.entries(onEvent);
    for (const [ev, fn] of handlers) {
      if (ev.startsWith("zr:")) zr.on(ev.slice(3) as any, (p: any) => fn(p, c));
      else c.on(ev, (p: any) => fn(p, c));
    }
    return () => {
      for (const [ev] of handlers) {
        if (ev.startsWith("zr:")) zr.off(ev.slice(3) as any);
        else c.off(ev);
      }
    };
  }, [onEvent]);
  return <div ref={el} className={className ?? "chart"} style={{ height }} data-testid={testId} />;
}

export const AXIS = {
  axisLine: { lineStyle: { color: "#28323e" } },
  splitLine: { lineStyle: { color: "#1b232d" } },
  axisLabel: { color: "#7d8b9a", fontSize: 10 },
  nameTextStyle: { color: "#7d8b9a", fontSize: 10 },
};
export const TOOLTIP = { backgroundColor: "#11161d", borderColor: "#28323e", textStyle: { color: "#d7dee7", fontSize: 11 } };
