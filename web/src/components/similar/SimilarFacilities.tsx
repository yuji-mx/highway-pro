import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useCompareTray } from "../../state/stores";
import { ErrorBox, Skeleton, fmt } from "../common/ui";
import FeatureSquares from "./FeatureSquares";

/** 類似 IC/JCT カード（§4.12.2）。 */
export default function SimilarFacilities({ facilityId, baseName, limit = 10 }: { facilityId: string; baseName?: string | null; limit?: number }) {
  const nav = useNavigate();
  const setBoth = useCompareTray((s) => s.setBoth);
  const q = useQuery({ queryKey: ["similar-fac", facilityId, limit], queryFn: () => api.get<any>(`/facilities/${facilityId}/similar?limit=${limit}`) });
  if (q.isLoading) return <Skeleton h={160} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  if (!d.results.length) return <div className="empty">比較できる施設がありません（SA/PA は SA/PA 同士のみ比較します）。</div>;
  return (
    <div className="tablewrap" data-testid="similar-facilities">
      <table className="dt">
        <thead>
          <tr><th>順位</th><th>施設</th><th>種別</th><th>形状分類</th><th>類似度</th><th>特徴量ごとの近さ</th><th>ランプ数</th><th>最小R</th><th>最大上り</th><th></th></tr>
        </thead>
        <tbody>
          {d.results.map((r: any, i: number) => (
            <tr key={r.facility_id}>
              <td className="n">{i + 1}</td>
              <td>{r.name ?? r.facility_id}</td>
              <td>{r.facility_type}</td>
              <td>{r.shape_class}{!r.shape_match && <span className="note">（形状違い）</span>}</td>
              <td><span className="simbar"><i style={{ width: `${r.similarity * 100}%` }} /></span> <span className="num">{fmt(r.similarity, 2)}</span></td>
              <td><FeatureSquares features={r.features} labels={d.feature_labels} squares={d.squares} values={r.values} /></td>
              <td className="n">{fmt(r.ramp_count)}</td>
              <td className="n">{fmt(r.min_radius_m, 0, " m")}</td>
              <td className="n">{fmt(r.grade_up_max_w100, 1, "%")}</td>
              <td>
                <button className="ghost" onClick={() => nav(`/facility/${r.facility_id}`)}>詳細</button>{" "}
                <button data-testid="similar-compare" onClick={() => {
                  setBoth({ facility_id: facilityId, name: baseName ?? d.base.name }, { facility_id: r.facility_id, name: r.name });
                  nav(`/search?mode=facility&compare=1`);
                }}>比較</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
