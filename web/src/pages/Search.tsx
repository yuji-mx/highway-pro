import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, downloadBlob, errMsg, waitJob } from "../api/client";
import type { QueryDSL } from "../api/types";
import { ComparePanel, CompareTray } from "../components/compare/ComparePanel";
import { b64, toText, usesInstantGrade } from "../components/common/queryText";
import { ConfBar, DifficultyBar, Empty, ErrorBox, QualityBadge, QueryText, Skeleton, TypeBadge } from "../components/common/ui";
import SimilarFacilities from "../components/similar/SimilarFacilities";
import SimilarRamps from "../components/similar/SimilarRamps";
import DataTable, { downloadCsv, type Col } from "../components/tables/DataTable";
import { t } from "../i18n";
import { useCompareTray, useRanges, useSimilar, useTargets, useToast } from "../state/stores";

type Mode = "ramp" | "facility" | "section";
interface Form {
  route: string; flows: string[]; lenMin: string; lenMax: string; rMax: string; upMin: string; downMin: string; window: string;
  zMin: string; dem: string; osm: string; shapes: string[]; type: string; confMin: string; rampsMin: string; loop: string;
  facUpMin: string; facRMax: string; distMin: string; gradeMeanMin: string; extra: { field: string; op: string; value: string }[]; extraMode: "all" | "any";
}
const EMPTY_FORM: Form = { route: "", flows: [], lenMin: "", lenMax: "", rMax: "", upMin: "", downMin: "", window: "100", zMin: "", dem: "",
  osm: "", shapes: [], type: "", confMin: "", rampsMin: "", loop: "", facUpMin: "", facRMax: "", distMin: "", gradeMeanMin: "", extra: [], extraMode: "all" };
const FLOWS = ["entry", "exit", "jct_link", "collector_distributor", "facility_link", "toll_link"];
const SHAPES = ["トランペット型", "ダイヤモンド型", "変形ダイヤモンド型", "クローバーリーフ型", "部分クローバー型", "ハーフIC", "スマートIC", "複合型", "Y型", "T型", "タービン型", "スタック型", "SA/PA", "分類不能"];
const EXTRA_FIELDS: Record<Mode, string[]> = {
  ramp: ["small_r_long", "short_accel", "end_uphill", "high_alt_steep", "small_r_uphill", "pre_merge_grade", "end_grade", "accel_lane_len_m", "curvature_rate_max", "difficulty", "grade_inst_max", "cont_up_len_m", "lanes", "maxspeed_kmh", "is_loop"],
  facility: ["mean_ramp_length_m", "max_elev_diff_m", "connection_directions", "is_half_ic", "review_status", "quality_level"],
  section: ["grade_up_max_w1000", "cont_up_len_m", "gain_per_km", "tunnel_len_m", "bridge_len_m", "z_max", "quality_level"],
};

function num(v: string): number | null {
  const n = Number(v);
  return v.trim() === "" || !Number.isFinite(n) ? null : n;
}
function coerce(v: string): any {
  if (v === "true") return true;
  if (v === "false") return false;
  return num(v) ?? v;
}

export function buildQuery(mode: Mode, f: Form): QueryDSL {
  const all: any[] = [];
  const add = (field: string, op: string, value: any) => value != null && value !== "" && all.push({ field, op, value });
  add("route_ref", "eq", f.route || null);
  if (mode === "ramp") {
    if (f.flows.length === 1) add("flow", "eq", f.flows[0]);
    else if (f.flows.length > 1) add("flow", "in", f.flows);
    add("length_m", "gte", num(f.lenMin)); add("length_m", "lte", num(f.lenMax)); add("min_radius_m", "lte", num(f.rMax));
    add(`grade_up_max_w${f.window}`, "gte", num(f.upMin)); add(`grade_down_max_w${f.window}`, "gte", num(f.downMin));
    add("z_max", "gte", num(f.zMin)); add("dem_quality", "gte", f.dem || null); add("osm_quality", "gte", f.osm || null);
    if (f.shapes.length) add("shape_class", "in", f.shapes);
  } else if (mode === "facility") {
    add("facility_type", "eq", f.type || null);
    if (f.shapes.length) add("shape_class", "in", f.shapes);
    add("shape_confidence", "gte", num(f.confMin)); add("ramp_count", "gte", num(f.rampsMin));
    if (f.loop) add("has_loop", "eq", f.loop === "yes");
    add("min_radius_m", "lte", num(f.facRMax)); add("grade_up_max_w100", "gte", num(f.facUpMin));
  } else {
    add("dist_standard_m", "gte", num(f.distMin)); add("grade_mean", "gte", num(f.gradeMeanMin));
    add("grade_up_max_w100", "gte", num(f.upMin)); add("min_radius_m", "lte", num(f.rMax));
  }
  const extra = f.extra.filter((e) => e.field && e.value !== "").map((e) => ({ field: e.field, op: e.op, value: coerce(e.value) }));
  const where: any = { all };
  if (extra.length) {
    if (f.extraMode === "any") where.any = extra;
    else where.all = [...all, ...extra];
  }
  const sort = mode === "ramp" ? [{ field: "difficulty", dir: "desc" as const }] : mode === "facility" ? [{ field: "shape_confidence", dir: "asc" as const }] : [{ field: "grade_up_max_w100", dir: "desc" as const }];
  return { target: mode, where, sort, limit: 500 };
}

