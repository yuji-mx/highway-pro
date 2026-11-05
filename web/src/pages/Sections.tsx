import { useMutation, useQuery } from "@tanstack/react-query";
import type { FeatureCollection } from "geojson";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadBlob, errMsg } from "../api/client";
import ProfileChart, { type Marker } from "../components/charts/ProfileChart";
import { Empty, ErrorBox, Kpi, QualityBadge, Skeleton, fmt } from "../components/common/ui";
import RoadMriMap, { type Overlay } from "../components/map/RoadMriMap";
import DataTable, { type Col } from "../components/tables/DataTable";
import { t } from "../i18n";

const DEF_JA: Record<string, string> = {
  standard: "標準（分流・合流の中点間）", diverge_to_diverge: "分流点→分流点", merge_to_diverge: "合流点→分流点（純粋な本線区間）",
  facility_point: "施設代表点間", toll_to_toll: "料金所間", ramp_end_to_ramp_end: "流入始端→流出終端",
  mainline_connection: "本線接続点間", centerline_avg: "上下線平均（中心線距離）",
};
const SEV: Record<string, string> = { error: "要対応", warn: "警告", info: "情報" };

function SectionTab() {
  const [sp, setSp] = useSearchParams();
  const list = useQuery({ queryKey: ["sections-all"], queryFn: () => api.get<any>("/sections") });
  const items: any[] = list.data?.items ?? [];
  const fac = sp.get("facility");
  const route = sp.get("route") ?? items[0]?.route_ref ?? "";
  const id = sp.get("id") ?? (fac ? items.find((s) => s.from_facility_id === fac)?.section_id : items.find((s) => s.route_ref === route)?.section_id);
  const sec = useQuery({ queryKey: ["section", id], queryFn: () => api.get<any>(`/sections/${encodeURIComponent(id!)}`), enabled: !!id });
  const prof = useQuery({ queryKey: ["secprof", id], queryFn: () => api.get<any>(`/sections/${encodeURIComponent(id!)}/profile`), enabled: !!id });
  const secFocus = useMemo<[number, number, number, number] | null>(() => {
    const c = sec.data?.geometry?.coordinates as number[][] | undefined;
    if (!c?.length) return null;
    const xs = c.map((p) => p[0]), ys = c.map((p) => p[1]);
    return [Math.min(...xs) - 0.01, Math.min(...ys) - 0.01, Math.max(...xs) + 0.01, Math.max(...ys) + 0.01];
  }, [sec.data]);
  const overlay = useMemo<Overlay[]>(() => sec.data?.geometry ? [{ id: "sec", color: "#ff7a2e", width: 3,
    data: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: sec.data.geometry }] } as FeatureCollection }] : [], [sec.data]);
  if (list.isLoading) return <Skeleton h={300} />;
  if (list.error) return <ErrorBox error={list.error} retry={() => list.refetch()} />;
  if (!items.length) return <Empty />;
  const routeItems = items.filter((s) => s.route_ref === route);
  const s = sec.data;
  const fromOpts = [...new Map(routeItems.map((x) => [x.from_facility_id, x.from_name])).entries()];
  const pick = (from: string, to?: string) => {
    const c = routeItems.find((x) => x.from_facility_id === from && (!to || x.to_facility_id === to));
    if (c) setSp({ route, id: c.section_id });
  };
  const cols: Col<any>[] = [
    { key: "direction", label: "方向", get: (r) => t(`dir.${r.direction}`, r.direction) },
    { key: "from_name", label: "起点" }, { key: "to_name", label: "終点" },
    { key: "dist_standard_m", label: "距離 [km]", num: true, get: (r) => (r.dist_standard_m ?? 0) / 1000 },
    { key: "grade_up_max_w100", label: "最大上り(100m)", num: true }, { key: "grade_down_max_w100", label: "最大下り(100m)", num: true },
    { key: "elev_gain_m", label: "累積上昇", num: true }, { key: "min_radius_m", label: "最小R", num: true },
    { key: "anomaly_flags", label: "異常", get: (r) => (r.anomaly_flags ?? []).join(", ") },
    { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
  ];
  const pts = prof.data?.points ?? [];
  const markers: Marker[] = s ? [{ s: 0, label: s.from_name ?? "起点", color: "#ffb53d" }, { s: pts.length ? pts[pts.length - 1].s_m : 0, label: s.to_name ?? "終点", color: "#ffb53d" }] : [];
  return (
    <div className="stack">
      <div className="card row">
        <label>路線 <select value={route} onChange={(e) => setSp({ route: e.target.value })}>{(list.data.routes ?? []).map((r: string) => <option key={r}>{r}</option>)}</select></label>
        <label>起点 <select value={s?.from_facility_id ?? ""} onChange={(e) => pick(e.target.value)} data-testid="sec-from">
          {fromOpts.map(([fid, name]) => <option key={fid} value={fid}>{name ?? fid}</option>)}</select></label>
        <label>終点 <select value={s?.to_facility_id ?? ""} onChange={(e) => pick(s.from_facility_id, e.target.value)}>
          {routeItems.filter((x) => x.from_facility_id === s?.from_facility_id).map((x) => <option key={x.to_facility_id} value={x.to_facility_id}>{x.to_name}</option>)}</select></label>
        <button disabled={!s?.reverse_section_id} onClick={() => setSp({ route, id: s.reverse_section_id })} data-testid="sec-reverse">⇄ 方向反転</button>
        {s && <span className="badge b-ok">{t(`dir.${s.direction}`, s.direction)}（{s.to_name}方面）</span>}
      </div>
      {sec.isLoading || !s ? <Skeleton h={300} /> : sec.error ? <ErrorBox error={sec.error} /> : (
        <>
          <div className="kpis">
            <Kpi label="標準距離" value={(s.dist_standard_m ?? 0) / 1000} unit="km" d={2} />
            <Kpi label="平均勾配" value={s.grade_mean} unit="%" d={2} />
            <Kpi label="最大上り（100m）" value={s.grade_up_max_w100} unit="%" d={2} />
            <Kpi label="最大下り（100m）" value={s.grade_down_max_w100} unit="%" d={2} />
            <Kpi label="累積上昇／下降" value={`${fmt(s.elev_gain_m, 0)} / ${fmt(s.elev_loss_m, 0)}`} unit="m" />
            <Kpi label="最高／最低標高" value={`${fmt(s.z_max, 0)} / ${fmt(s.z_min, 0)}`} unit="m" />
            <Kpi label="最小R" value={s.min_radius_m} unit="m" />
            <Kpi label="トンネル／橋梁" value={`${fmt(s.tunnel_len_m, 0)} / ${fmt(s.bridge_len_m, 0)}`} unit="m" />
          </div>
          <div className="grid2">
            <div className="card"><h3>標高・勾配プロファイル</h3>{prof.isLoading ? <Skeleton h={240} /> : <ProfileChart points={pts} markers={markers} dashedRaw />}</div>
            <RoadMriMap height={330} overlays={overlay} focus={secFocus} testId="section-map" />
          </div>
          <div className="grid2">
            <div className="card"><h3>距離の定義別</h3><table className="dt"><tbody>
              {(s.distances ?? []).map((d: any) => <tr key={d.definition}><td>{DEF_JA[d.definition] ?? d.definition}</td><td className="num">{fmt(d.distance_m / 1000, 3, " km")}</td></tr>)}
            </tbody></table></div>
            <div className="card"><h3>経路異常</h3>
              {(s.flags ?? []).length ? (s.flags ?? []).map((f: any) => <div key={f.flag_id} className={f.severity === "error" ? "error" : "warn"} style={{ textAlign: "left", border: "none", padding: 2 }}>[{SEV[f.severity]}] {f.code}：{f.message_ja}</div>) : <div className="note">異常はありません。</div>}
            </div>
          </div>
        </>
      )}
      <div className="card"><h3>路線 {route} の全区間（クリックで切替）</h3>
        <DataTable rows={routeItems} cols={cols} onRow={(r) => setSp({ route, id: r.section_id })} rowClass={(r) => (r.section_id === id ? "hl" : "")} maxHeight={300} testId="section-table" /></div>
    </div>
  );
}

