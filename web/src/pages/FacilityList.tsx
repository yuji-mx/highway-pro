import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Facility } from "../api/types";
import { ConfBar, Empty, ErrorBox, QualityBadge, ReviewBadge, Skeleton, TypeBadge, fmt } from "../components/common/ui";
import DataTable, { downloadCsv, type Col } from "../components/tables/DataTable";

const COLS: Col<Facility>[] = [
  { key: "name", label: "名称", get: (r) => r.name ?? "名称不明" },
  { key: "facility_type", label: "種別", render: (r) => <TypeBadge type={r.facility_type} /> },
  { key: "route_refs", label: "路線", get: (r) => (r.route_refs ?? []).join(", ") },
  { key: "exit_number", label: "出口番号" },
  { key: "shape_class", label: "推定分類" },
  { key: "shape_confidence", label: "信頼度", num: true, render: (r) => <ConfBar v={r.shape_confidence} /> },
  { key: "shape_class_2", label: "第2候補", get: (r) => (r.shape_class_2 ? `${r.shape_class_2}（${fmt(r.shape_confidence_2, 2)}）` : "—") },
  { key: "ramp_count", label: "ランプ数", num: true },
  { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
  { key: "review_status", label: "確認状態", render: (r) => <ReviewBadge s={r.review_status} /> },
  { key: "latlon", label: "緯度経度", get: (r) => `${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}`, sortable: false },
];

export default function FacilityList() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const p = Object.fromEntries(sp.entries());
  const qs = new URLSearchParams({ limit: "5000", ...p }).toString();
  const q = useQuery({ queryKey: ["facilities", qs], queryFn: () => api.get<any>(`/facilities?${qs}`) });
  const routes = useQuery({ queryKey: ["sections-routes"], queryFn: () => api.get<any>("/sections") });
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n);
  };
  return (
    <div className="page">
      <h1>IC/JCT 一覧 <span className="mut num">{q.data ? `${q.data.total} 件` : ""}</span></h1>
      <div className="card row" style={{ marginBottom: 10 }}>
        <label>種別 <select value={p.type ?? ""} onChange={(e) => set("type", e.target.value)} data-testid="filter-type">
          <option value="">すべて</option>{["IC", "JCT", "SIC", "SA", "PA"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label>路線 <select value={p.route ?? ""} onChange={(e) => set("route", e.target.value)}>
          <option value="">すべて</option>{(routes.data?.routes ?? []).map((x: string) => <option key={x}>{x}</option>)}</select></label>
        <label>信頼度の下限 <input type="number" step="0.05" min="0" max="1" style={{ width: 70 }} value={p.min_confidence ?? ""} onChange={(e) => set("min_confidence", e.target.value)} /></label>
        <label>確認状態 <select value={p.review_status ?? ""} onChange={(e) => set("review_status", e.target.value)}>
          <option value="">すべて</option><option value="unreviewed">未確認</option><option value="needs_review">要確認</option><option value="reviewed">確認済み</option></select></label>
        <label>品質 <select value={p.quality ?? ""} onChange={(e) => set("quality", e.target.value)}>
          <option value="">すべて</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option><option value="unusable">使用不可</option></select></label>
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={p.needs_review === "true"} onChange={(e) => set("needs_review", e.target.checked ? "true" : "")} data-testid="needs-review" />要確認だけ</label>
        <button style={{ marginLeft: "auto" }} disabled={!q.data?.items?.length} onClick={() => downloadCsv(q.data.items, COLS, "facilities.csv")}>CSV 出力</button>
      </div>
      {q.isLoading ? <Skeleton h={300} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : !q.data.items.length ? <Empty /> : (
        <DataTable rows={q.data.items} cols={COLS} onRow={(r) => nav(`/facility/${r.facility_id}`)} maxHeight="calc(100vh - 210px)" testId="facility-table" />
      )}
    </div>
  );
}
