import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errMsg } from "../api/client";
import { ConfBar, ErrorBox, QualityBadge, ReviewBadge, Skeleton, TypeBadge, fmt } from "../components/common/ui";
import RoadMriMap from "../components/map/RoadMriMap";
import SimilarFacilities from "../components/similar/SimilarFacilities";
import DataTable, { type Col } from "../components/tables/DataTable";
import { t } from "../i18n";
import { useCompareTray, useSimilar, useToast } from "../state/stores";

const SHAPES = ["トランペット型", "ダイヤモンド型", "変形ダイヤモンド型", "クローバーリーフ型", "部分クローバー型", "シングルポイント型",
  "平面交差接続型", "ハーフIC", "スマートIC", "複合型", "Y型", "T型", "タービン型", "スタック型", "風車型", "SA/PA", "分類不能"];
const FEAT_JA: Record<string, string> = {
  mainline_count: "接続本線数", connection_directions: "接続方向数", ramp_count: "本線接続ランプ数", loop_ramp_count: "ループランプ数",
  general_road_connections: "一般道接続点数", crossing_road_count: "交差道路数", toll_present: "料金所", toll_on_general_side: "料金所が一般道側",
  layer_count: "立体レベル数", terminating_routes: "終端路線数", mainline_angle_deg: "本線交差角", sweeping_jct_ramps: "旋回ランプ数",
  jct_link_count: "JCT連絡ランプ数", is_half_ic: "ハーフIC", is_smart: "ETC専用",
};

