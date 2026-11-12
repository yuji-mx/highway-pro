import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FeatureCollection } from "geojson";
import { useMemo, useState } from "react";
import { api, errMsg, waitJob } from "../api/client";
import { ErrorBox, Skeleton } from "../components/common/ui";
import { clearLayerCache } from "../components/map/layers";
import RoadMriMap, { type Overlay } from "../components/map/RoadMriMap";
import { useToast } from "../state/stores";

const STATUS: Record<string, [string, string]> = { processed: ["処理済み", "high"], fetched: ["取得済み", "ok"], cached: ["キャッシュあり", "medium"], missing: ["未取得", "low"] };
const TABS = ["都道府県", "市区町村", "州・郡", "矩形", "中心＋半径", "路線名", "IC/JCT 名", "GeoJSON", "地図で選択"];
const STEPS = ["ingest", "network", "facilities", "ramps", "sections", "metrics", "classify", "quality", "edits", "publish"];
const PHASES = [["P1 成立性確認", "MVP"], ["P2 車両性能向け解析", "MVP"], ["P3 形状分類", "MVP"], ["P3+ 比較・統計", "MVP"], ["P4 全国展開", "設計のみ"], ["P5 北米展開", "設計のみ"], ["P6 実走データ統合", "IF のみ"]];

