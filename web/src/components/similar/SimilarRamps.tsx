import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { t } from "../../i18n";
import { ErrorBox, Skeleton, fmt } from "../common/ui";

/** 類似ランプ一覧（§4.12.1）。特徴量ごとの寄与を併記する。 */
export default function SimilarRamps({ rampId }: { rampId: string }) {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["similar-ramp", rampId], queryFn: () => api.get<any>(`/ramps/${rampId}/similar?limit=10`) });
  if (q.isLoading) return <Skeleton h={160} />;
  if (q.error) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="tablewrap" data-testid="similar-ramps">
      <table className="dt">
        <thead><tr><th>順位</th><th>ランプ</th><th>施設</th><th>区分</th><th>類似度</th><th>長さ</th><th>最小R</th><th>100m上り</th><th>主な差</th></tr></thead>
        <tbody>
          {q.data.results.map((r: any, i: number) => {
            const top = Object.entries(r.contributions as Record<string, number>).sort((a, b) => b[1] - a[1])[0];
            return (
              <tr key={r.ramp_id} className="click" onClick={() => nav(`/ramp/${r.ramp_id}`)}>
                <td className="n">{i + 1}</td><td className="mono">{r.ramp_id}</td><td>{r.facility_name}</td>
                <td>{t(`flow.${r.flow}`, r.flow)}</td>
                <td><span className="simbar"><i style={{ width: `${r.similarity * 100}%` }} /></span> <span className="num">{fmt(r.similarity, 2)}</span></td>
                <td className="n">{fmt(r.length_m, 0)}</td><td className="n">{fmt(r.min_radius_m, 0)}</td><td className="n">{fmt(r.grade_up_max_w100, 2)}</td>
                <td className="note">{top ? `${top[0]}（${top[1].toFixed(2)}）` : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
