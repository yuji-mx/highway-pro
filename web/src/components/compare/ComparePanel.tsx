import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "../../api/client";
import { useCompareTray } from "../../state/stores";
import { ErrorBox, Skeleton, fmt } from "../common/ui";
import RoadMriMap from "../map/RoadMriMap";
import FeatureSquares from "../similar/FeatureSquares";

const R = 1500; // 周辺形状図の半径（同じ縮尺）

function bbox(lon: number, lat: number): [number, number, number, number] {
  const dlat = R / 110950, dlon = R / (111320 * Math.cos((lat * Math.PI) / 180));
  return [lon - dlon, lat - dlat, lon + dlon, lat + dlat];
}

/** 2 地点比較パネル（§4.13）。 */
export function ComparePanel() {
  const { a, b } = useCompareTray();
  const q = useQuery({
    queryKey: ["compare", a?.facility_id, b?.facility_id],
    queryFn: () => api.post<any>("/compare/facilities", { a: a!.facility_id, b: b!.facility_id }),
    enabled: !!a && !!b,
  });
  const fa = useMemo(() => (q.data ? bbox(q.data.a.lon, q.data.a.lat) : null), [q.data]);
  const fb = useMemo(() => (q.data ? bbox(q.data.b.lon, q.data.b.lat) : null), [q.data]);
  if (!a || !b) return <div className="empty">比較トレイに 2 施設を登録してください（各行の「＋比較」）。</div>;
  if (q.isLoading) return <Skeleton h={300} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  return (
    <div className="stack" data-testid="compare-panel">
      <div className="grid2">
        <div><div className="trayslot a" style={{ display: "inline-block" }}>A {d.a.name}</div>
          <RoadMriMap height={240} focus={fa} selectedFacilityId={d.a.facility_id} testId="compare-map-a" /></div>
        <div><div className="trayslot b" style={{ display: "inline-block" }}>B {d.b.name}</div>
          <RoadMriMap height={240} focus={fb} selectedFacilityId={d.b.facility_id} testId="compare-map-b" /></div>
      </div>
      <div className="row">
        <b>A–B 構造類似度</b><span className="simbar"><i style={{ width: `${d.similarity * 100}%` }} /></span>
        <span className="num" data-testid="compare-similarity">{fmt(d.similarity, 2)}</span>
        <FeatureSquares features={d.features} labels={d.feature_labels} squares={d.squares} />
        {!d.shape_match && <span className="warn">形状分類が異なります</span>}
      </div>
      <table className="dt" data-testid="compare-table">
        <thead><tr><th>項目</th><th>A</th><th>B</th></tr></thead>
        <tbody>
          {d.table.map((r: any) => (
            <tr key={r.key} className={r.differs ? "diff" : ""} data-differs={r.differs ? "1" : "0"}>
              <td>{r.label}</td><td className="num">{typeof r.a === "number" ? fmt(r.a, r.a % 1 ? 2 : 0) : fmt(r.a)}</td>
              <td className="num">{typeof r.b === "number" ? fmt(r.b, r.b % 1 ? 2 : 0) : fmt(r.b)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 比較トレイ（A・B のチップ、解除、入れ替え、開閉）。 */
export function CompareTray({ onGoExport }: { onGoExport?: () => void }) {
  const { a, b, open, remove, swap, setOpen } = useCompareTray();
  return (
    <div className="tray" data-testid="compare-tray">
      <b>比較トレイ</b>
      <span className="trayslot a" data-testid="tray-a">A: {a ? a.name ?? a.facility_id : "未登録"}{a && <button className="ghost" onClick={() => remove("a")} aria-label="A を解除">×</button>}</span>
      <span className="trayslot b" data-testid="tray-b">B: {b ? b.name ?? b.facility_id : "未登録"}{b && <button className="ghost" onClick={() => remove("b")} aria-label="B を解除">×</button>}</span>
      <button onClick={swap} disabled={!a && !b}>⇄ 入れ替え</button>
      <button className="primary" onClick={() => setOpen(!open)} disabled={!a || !b} data-testid="tray-open">{open ? "比較表を閉じる" : "比較表を開く"}</button>
      {onGoExport && <button onClick={onGoExport}>比較・出力画面へ</button>}
    </div>
  );
}
