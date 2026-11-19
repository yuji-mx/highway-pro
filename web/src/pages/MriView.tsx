import { useQuery } from "@tanstack/react-query";
import type { Map as MlMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import ProfileChart from "../components/charts/ProfileChart";
import { ConfBar, ErrorBox, QualityBadge, ReviewBadge, TypeBadge, fmt } from "../components/common/ui";
import { legendFor } from "../components/map/colorScales";
import RoadMriMap, { type MapClick } from "../components/map/RoadMriMap";
import { accelShort, accelStateClass } from "../components/accel/accelText";
import { t } from "../i18n";
import { LAYER_KEYS, useLayers, useTargets } from "../state/stores";
import { displayName } from "../components/common/names";

const METRICS = ["road_type", "grade", "elevation", "radius", "maxspeed", "lanes", "flow", "shape", "quality", "drive"];

function FacilityInfo({ id, onRamp }: { id: string; onRamp: (rid: string) => void }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["facility", id], queryFn: () => api.get<any>(`/facilities/${id}`) });
  const [hoverSec, setHoverSec] = useState<string | null>(null);
  const sec = hoverSec ?? q.data?.sections?.[0]?.section_id;
  const prof = useQuery({ queryKey: ["secprof", sec], queryFn: () => api.get<any>(`/sections/${sec}/profile`), enabled: !!sec });
  if (q.isLoading) return <div className="skel" style={{ height: 200 }} />;
  if (q.error) return <ErrorBox error={q.error} />;
  const f = q.data;
  return (
    <div data-testid="info-panel">
      <h3 style={{ margin: 0 }}>{displayName(f.name)} <TypeBadge type={f.facility_type} /></h3>
      <div className="note">{(f.route_refs ?? []).join(" / ")} ・ 出口番号 {f.exit_number ?? "—"}</div>
      <table className="dt" style={{ marginTop: 6 }}>
        <tbody>
          <tr><td>形状分類</td><td>{f.shape_class} <ConfBar v={f.shape_confidence} /></td></tr>
          <tr><td>第2候補</td><td>{f.shape_class_2 ?? "—"} {f.shape_confidence_2 != null && `(${fmt(f.shape_confidence_2, 2)})`}</td></tr>
          <tr><td>確認状態・品質</td><td><ReviewBadge s={f.review_status} /> <QualityBadge q={f.quality_level} /></td></tr>
          <tr><td>OSM 更新日</td><td>{f.osm_timestamp?.slice(0, 10) ?? "—"}</td></tr>
        </tbody>
      </table>
      <div className="note" style={{ marginTop: 6 }}>隣接 IC/JCT（ホバーで標高プロファイル）</div>
      <table className="dt">
        <tbody>
          {(f.sections ?? []).map((s: any) => (
            <tr key={s.section_id} className="click" onMouseEnter={() => setHoverSec(s.section_id)}
              onClick={() => nav(`/sections?id=${encodeURIComponent(s.section_id)}`)}>
              <td>{t(`dir.${s.direction}`, s.direction)}</td>
              <td>{s.from_facility_id === id ? `→ ${s.to_name ? displayName(s.to_name) : "—"}` : `← ${s.from_name ? displayName(s.from_name) : "—"}`}</td>
              <td className="n">{fmt((s.dist_standard_m ?? 0) / 1000, 1, " km")}</td>
              <td className="n">{fmt(s.grade_up_max_w100, 1, "%")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {prof.data && <ProfileChart points={prof.data.points} height={130} testId="mini-profile" />}
      <div className="note">ランプ（クリックで情報）</div>
      <div style={{ maxHeight: 140, overflow: "auto" }}>
        {(f.ramps ?? []).map((r: any) => (
          <div key={r.ramp_id} className="row" style={{ cursor: "pointer" }} onClick={() => onRamp(r.ramp_id)}>
            <span className="mono">{r.ramp_id.slice(-8)}</span><span>{t(`flow.${r.flow}`, r.flow)}</span><span className="num">{fmt(r.length_m, 0, " m")}</span>
            {r.accel_lane_status && <span className="note" title="加速車線長">加速 {accelShort(r)}</span>}
          </div>
        ))}
      </div>
      {(f.flags ?? []).filter((x: any) => x.target_id === id).map((x: any) => <div key={x.flag_id} className="warn">⚠ {x.message_ja}</div>)}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" onClick={() => nav(`/facility/${id}`)} data-testid="open-detail">詳細を開く</button>
        <button onClick={() => nav(`/sections?facility=${id}`)}>区間解析</button>
      </div>
    </div>
  );
}

function RampInfo({ id, onBack }: { id: string; onBack: (fid: string) => void }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["ramp", id], queryFn: () => api.get<any>(`/ramps/${id}`) });
  const prof = useQuery({ queryKey: ["rampprof", id], queryFn: () => api.get<any>(`/ramps/${id}/profile`) });
  if (q.isLoading) return <div className="skel" style={{ height: 200 }} />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data;
  return (
    <div data-testid="info-panel">
      <h3 style={{ margin: 0 }} className="mono">{r.ramp_id}</h3>
      <div>{t(`flow.${r.flow}`, r.flow)} ・ {r.from_ref?.kind} → {r.to_ref?.kind}</div>
      <div className="kpis" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 6 }}>
        <div className="kpi"><div className="l">長さ</div><div className="v">{fmt(r.length_m, 0)}<small>m</small></div></div>
        <div className="kpi"><div className="l">最小R</div><div className="v">{fmt(r.min_radius_m, 0)}<small>m</small></div></div>
        <div className="kpi"><div className="l">100m 平均上り</div><div className="v">{fmt(r.grade_up_max_w100, 1)}<small>%</small></div></div>
        <div className="kpi"><div className="l">標高差</div><div className="v">{fmt(r.elev_diff_m, 1)}<small>m</small></div></div>
      </div>
      {r.accel_lane_status && (
        <div style={{ marginTop: 4 }} data-testid="mri-accel">加速車線長：<span className={accelStateClass(r)}>{r.accel_display?.label ?? "—"}</span>{" "}
          <b className="num">{["provisional", "confirmed"].includes(r.accel_display?.state) ? fmt(r.accel_display.length_m, 0, " m") : ""}</b>
          {r.accel_display?.state === "pending" && r.accel_display.provisional_length_m != null && (
            <span className="note">地図データによる暫定値 {fmt(r.accel_display.provisional_length_m, 0, " m")}</span>)}
          {r.accel_lane_status !== "measured" && r.accel_lane_reason_text && <div className="note">{r.accel_lane_reason_text}</div>}</div>
      )}
      {prof.data && <ProfileChart points={prof.data.points} height={130} testId="mini-profile" />}
      {(r.flags ?? []).map((x: any) => <div key={x.flag_id} className="warn">⚠ {x.message_ja}</div>)}
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" onClick={() => nav(`/ramp/${id}`)}>ランプ詳細</button>
        <button onClick={() => onBack(r.facility_id)}>施設へ戻る</button>
      </div>
    </div>
  );
}

