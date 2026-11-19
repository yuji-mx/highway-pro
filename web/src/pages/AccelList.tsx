/** 加速車線一覧（ADR-0018）：自動の値・手動の値・差（手動−自動）・信頼度・確認状態を並べ、自動の測定の精度と偏りを把握する。 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import EChart, { AXIS, TOOLTIP } from "../components/charts/EChart";
import { Empty, ErrorBox, Skeleton, fmt } from "../components/common/ui";
import DataTable, { type Col } from "../components/tables/DataTable";
import type { EndpointInfo } from "../components/accel/AccelLanePanel";
import { t } from "../i18n";
import { convert, unitLabel } from "../state/units";
import { displayName } from "../components/common/names";

interface Row {
  ramp_id: string; facility_id: string; facility_name: string | null; route_ref: string | null; flow: string;
  auto_status: string; auto_reason_text: string | null; auto_method: string | null; auto_confidence: string | null;
  auto_len_m: number | null; manual_len_m: number | null; diff_m: number | null; review_status: string;
  manual_user: string | null; manual_at: string | null; manual_note: string | null;
  display_state: string | null; display_label: string | null; display_length_m: number | null; provisional_length_m: number | null;
  endpoints: { start: EndpointInfo; end: EndpointInfo } | null;
}
interface Stats { n: number; bias_m?: number; median_m?: number; mae_m?: number; rmse_m?: number; p90_abs_m?: number;
  min_m?: number; max_m?: number; share_manual_longer?: number }

const STATUS: Record<string, string> = { measured: "測定（暫定値）", pending: "判定保留", excluded: "対象外" };
const REVIEW: Record<string, string> = { unreviewed: "未確認", confirmed: "自動値どおり", corrected: "修正", not_accel_lane: "加速車線ではない", pending: "判定保留" };
const METHOD: Record<string, string> = { geometry: "並走部", lanes: "車線数の変化", "geometry+lanes": "並走部＋車線数" };
const CONF: Record<string, string> = { high: "高", medium: "中", low: "低" };
const CONF_COLOR: Record<string, string> = { high: "#47e08f", medium: "#ffb53d", low: "#ff5b4f" };
const FILTER_KEYS = ["q", "state", "country", "flow", "auto_status", "reason", "method", "confidence", "review", "min_abs_diff_m"];

const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${fmt(v, 0, " m")}`);

/** 端点の根拠の短い表示：方法・信頼度、保留なら「保留」、記録がなければ「未評価」。 */
const epShort = (e?: EndpointInfo) => (!e ? "—" : !e.evaluated ? "未評価" : e.pending ? "保留" : `${e.method?.replace("osm_", "") ?? "—"}・${CONF[e.confidence ?? ""] ?? "—"}`);
const STATE_FILTER: [string, string][] = [["provisional", "暫定値（地図データ）"], ["pending", "判定保留"], ["excluded", "対象外"], ["confirmed", "確認済み"]];