function RouteTab() {
  const facs = useQuery({ queryKey: ["facilities-all"], queryFn: () => api.get<any>("/facilities?limit=5000&sort=name") });
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [via, setVia] = useState("");
  const [sel, setSel] = useState(0);
  const run = useMutation({ mutationFn: () => api.post<any>("/route", { from_facility_id: from, to_facility_id: to, via_facility_ids: via ? [via] : [] }) });
  const routes: any[] = useMemo(() => run.data?.routes ?? [], [run.data]);
  const r = routes[sel];
  const overlay = useMemo<Overlay[]>(() => routes.map((x, i) => ({ id: `r${i}`, color: i === sel ? "#ff7a2e" : "#4a5663", width: i === sel ? 3 : 2,
    data: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: x.geometry }] } as FeatureCollection })), [routes, sel]);
  const opts = (facs.data?.items ?? []).map((f: any) => <option key={f.facility_id} value={f.facility_id}>{f.name ?? f.facility_id}</option>);
  const dl = async (fmt_: string) => {
    const b = await api.blob(`/route?format=${fmt_}`, { from_facility_id: from, to_facility_id: to, via_facility_ids: via ? [via] : [] });
    downloadBlob(b, `route.${fmt_ === "geojson" ? "geojson" : "csv"}`);
  };
  return (
    <div className="stack">
      <div className="card row">
        <label>起点 <select value={from} onChange={(e) => setFrom(e.target.value)} data-testid="route-from"><option value="">選択</option>{opts}</select></label>
        <label>経由地 <select value={via} onChange={(e) => setVia(e.target.value)}><option value="">なし</option>{opts}</select></label>
        <label>終点 <select value={to} onChange={(e) => setTo(e.target.value)} data-testid="route-to"><option value="">選択</option>{opts}</select></label>
        <button className="primary" disabled={!from || !to || run.isPending} onClick={() => { setSel(0); run.mutate(); }} data-testid="route-run">経路を探索</button>
        {r && <><button onClick={() => dl("geojson")}>GeoJSON</button><button onClick={() => dl("csv")}>CSV</button></>}
      </div>
      {run.error && <div className="error">{errMsg(run.error)}</div>}
      {routes.length > 0 && (
        <>
          <table className="dt" data-testid="route-table"><thead><tr><th>候補</th><th>総距離</th><th>最大上り</th><th>累積上昇</th><th>最小R</th><th>トンネル</th><th>橋梁</th><th>通過施設</th></tr></thead><tbody>
            {routes.map((x, i) => (
              <tr key={i} className={`click ${i === sel ? "hl" : ""}`} onClick={() => setSel(i)}>
                <td>#{i + 1}</td><td className="n">{fmt(x.length_m / 1000, 2, " km")}</td><td className="n">{fmt(x.max_grade_up_w100, 2, "%")}</td>
                <td className="n">{fmt(x.elev_gain_m, 0, " m")}</td><td className="n">{fmt(x.min_radius_m, 0, " m")}</td>
                <td className="n">{fmt(x.tunnel_len_m, 0)}</td><td className="n">{fmt(x.bridge_len_m, 0)}</td><td>{x.passed_facilities.length}</td>
              </tr>))}
          </tbody></table>
          <div className="grid2">
            <div className="card"><h3>選択経路のプロファイル</h3><ProfileChart points={r.profile.map((p: any) => ({ ...p, low_confidence: p.structure !== "none" }))} dashedRaw /></div>
            <RoadMriMap height={300} overlays={overlay} testId="route-map" />
          </div>
          <div className="grid2">
            <div className="card"><h3>通過施設と区間距離</h3><table className="dt"><tbody>
              {r.passed_facilities.map((f: any, i: number) => <tr key={i}><td>{f.name}</td><td className="num">{fmt(f.s_m / 1000, 2, " km")}</td>
                <td className="num">{r.legs[i] ? `→ ${fmt(r.legs[i].distance_m / 1000, 2, " km")}` : ""}</td></tr>)}
            </tbody></table></div>
            <div className="card"><h3>通過ランプ（JCT 連絡）</h3>{r.ramps.length ? r.ramps.map((x: string) => <div key={x} className="mono">{x}</div>) : <div className="note">なし</div>}</div>
          </div>
        </>
      )}
    </div>
  );
}

export default function Sections() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "section";
  return (
    <div className="page">
      <h1>IC/JCT 間区間</h1>
      <div className="tabs">
        <button className={tab === "section" ? "on" : ""} onClick={() => setSp({ tab: "section" })}>区間</button>
        <button className={tab === "route" ? "on" : ""} onClick={() => setSp({ tab: "route" })} data-testid="tab-route">ルート</button>
      </div>
      {tab === "route" ? <RouteTab /> : <SectionTab />}
    </div>
  );
}