function SimilarPanel() {
  const base = useSimilar((s) => s.base);
  const setBase = useSimilar((s) => s.setBase);
  const name = useQuery({ queryKey: ["facility", base?.id], queryFn: () => api.get<any>(`/facilities/${base!.id}`), enabled: base?.kind === "facility" });
  if (!base) return null;
  return (
    <div className="card" data-testid="similar-panel">
      <h3 className="row">類似地点：基準 {base.kind === "facility" ? `IC/JCT「${name.data?.name ?? base.id}」` : `ランプ ${base.id}`}
        <button className="ghost" style={{ marginLeft: "auto" }} onClick={() => setBase(null)}>×</button></h3>
      <div data-testid="similar-base" className="note">{base.id}</div>
      {base.kind === "facility" ? <SimilarFacilities facilityId={base.id} baseName={name.data?.name} /> : <SimilarRamps rampId={base.id} />}
    </div>
  );
}

export default function Search() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast((s) => s.push);
  const mode = (sp.get("mode") as Mode) || "ramp";
  const urlQ = sp.get("q");
  const initial = useMemo(() => (urlQ ? b64.dec<QueryDSL>(urlQ) : null), [urlQ]);
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [presetQ, setPresetQ] = useState<QueryDSL | null>(null);
  const tray = useCompareTray();
  const setRange = useRanges((s) => s.setRange);
  const setTargets = useTargets((s) => s.setTargets);
  const setSim = useSimilar((s) => s.setBase);
  useEffect(() => {
    if (sp.get("compare") === "1" && tray.a && tray.b) tray.setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sp]);
  useEffect(() => {
    try {
      if (urlQ) sessionStorage.setItem("roadmri.lastSearch", urlQ);
    } catch {
      /* 保存できない環境では無視 */
    }
  }, [urlQ]);
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => api.get<any[]>("/presets") });
  const routes = useQuery({ queryKey: ["sections-routes"], queryFn: () => api.get<any>("/sections") });
  const draft = presetQ ?? buildQuery(mode, form);
  const active = initial && initial.target === mode ? initial : null;
  const res = useQuery({
    queryKey: ["search", mode, urlQ],
    queryFn: () => api.post<any>(`/search/${mode}`, active),
    enabled: !!active,
  });
  const run = (q: QueryDSL) => setSp({ mode: q.target, q: b64.enc(q) });
  const setF = (p: Partial<Form>) => { setPresetQ(null); setForm((f) => ({ ...f, ...p })); };
  const items: any[] = res.data?.items ?? [];
  const saveQ = useMutation({
    mutationFn: () => api.post("/saved-queries", { name: `条件 ${new Date().toLocaleString("ja-JP")}`, target: mode, query: active }),
    onSuccess: () => { toast("条件を保存しました"); qc.invalidateQueries({ queryKey: ["saved-queries"] }); },
  });
  const toRange = (slot: "a" | "b") => {
    const ids = [...new Set(items.map((r) => r.facility_id ?? r.from_facility_id).filter(Boolean))];
    setRange(slot, { type: "facilities", facility_ids: ids, label: `検索結果（${ids.length} 施設）` });
    toast(`検索結果の ${ids.length} 施設を範囲${slot.toUpperCase()}に設定しました`);
  };
  const highlight = () => {
    setTargets(items.map((r) => r.ramp_id).filter(Boolean), [...new Set(items.map((r) => r.facility_id).filter(Boolean))]);
    nav("/mri");
  };
  const exportGeo = async () => {
    try {
      const j: any = await api.post("/export", { dataset: `${mode === "ramp" ? "ramps" : mode === "facility" ? "facilities" : "sections"}`, format: "geojson", query: active });
      const done = await waitJob(j.job_id);
      const r = await fetch(done.download_url);
      downloadBlob(await r.blob(), "search_geojson.zip");
    } catch (e) {
      toast(errMsg(e), true);
    }
  };
  const addCmp = (fid: string, name: string | null) => tray.add({ facility_id: fid, name });
  const rampCols: Col<any>[] = [
    { key: "ramp_id", label: "ID", render: (r) => <a onClick={(e) => { e.stopPropagation(); nav(`/ramp/${r.ramp_id}`); }} className="mono" style={{ cursor: "pointer" }}>{r.ramp_id.slice(-12)}</a> },
    { key: "facility_name", label: "施設" }, { key: "flow", label: "区分", get: (r) => t(`flow.${r.flow}`, r.flow) },
    { key: "shape_class", label: "形状" }, { key: "length_m", label: "長さ", num: true }, { key: "min_radius_m", label: "最小R", num: true },
    { key: "grade_up_max_w100", label: "100m上り", num: true }, { key: "grade_down_max_w100", label: "100m下り", num: true },
    { key: "elev_diff_m", label: "標高差", num: true }, { key: "z_max", label: "最高標高", num: true },
    { key: "dem_quality", label: "DEM品質", render: (r) => <QualityBadge q={r.dem_quality} /> },
    { key: "difficulty", label: "難易度候補値", num: true, render: (r) => <DifficultyBar comp={r.difficulty_components} value={r.difficulty} /> },
    { key: "ops", label: "操作", sortable: false, render: (r) => <span className="row" style={{ gap: 4 }}>
      <button className="ghost" onClick={(e) => { e.stopPropagation(); setSim({ kind: "ramp", id: r.ramp_id }); }}>類似</button>
      <button onClick={(e) => { e.stopPropagation(); addCmp(r.facility_id, r.facility_name); }} data-testid="add-compare">＋比較</button></span> },
  ];
  const facCols: Col<any>[] = [
    { key: "name", label: "施設", render: (r) => <span>{r.name ?? r.facility_id}{tray.a?.facility_id === r.facility_id && <span className="trayslot a"> A</span>}{tray.b?.facility_id === r.facility_id && <span className="trayslot b"> B</span>}</span> },
    { key: "facility_type", label: "種別", render: (r) => <TypeBadge type={r.facility_type} /> }, { key: "shape_class", label: "形状" },
    { key: "shape_confidence", label: "信頼度", num: true, render: (r) => <ConfBar v={r.shape_confidence} /> },
    { key: "ramp_count", label: "ランプ数", num: true }, { key: "loop_ramp_count", label: "ループ", num: true },
    { key: "mean_ramp_length_m", label: "平均ランプ長", num: true }, { key: "min_radius_m", label: "最小R", num: true },
    { key: "grade_up_max_w100", label: "最大100m上り", num: true }, { key: "max_elev_diff_m", label: "最大高低差", num: true },
    { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
    { key: "ops", label: "操作", sortable: false, render: (r) => <span className="row" style={{ gap: 4 }}>
      <button className="ghost" onClick={(e) => { e.stopPropagation(); setSim({ kind: "facility", id: r.facility_id }); }} data-testid="row-similar">類似</button>
      <button onClick={(e) => { e.stopPropagation(); addCmp(r.facility_id, r.name); }} data-testid="add-compare">＋比較</button></span> },
  ];
  const secCols: Col<any>[] = [
    { key: "section_id", label: "区間", render: (r) => <span className="mono">{r.section_id.slice(0, 40)}</span> }, { key: "direction", label: "方向" },
    { key: "dist_standard_m", label: "距離", num: true }, { key: "grade_mean", label: "平均勾配", num: true },
    { key: "grade_up_max_w100", label: "最大上り", num: true }, { key: "grade_down_max_w100", label: "最大下り", num: true },
    { key: "elev_gain_m", label: "累積上昇", num: true }, { key: "gain_per_km", label: "上昇/km", num: true }, { key: "min_radius_m", label: "最小R", num: true },
    { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
  ];
  const cols = mode === "ramp" ? rampCols : mode === "facility" ? facCols : secCols;
  const textQ = active ?? draft;
  return (
    <div className="page">
      <h1>条件検索</h1>
      <div className="tabs" role="tablist">
        {(["ramp", "facility", "section"] as Mode[]).map((m) => (
          <button key={m} className={mode === m ? "on" : ""} onClick={() => { setPresetQ(null); setSp({ mode: m }); }} data-testid={`mode-${m}`}>
            {{ ramp: "ランプ検索", facility: "IC/JCT 検索", section: "区間検索" }[m]}
          </button>
        ))}
      </div>
      <div className="card stack">
        <div className="row">
          <span className="note">目的別プリセット</span>
          {(presets.data ?? []).filter((p) => (p.query.target ?? "ramp") === mode).map((p) => (
            <button key={p.id} title={p.description} onClick={() => { setPresetQ(p.query); run(p.query); }} data-testid={`preset-${p.id}`}>{p.label}</button>
          ))}
        </div>
        <div className="form">
          <label>地域・路線<select value={form.route} onChange={(e) => setF({ route: e.target.value })}><option value="">すべて</option>
            {(routes.data?.routes ?? []).map((r: string) => <option key={r}>{r}</option>)}</select></label>
          {mode === "ramp" && <>
            <label>ランプ区分<select multiple value={form.flows} onChange={(e) => setF({ flows: [...e.target.selectedOptions].map((o) => o.value) })} style={{ height: 52 }}>
              {FLOWS.map((f) => <option key={f} value={f}>{t(`flow.${f}`)}</option>)}</select></label>
            <label>長さ 下限 [m]<input value={form.lenMin} onChange={(e) => setF({ lenMin: e.target.value })} /></label>
            <label>長さ 上限 [m]<input value={form.lenMax} onChange={(e) => setF({ lenMax: e.target.value })} /></label>
            <label>最小R 上限 [m]<input value={form.rMax} onChange={(e) => setF({ rMax: e.target.value })} /></label>
            <label>窓幅<select value={form.window} onChange={(e) => setF({ window: e.target.value })}>{["10", "50", "100", "500"].map((w) => <option key={w} value={w}>{w} m</option>)}</select></label>
            <label>平均上り 下限 [%]<input value={form.upMin} onChange={(e) => setF({ upMin: e.target.value })} data-testid="f-upmin" /></label>
            <label>平均下り 下限 [%]<input value={form.downMin} onChange={(e) => setF({ downMin: e.target.value })} /></label>
            <label>最高標高 下限 [m]<input value={form.zMin} onChange={(e) => setF({ zMin: e.target.value })} /></label>
            <label>DEM 信頼度<select value={form.dem} onChange={(e) => setF({ dem: e.target.value })}><option value="">指定なし</option><option value="medium">中以上</option><option value="high">高</option></select></label>
            <label>OSM 品質<select value={form.osm} onChange={(e) => setF({ osm: e.target.value })}><option value="">指定なし</option><option value="medium">中以上</option><option value="high">高</option></select></label>
          </>}
          {mode !== "section" && <label>IC/JCT 形状<select multiple value={form.shapes} onChange={(e) => setF({ shapes: [...e.target.selectedOptions].map((o) => o.value) })} style={{ height: 52 }}>
            {SHAPES.map((s) => <option key={s}>{s}</option>)}</select></label>}
          {mode === "facility" && <>
            <label>種別<select value={form.type} onChange={(e) => setF({ type: e.target.value })}><option value="">すべて</option>{["IC", "JCT", "SIC", "SA", "PA"].map((x) => <option key={x}>{x}</option>)}</select></label>
            <label>信頼度の下限<input value={form.confMin} onChange={(e) => setF({ confMin: e.target.value })} /></label>
            <label>ランプ数の下限<input value={form.rampsMin} onChange={(e) => setF({ rampsMin: e.target.value })} /></label>
            <label>ループ<select value={form.loop} onChange={(e) => setF({ loop: e.target.value })}><option value="">指定なし</option><option value="yes">あり</option><option value="no">なし</option></select></label>
            <label>最小R 上限 [m]<input value={form.facRMax} onChange={(e) => setF({ facRMax: e.target.value })} /></label>
            <label>最大上り 下限 [%]<input value={form.facUpMin} onChange={(e) => setF({ facUpMin: e.target.value })} /></label>
          </>}
          {mode === "section" && <>
            <label>距離 下限 [m]<input value={form.distMin} onChange={(e) => setF({ distMin: e.target.value })} /></label>
            <label>平均勾配 下限 [%]<input value={form.gradeMeanMin} onChange={(e) => setF({ gradeMeanMin: e.target.value })} /></label>
            <label>最大上り(100m) 下限 [%]<input value={form.upMin} onChange={(e) => setF({ upMin: e.target.value })} /></label>
            <label>最小R 上限 [m]<input value={form.rMax} onChange={(e) => setF({ rMax: e.target.value })} /></label>
          </>}
        </div>
        <details>
          <summary className="note">詳細条件（複合指標・AND/OR）</summary>
          <div className="row" style={{ margin: "6px 0" }}>結合
            <select value={form.extraMode} onChange={(e) => setF({ extraMode: e.target.value as "all" | "any" })}><option value="all">すべて満たす（AND）</option><option value="any">いずれか（OR）</option></select>
            <button onClick={() => setF({ extra: [...form.extra, { field: EXTRA_FIELDS[mode][0], op: "gte", value: "" }] })}>＋条件</button></div>
          {form.extra.map((e, i) => (
            <div key={i} className="row" style={{ marginBottom: 4 }}>
              <select value={e.field} onChange={(ev) => setF({ extra: form.extra.map((x, j) => (j === i ? { ...x, field: ev.target.value } : x)) })}>
                {EXTRA_FIELDS[mode].map((f) => <option key={f}>{f}</option>)}</select>
              <select value={e.op} onChange={(ev) => setF({ extra: form.extra.map((x, j) => (j === i ? { ...x, op: ev.target.value } : x)) })}>
                {["eq", "ne", "gte", "lte", "gt", "lt"].map((o) => <option key={o}>{o}</option>)}</select>
              <input value={e.value} onChange={(ev) => setF({ extra: form.extra.map((x, j) => (j === i ? { ...x, value: ev.target.value } : x)) })} />
              <button className="ghost" onClick={() => setF({ extra: form.extra.filter((_, j) => j !== i) })}>×</button>
            </div>
          ))}
        </details>
        <QueryText text={toText(textQ)} warnings={usesInstantGrade(textQ) ? ["最大瞬間勾配は DEM のノイズに敏感なため、候補抽出には 100 m 窓平均勾配の使用を推奨します"] : []} />
        <div className="row"><button className="primary" onClick={() => run(draft)} data-testid="run-search">検索</button>
          <button className="ghost" onClick={() => { setForm(EMPTY_FORM); setPresetQ(null); }}>クリア</button></div>
      </div>
      <div style={{ marginTop: 12 }}><CompareTray onGoExport={() => nav("/compare")} /></div>
      {tray.open && <div className="card" style={{ marginTop: 12 }}><ComparePanel /></div>}
      <div style={{ marginTop: 12 }}><SimilarPanel /></div>
      <div className="card" style={{ marginTop: 12 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <b data-testid="result-count">{res.data ? `${res.data.count} 件` : "—"}</b><span className="mut">／ 母数 {res.data?.total ?? "—"}</span>
          <span style={{ marginLeft: "auto" }} className="row">
            <button disabled={!items.length} onClick={() => toRange("a")} data-testid="to-range-a">施設を範囲Aへ</button>
            <button disabled={!items.length} onClick={() => toRange("b")}>範囲Bへ</button>
            <button disabled={!items.length || mode !== "ramp"} onClick={highlight} data-testid="highlight-mri">MRI で強調表示</button>
            <button disabled={!items.length} onClick={() => downloadCsv(items, cols.filter((c) => c.key !== "ops"), `search_${mode}.csv`)}>CSV</button>
            <button disabled={!items.length} onClick={exportGeo}>GeoJSON</button>
            <button disabled={!active} onClick={() => saveQ.mutate()}>条件を保存</button>
          </span>
        </div>
        {!active ? <div className="empty">条件を指定して「検索」を押すか、プリセットを選んでください。</div>
          : res.isLoading ? <Skeleton h={200} /> : res.error ? <ErrorBox error={res.error} retry={() => res.refetch()} />
          : !items.length ? <Empty /> : (
            <>
              {res.data.warnings?.map((w: string) => <div key={w} className="warn">⚠ {w}</div>)}
              <DataTable rows={items} cols={cols} testId="result-table"
                onRow={(r) => nav(mode === "ramp" ? `/ramp/${r.ramp_id}` : mode === "facility" ? `/facility/${r.facility_id}` : `/sections?id=${encodeURIComponent(r.section_id)}`)} />
              {mode === "ramp" && <div className="note" style={{ marginTop: 6 }}>{t("common.difficultyNote")}</div>}
            </>
          )}
      </div>
    </div>
  );
}