const COLS: Col<Row>[] = [
  { key: "display_state", label: "状態", render: (r) => (
    <span className={`state-badge state-${r.display_state ?? "excluded"}`} title={r.display_label ?? ""} data-testid="accel-row-state">
      {r.display_label ?? "—"}</span>) },
  { key: "facility_name", label: "施設", get: (r) => displayName(r.facility_name) },
  { key: "ramp_id", label: "ランプ", render: (r) => <span className="mono">{r.ramp_id.slice(-8)}</span> },
  { key: "route_ref", label: "路線" },
  { key: "flow", label: "種類", get: (r) => t(`flow.${r.flow}`, r.flow) },
  { key: "auto_status", label: "自動の判定", render: (r) => r.auto_status !== "measured"
    ? <span title={r.auto_reason_text ?? ""}>{STATUS[r.auto_status] ?? r.auto_status}<div className="note" style={{ maxWidth: 220, whiteSpace: "normal" }}>{r.auto_reason_text}</div></span>
    : STATUS[r.auto_status] },
  { key: "auto_method", label: "判定方法", get: (r) => (r.auto_method ? METHOD[r.auto_method] ?? r.auto_method : "—") },
  { key: "auto_confidence", label: "信頼度", render: (r) => r.auto_confidence
    ? <span style={{ color: CONF_COLOR[r.auto_confidence] }}>{CONF[r.auto_confidence]}</span> : "—" },
  { key: "endpoints", label: "根拠（始点／終点）", sortable: false, get: (r) => `${epShort(r.endpoints?.start)}／${epShort(r.endpoints?.end)}` },
  { key: "auto_len_m", label: "自動（暫定値）", num: true, get: (r) => fmt(r.auto_len_m, 0, " m") },
  { key: "manual_len_m", label: "手動", num: true, get: (r) => fmt(r.manual_len_m, 0, " m") },
  { key: "diff_m", label: "差（手動−自動）", num: true, render: (r) => (
    <span style={{ color: r.diff_m == null ? undefined : Math.abs(r.diff_m) < 10 ? "#47e08f" : r.diff_m > 0 ? "#ffb53d" : "#38e1ff" }}>
      {signed(r.diff_m)}</span>) },
  { key: "review_status", label: "確認状態", render: (r) => (
    <span className={`badge ${r.review_status === "unreviewed" ? "b-medium" : "b-ok"}`}>{REVIEW[r.review_status] ?? r.review_status}</span>) },
  { key: "manual_at", label: "確認者・日時", get: (r) => (r.manual_at ? `${r.manual_user ?? ""} ${r.manual_at.slice(0, 16).replace("T", " ")}` : "") },
  { key: "manual_note", label: "メモ", sortable: false, get: (r) => r.manual_note ?? "" },
];

/** 自動（横軸）× 手動（縦軸）。破線は一致（手動＝自動）。点の色は自動の信頼度。 */
function AccelScatter({ points, onClick }: { points: any[]; onClick: (rid: string) => void }) {
  const u = unitLabel("m");
  const option = useMemo(() => {
    const cv = (v: number) => convert(v, "m")[0];
    const max = Math.ceil((Math.max(100, ...points.flatMap((p) => [cv(p.auto_len_m), cv(p.manual_len_m)])) * 1.05) / 100) * 100;
    const byConf = (c: string) => points.filter((p) => (p.confidence ?? "") === c).map((p) => [cv(p.auto_len_m), cv(p.manual_len_m), p.ramp_id]);
    return {
      backgroundColor: "transparent", grid: { left: 52, right: 14, top: 28, bottom: 36 },
      legend: { data: ["高", "中", "低"], textStyle: { color: "#7d8b9a" }, right: 10 },
      tooltip: { ...TOOLTIP, formatter: (p: any) => `${p.data[2] ?? ""}<br>自動 ${Math.round(p.data[0])} ${u} → 手動 ${Math.round(p.data[1])} ${u}` },
      xAxis: { type: "value", name: `自動 [${u}]`, min: 0, max, ...AXIS }, yAxis: { type: "value", name: `手動 [${u}]`, min: 0, max, ...AXIS },
      series: [
        { name: "一致", type: "line", data: [[0, 0], [max, max]], symbol: "none", silent: true, lineStyle: { color: "#7d8b9a", type: "dashed" } },
        ...(["high", "medium", "low"] as const).map((c) => ({
          name: CONF[c], type: "scatter", symbolSize: 8, data: byConf(c), itemStyle: { color: CONF_COLOR[c] },
        })),
      ],
    } as any;
  }, [points, u]);
  const onEvent = useMemo(() => ({ click: (p: any) => p.data?.[2] && onClick(p.data[2]) }), [onClick]);
  return <EChart option={option} height={300} onEvent={onEvent} testId="accel-scatter" />;
}

function StatsRow({ label, s }: { label: string; s?: Stats }) {
  if (!s || !s.n) return <tr><td>{label}</td><td className="num">0</td><td colSpan={6} className="mut">比較できる件数がありません</td></tr>;
  return (
    <tr>
      <td>{label}</td><td className="num">{s.n}</td><td className="num">{signed(s.bias_m)}</td><td className="num">{signed(s.median_m)}</td>
      <td className="num">{fmt(s.mae_m, 0, " m")}</td><td className="num">{fmt(s.rmse_m, 0, " m")}</td>
      <td className="num">{fmt(s.p90_abs_m, 0, " m")}</td><td className="num">{fmt((s.share_manual_longer ?? 0) * 100, 0, " %")}</td>
    </tr>
  );
}

