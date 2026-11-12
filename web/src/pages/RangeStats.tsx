import { useMutation, useQuery } from "@tanstack/react-query";
import type { FeatureCollection } from "geojson";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, errMsg } from "../api/client";
import type { RangeSpec } from "../api/types";
import { BoxPlotCell, CompositionBar, EcdfChart, HistogramChart, ScatterChart } from "../components/charts/StatsCharts";
import { b64 } from "../components/common/queryText";
import { ErrorBox, Skeleton, fmt } from "../components/common/ui";
import { SHAPE_PALETTE } from "../components/map/colorScales";
import { loadLayer } from "../components/map/layers";
import RoadMriMap, { type Overlay } from "../components/map/RoadMriMap";
import { useRanges, useToast } from "../state/stores";

function RangeCard({ slot, r, counts, names }: { slot: "a" | "b"; r: RangeSpec | null; counts?: any; names?: string[] }) {
  const setRange = useRanges((s) => s.setRange);
  return (
    <div className="card" style={{ borderTop: `3px solid var(--range-${slot})` }} data-testid={`range-card-${slot}`}>
      <h3>範囲 {slot.toUpperCase()}：{r?.label ?? "未設定"}</h3>
      <div className="note">種類：{r?.type ?? "—"} {r?.route_refs ? `（${r.route_refs.join(", ")}）` : ""}{r?.bbox ? `（${r.bbox.map((v) => v.toFixed(2)).join(", ")}）` : ""}</div>
      {r?.type === "facilities" && (
        <details open><summary className="note">個別選択（{r.facility_ids?.length} 施設）</summary>
          <div style={{ maxHeight: 100, overflow: "auto" }}>
            {(r.facility_ids ?? []).map((id) => (
              <label key={id} className="row" style={{ gap: 4 }}><input type="checkbox" checked onChange={() =>
                setRange(slot, { ...r, facility_ids: r.facility_ids!.filter((x) => x !== id) })} />{names?.find((n) => n.startsWith(id + "|"))?.split("|")[1] ?? id}</label>
            ))}
          </div>
        </details>
      )}
      {counts && <div className="row num"><span>施設 {counts.facility}</span><span>ランプ {counts.ramp}</span><span>区間 {counts.section}</span></div>}
    </div>
  );
}

