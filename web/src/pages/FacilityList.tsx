import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Facility } from "../api/types";
import { ConfBar, Empty, ErrorBox, QualityBadge, ReviewBadge, Skeleton, TypeBadge, fmt } from "../components/common/ui";
import DataTable, { type Col } from "../components/tables/DataTable";

const COLS: Col<Facility>[] = [
  { key: "name", label: "名称", get: (r) => r.name ?? "名称不明" },
  { key: "facility_type", label: "種別", render: (r) => <TypeBadge type={r.facility_type} /> },
  { key: "route_refs", label: "路線", get: (r) => (r.route_refs ?? []).join(", "), sortable: false },
  { key: "exit_number", label: "出口番号" },
  { key: "shape_class", label: "推定分類" },
  { key: "shape_confidence", label: "信頼度", num: true, render: (r) => <ConfBar v={r.shape_confidence} /> },
  { key: "shape_class_2", label: "第2候補", get: (r) => (r.shape_class_2 ? `${r.shape_class_2}（${fmt(r.shape_confidence_2, 2)}）` : "—"), sortable: false },
  { key: "ramp_count", label: "ランプ数", num: true },
  { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
  { key: "review_status", label: "確認状態", render: (r) => <ReviewBadge s={r.review_status} /> },
  { key: "latlon", label: "緯度経度", get: (r) => `${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}`, sortable: false },
];

/** 絞り込みの URL パラメータ（ページ・並べ替え以外）。 */
const FILTER_KEYS = ["type", "route", "min_confidence", "review_status", "quality", "needs_review", "q"];

export default function FacilityList() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const p = Object.fromEntries(sp.entries());
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const pageSize: number | undefined = meta.data?.list?.page_size;
  const page = Math.max(0, Number(p.page ?? 0) || 0);
  const sort = { key: p.sort ?? "name", desc: p.order === "desc" };
  // 一覧は 1 ページ分だけ取得する（並べ替え・検索はサーバー側。全件を画面に並べない）
  const filters = new URLSearchParams(Object.entries(p).filter(([k]) => FILTER_KEYS.includes(k)));
  filters.set("sort", sort.key);
  filters.set("order", sort.desc ? "desc" : "asc");
  const qs = new URLSearchParams(filters);
  if (pageSize) {
    qs.set("limit", String(pageSize));
    qs.set("offset", String(page * pageSize));
  }
  const q = useQuery({
    queryKey: ["facilities", qs.toString()],
    queryFn: () => api.get<any>(`/facilities?${qs}`),
    enabled: !!pageSize,
    placeholderData: (prev) => prev,
  });
  const routes = useQuery({ queryKey: ["sections-routes"], queryFn: () => api.get<any>("/sections") });
  const update = (patch: Record<string, string>) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) {
      if (v) n.set(k, v);
      else n.delete(k);
    }
    setSp(n);
  };
  // 条件を変えたら先頭ページへ戻す
  const set = (k: string, v: string) => update({ [k]: v, page: "" });
  const onSort = (key: string) => update({ sort: key, order: sort.key === key && !sort.desc ? "desc" : "asc", page: "" });
  // 検索語は入力が止まってから反映する
  const [text, setText] = useState(p.q ?? "");
  const delay: number | undefined = meta.data?.list?.search_delay_ms;
  useEffect(() => {
    if (delay == null || text === (p.q ?? "")) return;
    const t = setTimeout(() => set("q", text.trim()), delay);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, delay]);
  const total: number = q.data?.total ?? 0;
  const pages = pageSize ? Math.max(1, Math.ceil(total / pageSize)) : 1;
  const from = pageSize ? page * pageSize : 0;
  return (
    <div className="page">
      <h1>IC/JCT 一覧 <span className="mut num">{q.data ? `${total.toLocaleString("ja-JP")} 件` : ""}</span></h1>
      <div className="card row" style={{ marginBottom: 10 }}>
        <label>検索 <input type="search" value={text} placeholder="名称・出口番号" onChange={(e) => setText(e.target.value)}
          style={{ width: 180 }} data-testid="facility-search" /></label>
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
        {/* CSV は表示中のページではなく、同じ条件の全件をサーバーで作る */}
        <a className="btn" style={{ marginLeft: "auto" }} href={`/api/facilities.csv?${filters}`} download="facilities.csv"
          aria-disabled={!total} data-testid="facility-csv">CSV 出力（{total.toLocaleString("ja-JP")} 件）</a>
      </div>
      {q.isLoading || !pageSize ? <Skeleton h={300} /> : q.error ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : !q.data.items.length ? <Empty /> : (
        <>
          <DataTable rows={q.data.items} cols={COLS} onRow={(r) => nav(`/facility/${r.facility_id}`)} maxHeight="calc(100vh - 250px)"
            testId="facility-table" serverSort={sort} onServerSort={onSort} />
          <div className="row" style={{ marginTop: 8, justifyContent: "flex-end" }} data-testid="facility-pager">
            <span className="mut num">{(from + 1).toLocaleString("ja-JP")}〜{(from + q.data.items.length).toLocaleString("ja-JP")} 件目</span>
            <button disabled={page <= 0} onClick={() => update({ page: page - 1 > 0 ? String(page - 1) : "" })}>前へ</button>
            <span className="num">{page + 1} / {pages}</span>
            <button disabled={page + 1 >= pages} onClick={() => update({ page: String(page + 1) })} data-testid="facility-next">次へ</button>
          </div>
        </>
      )}
    </div>
  );
}