export default function AccelList() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const p = Object.fromEntries(sp.entries());
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const pageSize: number | undefined = meta.data?.list?.page_size;
  const page = Math.max(0, Number(p.page ?? 0) || 0);
  const sort = { key: p.sort ?? "facility_name", desc: p.order === "desc" };
  const filters = new URLSearchParams(Object.entries(p).filter(([k]) => FILTER_KEYS.includes(k)));
  const listQs = new URLSearchParams(filters);
  listQs.set("sort", sort.key);
  listQs.set("order", sort.desc ? "desc" : "asc");
  if (pageSize) {
    listQs.set("limit", String(pageSize));
    listQs.set("offset", String(page * pageSize));
  }
  const q = useQuery({ queryKey: ["accel-list", listQs.toString()], queryFn: () => api.get<any>(`/accel_lanes?${listQs}`),
    enabled: !!pageSize, placeholderData: (prev) => prev });
  const sm = useQuery({ queryKey: ["accel-summary", filters.toString()], queryFn: () => api.get<any>(`/accel_lanes/summary?${filters}`),
    placeholderData: (prev) => prev });
  const update = (patch: Record<string, string>) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      if (v) n.set(k, v);
      else n.delete(k);
    }
    setSp(n);
  };
  const set = (k: string, v: string) => update({ [k]: v, page: "" });
  const onSort = (key: string) => update({ sort: key, order: sort.key === key && !sort.desc ? "desc" : "asc", page: "" });
  const [text, setText] = useState(p.q ?? "");
  const delay: number | undefined = meta.data?.list?.search_delay_ms;
  useEffect(() => {
    if (delay == null || text === (p.q ?? "")) return;
    const tm = setTimeout(() => set("q", text.trim()), delay);
    return () => clearTimeout(tm);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, delay]);
  const total: number = q.data?.total ?? 0;
  const pages = pageSize ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const from = pageSize ? page * pageSize : 0;
  const s = sm.data;
  const sel = (k: string, label: string, opts: [string, string][], testId?: string) => (
    <label>{label} <select value={p[k] ?? ""} onChange={(e) => set(k, e.target.value)} data-testid={testId}>
      <option value="">すべて</option>{opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
  );
  return (
    <div className="page" data-testid="accel-list">
      <h1>加速車線一覧 <span className="mut num">{q.data ? `${total.toLocaleString("ja-JP")} 件` : ""}</span></h1>
      <div className="note">
        自動で測った値と、航空写真などで確認・修正した値（手動）を比べます。差は手動−自動（正なら自動が短い）。
        検索・統計・難易度は自動の値を使っています。行をクリックするとランプ詳細で確認・修正できます。
      </div>
      <div className="card row" style={{ margin: "10px 0", flexWrap: "wrap" }}>
        <label>検索 <input type="search" value={text} placeholder="施設名・ランプID" onChange={(e) => setText(e.target.value)} style={{ width: 160 }}
          data-testid="accel-search" /></label>
        {sel("country", "国", (meta.data?.countries ?? []).map((c: string) => [c, t(`country.${c}`, c)]))}
        {sel("flow", "種類", ["entry", "jct_link", "facility_link"].map((f) => [f, t(`flow.${f}`, f)]))}
        {sel("state", "状態", STATE_FILTER, "accel-filter-state")}
        {sel("auto_status", "自動の判定", Object.entries(STATUS))}
        {sel("confidence", "信頼度", Object.entries(CONF), "accel-filter-confidence")}
        {sel("method", "判定方法", Object.entries(METHOD))}
        {sel("review", "確認状態", [["unreviewed", "未確認"], ["reviewed", "確認済み（すべて）"], ["confirmed", "自動値どおり"], ["corrected", "修正"],
          ["not_accel_lane", "加速車線ではない"]], "accel-filter-review")}
        <label>差の絶対値 ≥ <input type="number" min="0" step="10" style={{ width: 70 }} value={p.min_abs_diff_m ?? ""}
          onChange={(e) => set("min_abs_diff_m", e.target.value)} /> m</label>
        <a className="btn" style={{ marginLeft: "auto" }} href={`/api/accel_lanes.csv?${filters}`} download="accel_lanes.csv"
          aria-disabled={!total} data-testid="accel-csv">CSV 出力（{total.toLocaleString("ja-JP")} 件）</a>
      </div>

      {s && (
        <div className="grid2" style={{ marginBottom: 10 }}>
          <div className="card" data-testid="accel-summary">
            <h3 style={{ marginTop: 0 }}>自動の測定と手動の確認の比較</h3>
            <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
              <span>対象 <b className="num">{s.total}</b></span>
              <span>自動で測定 <b className="num">{s.by_auto_status.measured ?? 0}</b></span>
              <span>確認済み <b className="num" data-testid="accel-reviewed-count">{s.total - (s.by_review_status.unreviewed ?? 0)}</b>
                <span className="mut">（自動値どおり {s.by_review_status.confirmed ?? 0}・修正 {s.by_review_status.corrected ?? 0}・
                  加速車線ではない {s.by_review_status.not_accel_lane ?? 0}）</span></span>
            </div>
            <table className="dt" style={{ marginTop: 8 }} data-testid="accel-stats">
              <thead><tr><th></th><th>件数</th><th title="差の平均。正なら自動が短い">偏り（平均）</th><th>中央値</th>
                <th title="差の絶対値の平均">平均絶対誤差</th><th>RMSE</th><th>90%点</th><th>手動が長い割合</th></tr></thead>
              <tbody>
                <StatsRow label="全体" s={s.difference} />
                {(["high", "medium", "low"] as const).map((c) => <StatsRow key={c} label={`信頼度 ${CONF[c]}`} s={s.by_confidence[c]} />)}
                {Object.keys(METHOD).map((m) => <StatsRow key={m} label={METHOD[m]} s={s.by_method[m]} />)}
              </tbody>
            </table>
            <div className="note">
              判定の食い違い：自動では測ったが「加速車線ではない」<b className="num">{s.auto_measured_but_not_accel_lane}</b> 件、
              自動では対象外・判定保留としたが手動で測った <b className="num">{s.auto_excluded_but_manual_measured}</b> 件。
              偏り・誤差は、自動と手動の両方に長さがあるもの（自動値どおり・修正）で計算しています。
            </div>
          </div>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>自動 × 手動（点をクリックでランプ詳細）</h3>
            {s.points.length ? <AccelScatter points={s.points} onClick={(rid) => nav(`/ramp/${rid}`)} />
              : <Empty text="確認済みの加速車線がまだありません。ランプ詳細で確認・修正してください。" />}
          </div>
        </div>
      )}

      {q.isLoading || !pageSize ? <Skeleton h={300} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : !q.data.items.length ? (
        filters.toString() ? <Empty text="条件に合う加速車線がありません。" /> : (
          <div data-testid="accel-no-data"><Empty text="加速車線のデータがありません。加速車線の算出より前の版で作ったデータの場合は、
            metrics 段から作り直してください（例：uv run roadmri build --range <地域> --from-step metrics）。" /></div>
        )
      ) : (
        <>
          <DataTable rows={q.data.items} cols={COLS} onRow={(r) => nav(`/ramp/${r.ramp_id}`)} maxHeight="calc(100vh - 260px)"
            testId="accel-table" serverSort={sort} onServerSort={onSort} />
          <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }}>
            <span className="mut num">{(from + 1).toLocaleString("ja-JP")}〜{(from + q.data.items.length).toLocaleString("ja-JP")} 件目</span>
            <button disabled={page <= 0} onClick={() => update({ page: page - 1 > 0 ? String(page - 1) : "" })}>前へ</button>
            <span className="num">{page + 1} / {pages}</span>
            <button disabled={page + 1 >= pages} onClick={() => update({ page: String(page + 1) })}>次へ</button>
          </div>
        </>
      )}
    </div>
  );
}