export default function RangeStats() {
  const nav = useNavigate();
  const toast = useToast((s) => s.push);
  const [sp, setSp] = useSearchParams();
  const { a, b, setRange, swap } = useRanges();
  const [metricKey, setMetricKey] = useState<string | null>(null);
  const [view, setView] = useState<"hist" | "ecdf">("hist");
  const [overlap, setOverlap] = useState("keep_warn");
  const presets = useQuery({ queryKey: ["stats-presets"], queryFn: () => api.get<any[]>("/stats/presets") });
  const prQ = useQuery({ queryKey: ["presets"], queryFn: () => api.get<any[]>("/presets") });
  const facs = useQuery({ queryKey: ["facilities-all"], queryFn: () => api.get<any>("/facilities?limit=5000&sort=name") });
  // URL ⇄ ストア（ブラウザの戻る・進むで範囲を復元）
  useEffect(() => {
    const ua = sp.get("a"), ub = sp.get("b");
    if (ua) setRange("a", b64.dec(ua));
    if (ub) setRange("b", b64.dec(ub));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  useEffect(() => {
    if (!a && !b && presets.data?.length && !sp.get("a")) {
      const p = presets.data[0];
      setSp({ a: b64.enc(p.range_a), b: b64.enc(p.range_b) }, { replace: true });
    }
  }, [a, b, presets.data, sp, setSp]);
  const applyPreset = (id: string) => {
    const p = presets.data?.find((x) => x.id === id);
    if (p) setSp({ a: b64.enc(p.range_a), b: b64.enc(p.range_b) });
  };
  const hitQuery = prQ.data?.find((p) => p.id === "spec_example")?.query;
  const res = useQuery({
    queryKey: ["stats", a, b, overlap, hitQuery],
    queryFn: () => api.post<any>("/stats/compare", { range_a: a, range_b: b, overlap, hit_query: hitQuery }),
    enabled: !!a && !!b,
  });
  const d = res.data;
  const m = d?.metrics?.find((x: any) => x.key === metricKey) ?? d?.metrics?.[0];
  const [layerFac, setLayerFac] = useState<FeatureCollection | null>(null);
  const [layerMain, setLayerMain] = useState<FeatureCollection | null>(null);
  useEffect(() => {
    loadLayer("facilities").then(setLayerFac).catch(() => setLayerFac(null));
    loadLayer("mainline").then(setLayerMain).catch(() => setLayerMain(null));
  }, []);
  const overlays = useMemo<Overlay[]>(() => {
    if (!d || !layerFac) return [];
    const A = new Set<string>(d.a.facility_ids), B = new Set<string>(d.b.facility_ids);
    const pick = (fn: (id: string) => boolean): FeatureCollection => ({ type: "FeatureCollection", features: layerFac.features.filter((f) => fn(String(f.properties?.facility_id))) });
    const secs = (ids: string[]): FeatureCollection => {
      const routes = new Set(ids.map((s) => s.split("-")[0]));
      return { type: "FeatureCollection", features: (layerMain?.features ?? []).filter((f) => routes.has(String(f.properties?.route_ref))) };
    };
    return [
      { id: "sa", data: secs(d.a.section_ids), color: "#38e1ff", width: 2 },
      { id: "sb", data: secs(d.b.section_ids), color: "#ff7a2e", width: 1.5 },
      { id: "fa", data: pick((id) => A.has(id) && !B.has(id)), color: "#38e1ff", kind: "circle" },
      { id: "fb", data: pick((id) => B.has(id) && !A.has(id)), color: "#ff7a2e", kind: "circle" },
      { id: "fab", data: pick((id) => A.has(id) && B.has(id)), color: "#e8e8ff", kind: "circle", width: 11 },
    ];
  }, [d, layerFac, layerMain]);
  const saveRange = useMutation({
    mutationFn: async () => {
      await api.post("/saved-ranges", { name: a?.label ?? "範囲A", spec: a });
      await api.post("/saved-ranges", { name: b?.label ?? "範囲B", spec: b });
    },
    onSuccess: () => toast("範囲定義を保存しました"),
    onError: (e) => toast(errMsg(e), true),
  });
  const csv = () => {
    if (!d) return;
    const rows = [["指標", "対象", "単位", "n_A", "n_B", "平均A", "平均B", "中央値A", "中央値B", "P90A", "P90B", "最大A", "最大B", "中央値差", "CI下限", "CI上限", "Cliffs_delta", "Cohens_d", "検定", "p", "q", "参考値", "範囲A", "範囲B", "run_id"]];
    for (const x of d.metrics) rows.push([x.label, x.target, x.unit, x.a.n, x.b.n, x.a.mean, x.b.mean, x.a.median, x.b.median, x.a.p90, x.b.p90, x.a.max, x.b.max,
      x.median_diff, x.median_diff_ci?.[0], x.median_diff_ci?.[1], x.cliffs_delta, x.cohens_d, x.test, x.p, x.q, x.reference_only, d.a.label, d.b.label, d.run_id]);
    for (const s of d.summary) rows.push([s.label, s.kind, "", s.a_n ?? "", s.b_n ?? "", s.a, s.b, "", "", "", "", "", "", s.diff ?? "", s.a_ci?.[0] ?? "", s.a_ci?.[1] ?? "", "", "", s.test, s.p, s.q, s.reference_only, d.a.label, d.b.label, d.run_id]);
    const text = "﻿" + rows.map((r) => r.map((v) => (v == null ? "" : String(v).includes(",") ? `"${v}"` : String(v))).join(",")).join("\n");
    const el = document.createElement("a");
    el.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    el.download = "stats_compare.csv";
    el.click();
  };
  const report = () => {
    if (!d) return;
    const html = `<!doctype html><meta charset="utf-8"><title>統計比較レポート</title><style>body{background:#05070a;color:#d7dee7;font:13px "Noto Sans JP",sans-serif;padding:20px}td,th{border-bottom:1px solid #1b232d;padding:3px 6px}</style>
<h1>範囲統計比較：${d.a.label} vs ${d.b.label}</h1><ul>${d.insights_ja.map((s: string) => `<li>${s}</li>`).join("")}</ul>
<table><tr><th>指標</th><th>n A/B</th><th>中央値 A/B</th><th>中央値差 [95%CI]</th><th>δ</th><th>p</th><th>q</th></tr>${d.metrics.map((x: any) => `<tr><td>${x.label}</td><td>${x.a.n}/${x.b.n}</td><td>${fmt(x.a.median, x.digits)}/${fmt(x.b.median, x.digits)}</td><td>${fmt(x.median_diff, x.digits)} [${fmt(x.median_diff_ci?.[0], x.digits)}, ${fmt(x.median_diff_ci?.[1], x.digits)}]</td><td>${fmt(x.cliffs_delta, 2)}</td><td>${fmt(x.p, 3)}</td><td>${fmt(x.q, 3)}</td></tr>`).join("")}</table>
<p>${d.notes_ja.join("<br>")}</p><p>run ${d.run_id} / ${d.provenance?.attribution}</p>`;
    const el = document.createElement("a");
    el.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    el.download = "stats_report.html";
    el.click();
  };
  const shapes = useMemo(() => [...new Set(Object.keys(d?.categorical?.[0]?.a ?? {}).concat(Object.keys(d?.categorical?.[0]?.b ?? {})))].sort(), [d]);
  const names = (facs.data?.items ?? []).map((f: any) => `${f.facility_id}|${f.name}`);
  const strong = (x: any) => (x.p != null && x.p < 0.05) || (x.q != null && x.q < 0.1) || (x.cliffs_delta != null && Math.abs(x.cliffs_delta) >= 0.33);
  return (
    <div className="page">
      <h1>範囲統計比較</h1>
      <div className="card row" style={{ marginBottom: 12 }}>
        <span className="note">比較プリセット</span>
        <select onChange={(e) => applyPreset(e.target.value)} defaultValue="" data-testid="stats-preset">
          <option value="" disabled>選択</option>{(presets.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
        <button onClick={() => { swap(); setSp({ a: b64.enc(b), b: b64.enc(a) }); }}>A ⇄ B</button>
        <label>重複の扱い <select value={overlap} onChange={(e) => setOverlap(e.target.value)}>
          <option value="keep_warn">含めたまま比較（警告）</option><option value="exclude_both">両方から除外</option><option value="keep_in_a">A 側にだけ残す</option></select></label>
        <span style={{ marginLeft: "auto" }} className="row">
          <button disabled={!d} onClick={csv} data-testid="stats-csv">統計表 CSV</button>
          <button disabled={!d} onClick={report}>統計レポート</button>
          <button disabled={!a || !b} onClick={() => saveRange.mutate()}>範囲定義を保存</button>
        </span>
      </div>
      <div className="grid2">
        <RangeCard slot="a" r={a} counts={d?.a?.counts} names={names} />
        <RangeCard slot="b" r={b} counts={d?.b?.counts} names={names} />
      </div>
      {d?.overlap && (d.overlap.facilities > 0 || d.overlap.ramps > 0) && <div className="warn" style={{ marginTop: 6 }}>⚠ 重複：施設 {d.overlap.facilities}・ランプ {d.overlap.ramps}（{overlap === "keep_warn" ? "含めたまま比較。独立性の仮定が崩れます" : "指定の方法で処理済み"}）</div>}
      {!a || !b ? <div className="empty" style={{ marginTop: 12 }}>範囲 A・B を設定してください（プリセット、または条件検索の「範囲Aへ／範囲Bへ」）。</div>
        : res.isLoading ? <Skeleton h={400} /> : res.error ? <ErrorBox error={res.error} retry={() => res.refetch()} /> : d && (
          <div className="stack" style={{ marginTop: 12 }} data-testid="stats-result">
            <div className="grid2">
              <RoadMriMap height={320} overlays={overlays} showLabels={false} testId="stats-map" />
              <div className="card"><h3>集計サマリー</h3>
                <table className="dt" data-testid="stats-summary"><thead><tr><th>項目</th><th>A</th><th>B</th><th>q</th></tr></thead><tbody>
                  <tr><td>施設構成</td><td>{d.a.counts.facility}</td><td>{d.b.counts.facility}</td><td></td></tr>
                  <tr><td>ランプ数</td><td>{d.a.counts.ramp}</td><td>{d.b.counts.ramp}</td><td></td></tr>
                  <tr><td>区間延長</td><td>{fmt(d.summary.find((s: any) => s.key === "ramp_density")?.a_len_km, 1, " km")}</td><td>{fmt(d.summary.find((s: any) => s.key === "ramp_density")?.b_len_km, 1, " km")}</td><td></td></tr>
                  {d.summary.map((s: any) => (
                    <tr key={s.key}><td>{s.label}</td>
                      <td className="num">{s.kind === "ratio" ? `${fmt((s.a ?? NaN) * 100, 1)}% [${fmt((s.a_ci?.[0] ?? NaN) * 100, 0)}–${fmt((s.a_ci?.[1] ?? NaN) * 100, 0)}]` : fmt(s.a, 2)}</td>
                      <td className="num">{s.kind === "ratio" ? `${fmt((s.b ?? NaN) * 100, 1)}% [${fmt((s.b_ci?.[0] ?? NaN) * 100, 0)}–${fmt((s.b_ci?.[1] ?? NaN) * 100, 0)}]` : fmt(s.b, 2)}</td>
                      <td className="num">{fmt(s.q, 2)}</td></tr>))}
                </tbody></table></div>
            </div>
            <div className="card"><h3>主な差異</h3><ul data-testid="insights">{d.insights_ja.map((s: string) => <li key={s}>{s}</li>)}</ul>
              {d.notes_ja.map((s: string) => <div key={s} className="note">{s}</div>)}</div>
            <div className="card"><h3>指標別統計表（行クリックで分布図を切替）</h3>
              <div className="tablewrap"><table className="dt" data-testid="stats-table"><thead><tr>
                <th>指標（対象）</th><th>単位</th><th>n A/B</th><th>平均 A/B</th><th>中央値 A/B</th><th>P90 A/B</th><th>最大 A/B</th><th>中央値差 [95%CI]</th><th>δ</th><th>d</th><th>p</th><th>q</th><th>分布</th></tr></thead>
                <tbody>{d.metrics.map((x: any) => {
                  const lo = Math.min(x.a.min ?? Infinity, x.b.min ?? Infinity), hi = Math.max(x.a.max ?? -Infinity, x.b.max ?? -Infinity);
                  return (
                    <tr key={x.key} className={`click ${x.key === m?.key ? "hl" : ""}`} onClick={() => setMetricKey(x.key)} style={strong(x) ? { fontWeight: 700 } : undefined}>
                      <td>{x.label}<span className="note">（{x.target}）</span>{x.reference_only && <span className="note"> 参考値</span>}</td><td>{x.unit}</td>
                      <td className="n">{x.a.n}/{x.b.n}</td><td className="n">{fmt(x.a.mean, x.digits)}/{fmt(x.b.mean, x.digits)}</td>
                      <td className="n">{fmt(x.a.median, x.digits)}/{fmt(x.b.median, x.digits)}</td><td className="n">{fmt(x.a.p90, x.digits)}/{fmt(x.b.p90, x.digits)}</td>
                      <td className="n">{fmt(x.a.max, x.digits)}/{fmt(x.b.max, x.digits)}</td>
                      <td className="n">{fmt(x.median_diff, x.digits)} [{fmt(x.median_diff_ci?.[0], x.digits)}, {fmt(x.median_diff_ci?.[1], x.digits)}]</td>
                      <td className="n" style={{ color: Math.abs(x.cliffs_delta ?? 0) >= 0.33 ? "var(--amber)" : undefined }}>{fmt(x.cliffs_delta, 2)}</td>
                      <td className="n">{fmt(x.cohens_d, 2)}</td>
                      <td className="n" style={{ color: (x.p ?? 1) < 0.05 ? "var(--amber)" : undefined }}>{fmt(x.p, 3)}</td>
                      <td className="n" style={{ color: (x.q ?? 1) < 0.1 ? "var(--amber)" : undefined }}>{fmt(x.q, 3)}</td>
                      <td>{Number.isFinite(lo) && <BoxPlotCell a={x.a} b={x.b} min={lo} max={hi} />}</td>
                    </tr>);
                })}</tbody></table></div></div>
            <div className="grid2">
              <div className="card"><h3 className="row">分布：{m?.label}
                <span className="row" style={{ marginLeft: "auto" }}><button className={view === "hist" ? "primary" : ""} onClick={() => setView("hist")}>ヒストグラム</button>
                  <button className={view === "ecdf" ? "primary" : ""} onClick={() => setView("ecdf")}>ECDF</button></span></h3>
                {m && (view === "hist" ? <HistogramChart m={m} /> : <EcdfChart m={m} />)}</div>
              <div className="card"><h3>散布図（最小R × 100m 上り勾配）</h3>
                <ScatterChart a={d.scatter.a} b={d.scatter.b} onClick={(rid) => nav(`/ramp/${rid}`)} /></div>
            </div>
            <div className="card"><h3>形状分類の構成 <span className="note">{d.categorical[0]?.test}：p={fmt(d.categorical[0]?.p, 3)}・q={fmt(d.categorical[0]?.q, 3)}</span></h3>
              <CompositionBar a={d.categorical[0]?.a ?? {}} b={d.categorical[0]?.b ?? {}} palette={(k) => SHAPE_PALETTE[shapes.indexOf(k) % SHAPE_PALETTE.length]} /></div>
          </div>
        )}
    </div>
  );
}