export default function Region() {
  const qc = useQueryClient();
  const toast = useToast((s) => s.push);
  const [country, setCountry] = useState("JP");
  const [tab, setTab] = useState(3);
  const [sel, setSel] = useState<string | null>(null);
  const [bbox, setBbox] = useState("139.72,36.515,139.90,36.655");
  const [circle, setCircle] = useState({ lon: "139.835", lat: "36.605", r: "30" });
  const [geo, setGeo] = useState("");
  const [text, setText] = useState("");
  const [progress, setProgress] = useState<{ p: number; m?: string } | null>(null);
  const regions = useQuery({ queryKey: ["regions"], queryFn: () => api.get<any[]>("/regions") });
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const region = (regions.data ?? []).find((r) => r.id === sel) ?? (regions.data ?? []).find((r) => r.status === "processed");
  const rangeSpec = (): any => {
    if (tab === 3) return { type: "bbox", bbox: bbox.split(",").map(Number) };
    if (tab === 4) return { type: "circle", circle: { lon: Number(circle.lon), lat: Number(circle.lat), radius_km: Number(circle.r) } };
    if (tab === 7 || tab === 8) return { type: "polygon", polygon: JSON.parse(geo) };
    if (tab === 5) return { type: "route", route_refs: text.split(",").map((s) => s.trim()) };
    if (tab === 2) return { type: "admin", admin_codes: text.split(",").map((s) => s.trim()), bbox: region?.range?.bbox };
    return region?.range;
  };
  const preview = useMemo<Overlay[]>(() => {
    try {
      const r = tab === 3 ? { bbox: bbox.split(",").map(Number) } : region?.range;
      const b = r?.bbox;
      if (!b || b.length !== 4 || b.some((v: number) => !Number.isFinite(v))) return [];
      const ring = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]];
      return [{ id: "range", color: "#ffd84a", width: 1.5, data: { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: ring } }] } as FeatureCollection }];
    } catch {
      return [];
    }
  }, [tab, bbox, region]);
  const apply = useMutation({
    mutationFn: async () => {
      let id = region?.id;
      if ((tab >= 3 && tab !== 6) || tab === 2) {
        const spec = rangeSpec();
        id = `custom_${Date.now()}`;
        await api.post("/regions", { id, label: "カスタム範囲", country: country === "NA" ? "US" : country, range: spec,
          dem: region?.dem ?? (country === "NA" ? "us" : "jp"), pbf: region?.pbf ?? {} });
      }
      const j: any = await api.post("/pipeline/run", { region: id });
      await waitJob(j.job_id, (p, m) => setProgress({ p, m }));
    },
    onSuccess: () => { toast("パイプラインが完了しました"); clearLayerCache(); qc.invalidateQueries(); setProgress(null); },
    onError: (e) => { toast(errMsg(e), true); setProgress(null); },
  });
  if (regions.isLoading) return <div className="page"><Skeleton h={400} /></div>;
  if (regions.error) return <div className="page"><ErrorBox error={regions.error} retry={() => regions.refetch()} /></div>;
  const m = meta.data;
  return (
    <div className="page">
      <h1>地域・路線選択
        <span className="row" style={{ marginLeft: 12 }}>
          {[["JP", "日本"], ["NA", "北米"]].map(([k, l]) => <button key={k} className={country === k ? "primary" : ""} onClick={() => setCountry(k)}>{l}</button>)}
        </span></h1>
      {country === "NA" && (
        <div className="note" style={{ marginBottom: 8 }} data-testid="na-note">
          米国：OSM（Geofabrik の州別 PBF）＋ USGS 3DEP 標高（The National Map、登録不要、NAVD88）。方向は北行き・南行き・東行き・西行き、
          出口番号のみの出口は「路線＋出口番号」で施設名を付けます。カナダは地域設定と DEM（NRCan）アダプタが未整備です。
        </div>
      )}
      <div className="grid2">
        <div className="stack">
          <div className="card"><h3>地域プリセット</h3>
            <table className="dt" data-testid="region-table"><thead><tr><th>地域</th><th>ID</th><th>状態</th></tr></thead><tbody>
              {(regions.data ?? []).filter((r) => (country === "JP" ? r.country === "JP" : r.country !== "JP")).map((r) => (
                <tr key={r.id} className={`click ${region?.id === r.id ? "hl" : ""}`} onClick={() => setSel(r.id)}>
                  <td>{r.label}</td><td className="mono">{r.id}</td><td><span className={`badge b-${STATUS[r.status]?.[1]}`}>{STATUS[r.status]?.[0] ?? r.status}</span></td></tr>))}
            </tbody></table></div>
          <div className="card"><h3>範囲指定</h3>
            <div className="tabs">{TABS.map((tb, i) => <button key={tb} className={tab === i ? "on" : ""} onClick={() => setTab(i)}>{tb}</button>)}</div>
            {tab === 0 && <div className="note">都道府県は地域プリセットから選択します（JIS コード、例：栃木 09）。境界ポリゴンは data/raw/boundaries/ に保存したものを使います。</div>}
            {tab === 1 && <div className="note">市区町村コードによる指定：境界ポリゴン（admin_level 7）を取得後に利用できます。</div>}
            {tab === 2 && <label className="form">州 FIPS コード（例：ミシガン 26、コロラド 08。境界ポリゴンがない場合は地域プリセットの矩形で代替）<input value={text} onChange={(e) => setText(e.target.value)} data-testid="fips-input" /></label>}
            {tab === 3 && <label className="form">北西・南東（西,南,東,北）<input value={bbox} onChange={(e) => setBbox(e.target.value)} data-testid="bbox-input" /></label>}
            {tab === 4 && <div className="form"><label>経度<input value={circle.lon} onChange={(e) => setCircle({ ...circle, lon: e.target.value })} /></label>
              <label>緯度<input value={circle.lat} onChange={(e) => setCircle({ ...circle, lat: e.target.value })} /></label>
              <label>半径 [km]<input value={circle.r} onChange={(e) => setCircle({ ...circle, r: e.target.value })} /></label></div>}
            {(tab === 5 || tab === 6) && <label className="form">{tab === 5 ? "路線名または ref（カンマ区切り）" : "施設名"}<input value={text} onChange={(e) => setText(e.target.value)} /></label>}
            {(tab === 7 || tab === 8) && <label className="form">GeoJSON（Polygon / MultiPolygon、WGS84）<textarea rows={4} value={geo} onChange={(e) => setGeo(e.target.value)} /></label>}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" disabled={apply.isPending} onClick={() => apply.mutate()} data-testid="apply-range">範囲を適用</button>
              {progress && <span className="num">{Math.round(progress.p * 100)}% {progress.m}</span>}
            </div>
          </div>
        </div>
        <div className="stack">
          <RoadMriMap height={300} overlays={preview} showLabels={false} testId="region-map" />
          <div className="card"><h3>データ取得状態</h3>
            <table className="dt"><thead><tr><th>データ種別</th><th>出典・版</th><th>取得日</th><th>規模</th><th>状態</th></tr></thead><tbody>
              {(m?.sources ?? []).map((s: any) => <tr key={s.source_id}><td>OSM</td><td>{s.name}</td><td>{String(s.retrieved_at).slice(0, 10)}</td><td>{m.counts.facility} 施設</td><td><span className="badge b-high">処理済み</span></td></tr>)}
              {(m?.dem_datasets ?? []).map((d: any) => <tr key={d.dem_id}><td>DEM</td><td>{d.name}</td><td>{String(d.retrieved_at).slice(0, 10)}</td><td>{d.resolution_m} m</td><td><span className="badge b-high">キャッシュあり</span></td></tr>)}
            </tbody></table>
            <h3 style={{ marginTop: 10 }}>DEM メタデータ</h3>
            <table className="dt"><thead><tr><th>DEM</th><th>座標系</th><th>標高基準</th><th>推定誤差</th><th>欠損率</th></tr></thead><tbody>
              {(m?.dem_datasets ?? []).map((d: any) => <tr key={d.dem_id}><td>{d.name}</td><td>{d.crs}</td><td>{d.vertical_datum}</td><td>{d.nominal_error_m} m</td><td>{(d.missing_ratio * 100).toFixed(1)}%</td></tr>)}
            </tbody></table></div>
          <div className="card"><h3>処理パイプライン</h3>
            <div className="row">{STEPS.map((s) => <span key={s} className={`badge ${m?.has_data ? "b-high" : "b-medium"}`}>{s}</span>)}</div>
            <div className="note" style={{ marginTop: 6 }}>施設 {m?.counts?.facility ?? 0}・ランプ {m?.counts?.ramp ?? 0}・区間 {m?.counts?.section ?? 0}（run {m?.run?.run_id ?? "—"}、{m?.run?.duration_s?.toFixed?.(1) ?? "—"} 秒）</div>
            <h3 style={{ marginTop: 10 }}>開発フェーズ</h3>
            <div className="row">{PHASES.map(([p, s]) => <span key={p} className={`badge ${s === "MVP" ? "b-ok" : "b-medium"}`}>{p}（{s}）</span>)}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
