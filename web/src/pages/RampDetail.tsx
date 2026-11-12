import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import CurvatureChart from "../components/charts/CurvatureChart";
import ProfileChart, { type Marker } from "../components/charts/ProfileChart";
import { DIFF_COLORS, DIFF_LABELS, ErrorBox, Kpi, QualityBadge, Skeleton, fmt } from "../components/common/ui";
import RoadMriMap from "../components/map/RoadMriMap";
import SimilarRamps from "../components/similar/SimilarRamps";
import { t } from "../i18n";
import { useCompareTray, useSimilar } from "../state/stores";

export default function RampDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [win, setWin] = useState<number | null>(null);
  const [showSim, setShowSim] = useState(false);
  const setSim = useSimilar((s) => s.setBase);
  const add = useCompareTray((s) => s.add);
  const q = useQuery({ queryKey: ["ramp", id], queryFn: () => api.get<any>(`/ramps/${id}`) });
  const prof = useQuery({ queryKey: ["rampprof", id], queryFn: () => api.get<any>(`/ramps/${id}/profile`) });
  const focus = useMemo<[number, number, number, number] | null>(() => {
    const c = q.data?.geometry?.coordinates as number[][] | undefined;
    if (!c?.length) return null;
    const xs = c.map((p) => p[0]), ys = c.map((p) => p[1]);
    const pad = 0.004;
    return [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad];
  }, [q.data]);
  if (q.isLoading) return <div className="page"><Skeleton h={400} /></div>;
  if (q.error) return <div className="page"><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  const r = q.data;
  const pts = prof.data?.points ?? [];
  const L = r.length_m ?? 0;
  const markers: Marker[] = [
    { s: 0, label: `${r.from_ref?.kind === "mainline" ? "分流" : "接続元"}（${r.from_ref?.kind ?? ""}）`, color: "#38e1ff" },
    { s: L, label: `${r.to_ref?.kind === "mainline" ? "合流" : "接続先"}（${r.to_ref?.kind ?? ""}）`, color: "#ff7a2e" },
  ];
  const sib: string[] = r.siblings ?? [];
  const i = sib.indexOf(id);
  const dc = r.difficulty_components ?? {};
  const params = r.params ?? {};
  return (
    <div className="page" data-testid="ramp-detail">
      <h1>
        <span className="mono">{r.ramp_id}</span> <span className="badge b-ok">{t(`flow.${r.flow}`, r.flow)}</span>
        <span className="badge b-medium">{t(`dir.${r.carriageway}`, r.carriageway)}</span>
        <QualityBadge q={r.osm_quality} prefix="OSM " /> <QualityBadge q={r.dem_quality} prefix="DEM " />
        <span style={{ marginLeft: "auto" }} className="row">
          <button disabled={i <= 0} onClick={() => nav(`/ramp/${sib[i - 1]}`)}>← 前</button>
          <select value={id} onChange={(e) => nav(`/ramp/${e.target.value}`)} aria-label="同じ施設のランプ">
            {sib.map((s) => <option key={s} value={s}>{s.slice(-8)}</option>)}
          </select>
          <button disabled={i < 0 || i >= sib.length - 1} onClick={() => nav(`/ramp/${sib[i + 1]}`)}>次 →</button>
          <button onClick={() => nav(`/facility/${r.facility_id}`)}>施設へ（{r.facility?.name}）</button>
          <button onClick={() => setShowSim(!showSim)} data-testid="btn-similar-ramps">類似ランプ</button>
          <button onClick={() => { setSim({ kind: "facility", id: r.facility_id }); nav("/search?mode=facility&similar=1"); }} data-testid="btn-similar-facility">類似IC/JCTを検索</button>
          <button onClick={() => add({ facility_id: r.facility_id, name: r.facility?.name })}>＋比較</button>
        </span>
      </h1>
      <div className="kpis">
        <Kpi label="ランプ長" value={r.length_m} unit="m" />
        <Kpi label="最小曲率半径" value={r.min_radius_m} unit="m" />
        <Kpi label="最大上り（瞬間）" value={r.grade_inst_max} unit="%" d={1} />
        <Kpi label="100m 平均上り" value={r.grade_up_max_w100} unit="%" d={2} />
        <Kpi label="最大下り（100m）" value={r.grade_down_max_w100} unit="%" d={2} />
        <Kpi label="標高差" value={r.elev_diff_m} unit="m" d={1} />
        <Kpi label="累積上昇／下降" value={r.elev_gain_m != null ? `${fmt(r.elev_gain_m, 1)} / ${fmt(r.elev_loss_m, 1)}` : null} unit="m" />
        <Kpi label="難易度候補値" value={r.difficulty} d={1} />
      </div>
      {showSim && <div className="card" style={{ marginTop: 12 }}><h3>類似ランプ</h3><SimilarRamps rampId={id} /></div>}
      <div className="grid2" style={{ marginTop: 12 }}>
        <RoadMriMap height={460} focus={focus} selectedRampId={id} selectedFacilityId={r.facility_id} testId="ramp-map" />
        <div className="card">
          <div className="row"><h3 style={{ margin: 0 }}>標高・勾配プロファイル</h3>
            <span className="row" style={{ marginLeft: "auto" }}>窓幅
              {[null, 10, 50, 100, 500].map((w) => <button key={String(w)} className={win === w ? "primary" : ""} onClick={() => setWin(w)}>{w ? `${w}m` : "なし"}</button>)}</span></div>
          {prof.isLoading ? <Skeleton h={240} /> : <ProfileChart points={pts} markers={markers} window={win} />}
          <h3>曲率プロファイル</h3>
          {prof.isLoading ? <Skeleton h={180} /> : <CurvatureChart points={pts} />}
          <div className="note">グラフにカーソルを置くと地図上に位置を表示します。灰色＝DEM 生値、色＝補正後（勾配で色分け）、帯＝橋梁・トンネル・DEM 低信頼区間。</div>
        </div>
      </div>
      <div className="grid3" style={{ marginTop: 12 }}>
        <div className="card"><h3>属性</h3><table className="dt"><tbody>
          <tr><td>車線数</td><td className="num">{fmt(r.lanes)}</td></tr>
          <tr><td>制限速度</td><td className="num">{fmt(r.maxspeed_kmh, 0, " km/h")}</td></tr>
          <tr><td>加速車線長</td><td className="num">{fmt(r.accel_lane_len_m, 0, " m")}</td></tr>
          <tr><td>平均曲率半径</td><td className="num">{fmt(r.mean_radius_m, 0, " m")}</td></tr>
          <tr><td>標高 最高／最低</td><td className="num">{fmt(r.z_max, 1)} / {fmt(r.z_min, 1)} m</td></tr>
          <tr><td>構造物</td><td>橋梁 {fmt(r.bridge_len_m, 0)} m・高架 {fmt(r.viaduct_len_m, 0)} m・トンネル {fmt(r.tunnel_len_m, 0)} m</td></tr>
          <tr><td>補間間隔</td><td>{params.resample?.ramp_step_m ?? "—"} m（内部 {params.resample?.internal_ramp_step_m} m）</td></tr>
          <tr><td>平滑化</td><td>{params.elevation_smoothing?.method}（窓 {params.elevation_smoothing?.ramp_window_m} m）</td></tr>
          <tr><td>勾配計算方式</td><td>窓平均（既定 {params.grade?.default_window_m} m）</td></tr>
        </tbody></table></div>
        <div className="card" data-testid="difficulty-card"><h3>難易度候補値の内訳</h3>
          {Object.keys(DIFF_COLORS).map((k) => (
            <div key={k} className="row" style={{ marginBottom: 4 }}>
              <span style={{ width: 70 }}>{DIFF_LABELS[k]}</span>
              <span className="dbar" style={{ width: 160 }}><i style={{ width: `${Math.min(100, ((dc.contributions?.[k] ?? 0) / Math.max(1e-9, (dc.weights?.[k] ?? 1) * 100)) * 100)}%`, background: DIFF_COLORS[k] }} /></span>
              <span className="num">{fmt(dc.contributions?.[k], 1)}</span><span className="note">（重み {dc.weights?.[k]}）</span>
            </div>
          ))}
          {dc.accel_missing && <div className="warn">加速車線長が取得できないため、合流条件の寄与は 0 としています。</div>}
          <div className="note">{t("common.difficultyNote")}</div>
        </div>
        <div className="card"><h3>品質・注意事項</h3>
          {(r.flags ?? []).length ? (r.flags ?? []).map((x: any) => <div key={x.flag_id} className={x.severity === "error" ? "error" : "warn"} style={{ textAlign: "left", padding: 2, border: "none" }}>⚠ {x.message_ja}</div>) : <div className="note">品質フラグはありません。</div>}
          {r.window_truncated && <div className="warn">ランプ長が 100 m 未満のため、窓平均勾配は端点間平均です。</div>}
        </div>
      </div>
    </div>
  );
}