/** 名称の表示条件：IC・SA/PA 名と JCT 名で表示を始めるズームが違うので、分けて示す。 */
function labelZoomHint(zoom: number | null, mc: any): { short: string; long: string } | null {
  if (zoom == null || !mc) return null;
  const ic = mc.label_min_zoom, jct = mc.jct_label_min_zoom;
  if (zoom >= Math.max(ic, jct)) return null;
  const long = `JCT 名はズーム ${jct} 以上、IC・SA/PA 名はズーム ${ic} 以上で表示します（いまは ${zoom}）。重なる名称は省きます`;
  if (zoom < Math.min(ic, jct)) return { short: `ズーム${Math.min(ic, jct)}以上`, long };
  return { short: ic > jct ? `IC・SA/PA名はズーム${ic}以上` : `JCT名はズーム${jct}以上`, long };
}

export default function MriView() {
  const L = useLayers();
  const targets = useTargets();
  const [sp, setSp] = useSearchParams();
  const sel = sp.get("sel");
  const country = sp.get("country");
  const kind = sp.get("kind") as "facility" | "ramp" | null;
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const shapes = useMemo(() => [], []);
  const select = (k: "facility" | "ramp", id: string) => setSp({ ...(country ? { country } : {}), kind: k, sel: id });
  const onClick = (c: MapClick) => select(c.kind, c.id);
  const legend = legendFor(L.metric, undefined, shapes);
  // 現在のズーム（最小ズームのあるレイヤーの表示条件を示すため）
  const [zoom, setZoom] = useState<number | null>(null);
  const [layersOpen, setLayersOpen] = useState(() => window.innerWidth > 640);
  const onMap = useCallback((m: MlMap) => {
    mapRef.current = m;
    const upd = () => setZoom(Math.round(m.getZoom() * 10) / 10);
    m.on("zoomend", upd);
    m.on("load", upd);
  }, []);
  const tl = meta.data?.map?.tiles?.layers ?? {};
  const minZoom: Partial<Record<string, number>> = {
    ramps: tl.ramps?.min_zoom,
    target: tl.ramps?.min_zoom,
    general: tl.general?.min_zoom,
    bridges: tl.structures?.min_zoom,
    tunnels: tl.structures?.min_zoom,
  };
  // 表示範囲にデータがあるか（地図の描画が終わるたびに、チェックの入ったレイヤーの描画済みの要素を数える）
  const [empty, setEmpty] = useState<Record<string, boolean>>({});
  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    const count = () => {
      const has = (layers: string[], pred?: (p: any) => boolean) => {
        const ls = layers.filter((id) => m.getLayer(id));
        if (!ls.length) return true;
        const fs = m.queryRenderedFeatures({ layers: ls });
        return pred ? fs.some((f) => pred(f.properties)) : fs.length > 0;
      };
      const ft = (types: string[]) => (p: any) => types.includes(p?.facility_type);
      setEmpty({
        mainline: !has(["mainline-lo-2", "mainline-hi-2"]),
        ramps: !has(["ramps-all-2"]),
        general: !has(["general"]),
        bridges: !has(["bridges"]),
        tunnels: !has(["tunnels"]),
        tolls: !has(["tolls"]),
        ic: !has(["facilities-2"], ft(["IC", "SIC"])),
        jct: !has(["facilities-2"], ft(["JCT"])),
        sapa: !has(["facilities-2"], ft(["SA", "PA"])),
        warnings: !has(["fac-warn"]),
      });
    };
    m.on("idle", count);
    return () => {
      m.off("idle", count);
    };
  }, [zoom]);
  const exportPng = () => {
    const c = mapRef.current?.getCanvas();
    if (!c) return;
    const a = document.createElement("a");
    a.href = c.toDataURL("image/png");
    a.download = "road_mri_view.png";
    a.click();
  };
  return (
    <div style={{ position: "relative", height: "100%" }}>
      <RoadMriMap height="100%" onClick={onClick} onCursor={setCursor} targetRampIds={targets.rampIds}
        selectedFacilityId={kind === "facility" ? sel : null} selectedRampId={kind === "ramp" ? sel : null}
        onMap={onMap} testId="mri-map" country={country} />
      <div className="float mri-layers" style={{ left: 10, top: 10, width: 190 }} data-testid="layer-panel">
        {/* 狭い画面では地図を広く使えるよう、最初は畳んでおく */}
        <button className="ghost note" style={{ padding: 0, border: "none", width: "100%", textAlign: "left" }} aria-expanded={layersOpen}
          onClick={() => setLayersOpen(!layersOpen)} data-testid="layers-toggle">レイヤー {layersOpen ? "▾" : "▸"}</button>
        {layersOpen && LAYER_KEYS.map((k) => {
          // 表示されない理由を示す：縮尺の条件（ズーム N 以上）か、表示範囲にデータがないか
          const minZ = minZoom[k];
          const hidden = minZ != null && zoom != null && zoom < minZ;
          const noTarget = k === "target" && targets.rampIds.length === 0;
          const labelHint = k === "labels" ? labelZoomHint(zoom, meta.data?.map) : null;
          const none = !hidden && !noTarget && L.visible[k] && empty[k] === true;
          const hint = hidden ? `ズーム${minZ}以上` : labelHint ? labelHint.short : none ? "この範囲になし" : null;
          return (
            <label key={k} className="row" style={{ gap: 4, fontSize: 12, opacity: hidden || noTarget || none ? 0.55 : 1, flexWrap: "wrap" }}
              title={noTarget ? "条件検索の結果で「地図で強調」したランプを表示します"
                : hidden ? `ズーム ${minZ} 以上で表示します（いまは ${zoom}）` : labelHint ? labelHint.long
                : none ? "表示範囲にこの種類のデータがありません（縮尺の条件は満たしています）" : undefined}
              data-testid={`layer-${k}`}>
              <input type="checkbox" checked={L.visible[k]} onChange={() => L.toggle(k)} />{t(`layer.${k}`)}
              {k === "target" && <span className="mut">（{targets.rampIds.length}）</span>}
              {hint && <span className="mut" style={{ fontSize: 10 }} data-testid={`layer-hint-${k}`}>{hint}</span>}
            </label>
          );
        })}
        {layersOpen && <>
        <div className="note" style={{ marginTop: 6 }}>色分け指標</div>
        <select value={L.metric} onChange={(e) => L.setMetric(e.target.value)} data-testid="metric-select" style={{ width: "100%" }}>
          {METRICS.map((m) => <option key={m} value={m} disabled={m === "drive"}>{t(`metric.${m}`)}</option>)}
        </select>
        <label className="row" style={{ gap: 4, fontSize: 12, marginTop: 4 }}>
          <input type="checkbox" checked={L.widthByCurvature} onChange={() => L.set({ widthByCurvature: !L.widthByCurvature })} />曲率で線幅変更
        </label>
        </>}
      </div>
      <div className="float row mri-tools" style={{ left: "50%", top: 10, transform: "translateX(-50%)" }}>
        <button onClick={() => mapRef.current?.zoomIn()} aria-label="拡大">＋</button>
        <button onClick={() => mapRef.current?.zoomOut()} aria-label="縮小">－</button>
        <button onClick={() => setSp(country ? { country } : {})}>全体</button>
        {(meta.data?.countries ?? []).length > 1 && (meta.data.countries as string[]).map((c) => (
          <button key={c} className={(country ?? [...meta.data.countries].sort()[0]) === c ? "primary" : ""} data-testid={`country-${c}`}
            onClick={() => setSp({ country: c })}>{t(`country.${c}`, c)}</button>
        ))}
        <button className={L.glow ? "primary" : ""} onClick={() => L.set({ glow: !L.glow })}>ランプ発光</button>
        <button className={L.highlightTargets ? "primary" : ""} onClick={() => L.set({ highlightTargets: !L.highlightTargets })}>
          検索結果を強調（{targets.rampIds.length}）
        </button>
        <button onClick={exportPng} data-testid="export-view">画像出力</button>
      </div>
      <div className="mri-right">
        {sel && kind && (
          <div className="float info" data-testid="info-float">
            <button className="ghost" style={{ float: "right" }} onClick={() => setSp({})} aria-label="閉じる">×</button>
            {kind === "facility" ? <FacilityInfo id={sel} onRamp={(rid) => select("ramp", rid)} /> : <RampInfo id={sel} onBack={(fid) => select("facility", fid)} />}
          </div>
        )}
        <div className="float legend" data-testid="legend">
          <div className="note">{t(`metric.${L.metric}`)}</div>
          {legend.map((x) => <div key={x.label} style={{ fontSize: 11 }}><i style={{ background: x.color }} />{x.label}</div>)}
          <div className="note" style={{ marginTop: 4 }}>{meta.data?.attribution}</div>
        </div>
      </div>
      <div className="float mono" style={{ left: 120, bottom: 8, fontSize: 11, padding: "2px 8px" }} data-testid="cursor-coords">
        {cursor ? `${cursor[1].toFixed(5)}, ${cursor[0].toFixed(5)}` : "—"}
      </div>
    </div>
  );
}
