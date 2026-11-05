import { useQuery } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import { Skeleton, Toasts } from "./components/common/ui";
import { t } from "./i18n";
import {
  CompareExport, FacilityDetail, FacilityList, MriView, NAV, RampDetail, RangeStats, Region, Search, Sections,
} from "./routes";

function lastId(prefix: string): string | null {
  try {
    return localStorage.getItem(`roadmri.last.${prefix}`);
  } catch {
    return null;
  }
}

function GlobalSearch() {
  const [q, setQ] = useState("");
  const [act, setAct] = useState(0);
  const nav = useNavigate();
  const res = useQuery({
    queryKey: ["gsearch", q],
    queryFn: () => api.get<any>(`/facilities?q=${encodeURIComponent(q)}&limit=12`),
    enabled: q.trim().length > 0,
  });
  const items: any[] = res.data?.items ?? [];
  const go = (id: string) => {
    setQ("");
    nav(`/facility/${id}`);
  };
  return (
    <div className="gsearch">
      <input placeholder="施設名で検索（表記ゆれ対応）" value={q} aria-label="施設名で検索" data-testid="global-search"
        onChange={(e) => { setQ(e.target.value); setAct(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setAct((a) => Math.min(items.length - 1, a + 1));
          if (e.key === "ArrowUp") setAct((a) => Math.max(0, a - 1));
          if (e.key === "Enter" && items[act]) go(items[act].facility_id);
          if (e.key === "Escape") setQ("");
        }} />
      {q && items.length > 0 && (
        <ul role="listbox">
          {items.map((f, i) => (
            <li key={f.facility_id} className={i === act ? "act" : ""} onMouseDown={() => go(f.facility_id)}>
              {f.name ?? f.facility_id} <span className="mut">{f.facility_type}・{(f.route_refs ?? []).join("/")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function App() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const loc = useLocation();
  useEffect(() => {
    const m = loc.pathname.match(/^\/(facility|ramp)\/(.+)$/);
    if (m) {
      try {
        localStorage.setItem(`roadmri.last.${m[1]}`, decodeURIComponent(m[2]));
      } catch {
        /* 保存できない環境では無視 */
      }
    }
  }, [loc.pathname]);
  const m = meta.data;
  const osmDate = m?.sources?.[0]?.osm_timestamp?.slice(0, 10);
  const dem = (m?.dem_datasets ?? []).map((d: any) => d.name).join(" / ");
  return (
    <div className="app">
      <header className="hdr">
        <div className="logo"><b>ROAD</b> MRI</div>
        <div className="sub">{t("app.subtitle")}</div>
        <div className="chips">
          <span className="chip" title="OSM データの日付">OSM {osmDate ?? "—"}</span>
          <span className="chip" title="使用 DEM">DEM {dem || "—"}</span>
          <span className="chip ok" title="取得後はネットワークなしで動作します">オフライン動作可</span>
          <span className="chip" title="現在の run" data-testid="run-chip">run {m?.run?.run_id?.slice(0, 10) ?? "—"}</span>
        </div>
        <GlobalSearch />
      </header>
      <nav className="nav" aria-label="画面">
        {NAV.map((n) => {
          const to = n.path === "/facility" ? (lastId("facility") ? `/facility/${lastId("facility")}` : "/facilities")
            : n.path === "/ramp" ? (lastId("ramp") ? `/ramp/${lastId("ramp")}` : "/search") : n.path;
          const active = loc.pathname === n.path || loc.pathname.startsWith(`${n.path}/`);
          return (
            <NavLink key={n.no} to={to} className={() => (active ? "active" : "")} data-testid={`nav-${n.no}`}>
              <span className="no">{n.no}</span>{t(n.key)}
            </NavLink>
          );
        })}
        <div className="summary" data-testid="nav-summary">
          <div>対象範囲：{m?.run?.region_id ?? "—"}</div>
          <div>施設 {m?.counts?.facility ?? "—"} ／ ランプ {m?.counts?.ramp ?? "—"}</div>
          <div>区間 {m?.counts?.section ?? "—"} ／ 要確認 {m?.counts?.needs_review ?? "—"}</div>
          <div className="note" style={{ marginTop: 6 }}>{m?.attribution}</div>
        </div>
      </nav>
      <main className="main">
        <Suspense fallback={<div className="page"><Skeleton h={300} /></div>}>
          <Routes>
            <Route path="/" element={<Navigate to={m && !m.has_data ? "/region" : "/mri"} replace />} />
            <Route path="/region" element={<Region />} />
            <Route path="/mri" element={<MriView />} />
            <Route path="/facilities" element={<FacilityList />} />
            <Route path="/facility/:id" element={<FacilityDetail />} />
            <Route path="/ramp/:id" element={<RampDetail />} />
            <Route path="/sections" element={<Sections />} />
            <Route path="/search" element={<Search />} />
            <Route path="/stats" element={<RangeStats />} />
            <Route path="/compare" element={<CompareExport />} />
            <Route path="*" element={<div className="page"><div className="empty">ページが見つかりません</div></div>} />
          </Routes>
        </Suspense>
      </main>
      <Toasts />
    </div>
  );
}