function EditCard({ f }: { f: any }) {
  const qc = useQueryClient();
  const toast = useToast((s) => s.push);
  const [shape, setShape] = useState(f.shape_class);
  const [type, setType] = useState(f.facility_type);
  const [review, setReview] = useState(f.review_status);
  const [comment, setComment] = useState("");
  const hist = useQuery({ queryKey: ["history", f.facility_id], queryFn: () => api.get<any>(`/facilities/${f.facility_id}/history`) });
  const save = useMutation({
    mutationFn: async () => {
      const ops: [string, unknown][] = [];
      if (shape !== f.shape_class) ops.push(["shape_class", shape]);
      if (type !== f.facility_type) ops.push(["facility_type", type]);
      if (review !== f.review_status) ops.push(["review_status", review]);
      if (comment) ops.push(["comment", comment]);
      for (const [field, v] of ops)
        await api.post("/edits", { target_type: "facility", target_id: f.facility_id, field, new_value: v, comment: comment || null });
      return ops.length;
    },
    onSuccess: (n) => {
      toast(n ? `${n} 件の修正を保存しました` : "変更はありません");
      setComment("");
      qc.invalidateQueries();
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const revert = useMutation({
    mutationFn: (id: string) => api.post(`/edits/${id}/revert`, {}),
    onSuccess: () => { toast("修正を取り消しました"); qc.invalidateQueries(); },
    onError: (e) => toast(errMsg(e), true),
  });
  return (
    <div className="card" data-testid="edit-card">
      <h3>手動修正</h3>
      <div className="form">
        <label>形状分類<select value={shape} onChange={(e) => setShape(e.target.value)} data-testid="edit-shape">{SHAPES.map((s) => <option key={s}>{s}</option>)}</select></label>
        <label>種別<select value={type} onChange={(e) => setType(e.target.value)}>{["IC", "JCT", "SIC", "SA", "PA"].map((s) => <option key={s}>{s}</option>)}</select></label>
        <label>確認状態<select value={review} onChange={(e) => setReview(e.target.value)} data-testid="edit-review">
          <option value="unreviewed">未確認</option><option value="needs_review">要確認</option><option value="reviewed">確認済み</option></select></label>
        <label>コメント<input value={comment} onChange={(e) => setComment(e.target.value)} data-testid="edit-comment" /></label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" onClick={() => save.mutate()} disabled={save.isPending} data-testid="edit-save">保存</button>
        <a href={`https://maps.gsi.go.jp/#17/${f.lat}/${f.lon}/&base=seamlessphoto`} target="_blank" rel="noreferrer">地理院地図（航空写真）で確認 ↗</a>
      </div>
      <h3 style={{ marginTop: 12 }}>修正履歴</h3>
      <div data-testid="edit-history" style={{ maxHeight: 200, overflow: "auto" }}>
        {hist.data && (
          <table className="dt"><tbody>
            <tr><td className="mut">{hist.data.auto.created_at?.slice(0, 16)}</td><td colSpan={3}>{hist.data.auto.text_ja}</td></tr>
            {hist.data.edits.map((e: any) => (
              <tr key={e.edit_id}>
                <td className="mut">{new Date(e.created_at).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" })}</td>
                <td>{e.field === "__revert__" ? "取り消し" : e.field}</td>
                <td>{e.field === "__revert__" ? e.new_value?.edit_id?.slice(-6) : `${JSON.stringify(e.old_value)} → ${JSON.stringify(e.new_value)}`}{e.comment ? `（${e.comment}）` : ""}</td>
                <td>{e.user_name} {e.status === "orphaned" && <span className="warn">孤立</span>}
                  {e.field !== "__revert__" && <button className="ghost" onClick={() => revert.mutate(e.edit_id)}>取消</button>}</td>
              </tr>
            ))}
          </tbody></table>
        )}
      </div>
    </div>
  );
}

export default function FacilityDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [hoverRamp, setHoverRamp] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["facility", id], queryFn: () => api.get<any>(`/facilities/${id}`) });
  const add = useCompareTray((s) => s.add);
  const setSim = useSimilar((s) => s.setBase);
  const focus = useMemo<[number, number, number, number] | null>(() => {
    if (!q.data) return null;
    const R = 1800, lat = q.data.lat, lon = q.data.lon;
    const dlat = R / 110950, dlon = R / (111320 * Math.cos((lat * Math.PI) / 180));
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat];
  }, [q.data]);
  if (q.isLoading) return <div className="page"><Skeleton h={400} /></div>;
  if (q.error) return <div className="page"><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  const f = q.data;
  const feats = f.classification?.features ?? {};
  const rampCols: Col<any>[] = [
    { key: "ramp_id", label: "ID", render: (r) => <span className="mono">{r.ramp_id.slice(-8)}</span> },
    { key: "flow", label: "区分", get: (r) => t(`flow.${r.flow}`, r.flow) },
    { key: "carriageway", label: "方向", get: (r) => t(`dir.${r.carriageway}`, r.carriageway) },
    { key: "fromto", label: "接続元→接続先", get: (r) => `${r.from_ref?.kind ?? ""}→${r.to_ref?.kind ?? ""}` },
    { key: "length_m", label: "長さ", num: true, get: (r) => r.length_m },
    { key: "min_radius_m", label: "最小R", num: true },
    { key: "grade_up_max_w100", label: "100m上り", num: true },
    { key: "grade_down_max_w100", label: "最大下り", num: true },
    { key: "elev_diff_m", label: "標高差", num: true },
    { key: "quality_level", label: "品質", render: (r) => <QualityBadge q={r.quality_level} /> },
    { key: "dem_quality", label: "DEM品質", render: (r) => <QualityBadge q={r.dem_quality} /> },
  ];
  return (
    <div className="page" data-testid="facility-detail">
      <h1>
        {f.name ?? "名称不明"} <TypeBadge type={f.facility_type} /> <QualityBadge q={f.quality_level} prefix="品質 " /> <ReviewBadge s={f.review_status} />
        <span className="mut" style={{ fontSize: 12 }}>{(f.route_refs ?? []).join(" / ")} ・ 出口 {f.exit_number ?? "—"} ・ OSM {(f.osm_ids ?? []).join(", ")}</span>
        <span style={{ marginLeft: "auto" }} className="row">
          <button disabled={!f.prev_id} onClick={() => nav(`/facility/${f.prev_id}`)}>← 前へ</button>
          <button disabled={!f.next_id} onClick={() => nav(`/facility/${f.next_id}`)}>次へ →</button>
          <button onClick={() => nav(`/mri?kind=facility&sel=${id}`)}>MRI で表示</button>
          <button onClick={() => add({ facility_id: id, name: f.name })}>＋比較</button>
        </span>
      </h1>
      <div className="grid2">
        <RoadMriMap height={420} focus={focus} selectedFacilityId={id} selectedRampId={hoverRamp} onClick={(c) => c.kind === "ramp" && nav(`/ramp/${c.id}`)} testId="facility-map" />
        <div className="stack">
          <div className="card" data-testid="classification-card">
            <h3>形状分類</h3>
            <div className="row"><b style={{ fontSize: 16 }}>{f.shape_class}</b><ConfBar v={f.shape_confidence} />
              {f.shape_class !== f.shape_class_auto && <span className="note">自動：{f.shape_class_auto} {fmt(f.shape_confidence_auto, 2)} → 手動：{f.shape_class}</span>}</div>
            <div className="note">第2候補 {f.shape_class_2 ?? "—"}（{fmt(f.shape_confidence_2, 2)}） ・ 分類ロジック {f.classification?.classifier_version}</div>
            {f.unclassifiable_reason && <div className="warn">{f.unclassifiable_reason}</div>}
            {f.shape_confidence < 0.7 && f.shape_class !== "SA/PA" && <div className="warn">信頼度が低い（&lt;0.70）ため要確認です。判定根拠を確認してください。</div>}
            <table className="dt" style={{ marginTop: 6 }}><tbody>
              {Object.entries(FEAT_JA).filter(([k]) => k in feats).map(([k, lab]) => (
                <tr key={k}><td>{lab}</td><td className="num">{typeof feats[k] === "number" ? fmt(feats[k], feats[k] % 1 ? 1 : 0) : String(feats[k])}</td><td className="note">ネットワーク</td></tr>
              ))}
            </tbody></table>
            <details><summary className="note">判定根拠</summary>
              <ul>{(f.classification_reasons ?? []).map((r: any, i: number) => <li key={i} style={{ color: r.satisfied ? "var(--green)" : "var(--mut)" }}>{r.satisfied ? "✓" : "✗"} {r.text_ja}</li>)}</ul>
            </details>
          </div>
          <EditCard key={`${f.facility_id}-${f.shape_class}-${f.review_status}`} f={f} />
        </div>
      </div>
      <div className="grid2" style={{ marginTop: 12 }}>
        <div className="card">
          <h3>基本情報</h3>
          <table className="dt"><tbody>
            <tr><td>施設 ID</td><td className="mono">{f.facility_id}</td></tr>
            <tr><td>正規化名称</td><td>{f.normalized_name}</td></tr>
            <tr><td>表記ゆれ</td><td>{(f.name_variants ?? []).join(" / ")}</td></tr>
            <tr><td>座標</td><td className="num">{f.lat.toFixed(6)}, {f.lon.toFixed(6)}</td></tr>
            <tr><td>DEM 標高</td><td className="num">{fmt(f.z_dem, 1, " m")}</td></tr>
            <tr><td>方面・行先</td><td>{Object.entries(f.destinations ?? {}).map(([k, v]: any) => `${t(`dir.${k}`, k)}：${v.join("・")}`).join(" ／ ") || "—"}</td></tr>
            <tr><td>元 OSM オブジェクト</td><td>{(f.osm_objects ?? []).map((o: any) => `${o.osm_type}/${o.osm_id}(${o.role})`).join(", ")}</td></tr>
            <tr><td>OSM 取得日</td><td>{f.osm_timestamp?.slice(0, 10) ?? "—"}</td></tr>
          </tbody></table>
        </div>
        <div className="card">
          <h3>接続関係</h3>
          <table className="dt"><thead><tr><th>路線</th><th>方向</th><th>次の施設</th><th>道路距離</th><th>最大上り</th><th>最小R</th></tr></thead><tbody>
            {(f.sections ?? []).map((s: any) => (
              <tr key={s.section_id} className="click" onClick={() => nav(`/sections?id=${encodeURIComponent(s.section_id)}`)}>
                <td>{s.route_ref}</td><td>{t(`dir.${s.direction}`, s.direction)}</td>
                <td>{s.from_facility_id === id ? `→ ${s.to_name ?? "—"}` : `← ${s.from_name ?? "—"}`}</td>
                <td className="n">{fmt((s.dist_standard_m ?? 0) / 1000, 2, " km")}</td><td className="n">{fmt(s.grade_up_max_w100, 1, "%")}</td><td className="n">{fmt(s.min_radius_m, 0, " m")}</td>
              </tr>
            ))}
          </tbody></table>
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <h3>ランプ一覧（{(f.ramps ?? []).length} 本、行ホバーで地図に強調）</h3>
        <DataTable rows={f.ramps ?? []} cols={rampCols} onRow={(r) => nav(`/ramp/${r.ramp_id}`)} onRowHover={(r) => setHoverRamp(r?.ramp_id ?? null)} maxHeight={320} testId="ramp-table" />
      </div>
      <div className="card" style={{ marginTop: 12 }}>
        <h3 className="row">類似 IC/JCT
          <button style={{ marginLeft: "auto" }} onClick={() => { setSim({ kind: "facility", id }); nav("/search?mode=facility"); }}>条件検索で探す</button>
        </h3>
        <SimilarFacilities facilityId={id} baseName={f.name} />
      </div>
      <div className="note" style={{ marginTop: 8 }}>来歴：run {f.provenance?.run_id} ・ <Link to="/compare">再現性情報</Link></div>
    </div>
  );
}
