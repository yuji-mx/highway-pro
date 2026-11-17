/** 加速車線の確認・修正（ADR-0017）。自動の測定値は変えず、手動の値を別に保存して比較する。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FeatureCollection, Geometry } from "geojson";
import type { Map as MlMap, MapMouseEvent } from "maplibre-gl";
import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../../api/client";
import { useLayers } from "../../state/stores";
import { fmt } from "../common/ui";
import type { Overlay } from "../map/RoadMriMap";

type LonLat = [number, number];

interface Part {
  status: string | null;
  length_m: number | null;
  start: LonLat | null;
  end: LonLat | null;
}

interface AccelData {
  ramp_id: string;
  auto: Part & {
    reason: string | null; reason_text: string | null; method: string | null; confidence: string | null; notes: string | null;
    parallel_m: number | null; added_lane_m: number | null;
  };
  manual: (Part & { note: string | null; edit_id: string; user: string | null; at: string | null }) | null;
  difference_m: number | null;
  path: Geometry | null;
  auto_span: Geometry | null;
  manual_span: Geometry | null;
  history: { edit_id: string; created_at: string; user_name: string | null; comment: string | null; new_value: any; status?: string }[];
}

interface Preview {
  length_m: number;
  start: LonLat;
  end: LonLat;
  span: Geometry;
  warnings: string[];
}

const COLORS = { auto: "#38e1ff", start: "#47e08f", end: "#ff5b4f", manual: "#ffb53d", draft: "#ff4fd8", path: "rgba(255,255,255,0.35)" };
const STATUS: Record<string, string> = { measured: "測定", excluded: "測定対象外" };
const MANUAL: Record<string, string> = { confirmed: "自動値どおりと確認", corrected: "修正", not_accel_lane: "加速車線ではない" };
const METHOD: Record<string, string> = { geometry: "並走部から", lanes: "車線数の変化から", "geometry+lanes": "並走部と車線数の変化から" };
const CONF: Record<string, string> = { high: "高", medium: "中", low: "低" };

const fc = (...gs: (Geometry | null | undefined)[]): FeatureCollection => ({
  type: "FeatureCollection",
  features: gs.filter((g): g is Geometry => !!g).map((g) => ({ type: "Feature", properties: {}, geometry: g })),
});
const pt = (c: LonLat | null | undefined): Geometry | null => (c ? { type: "Point", coordinates: c } : null);
const ll = (c: LonLat | null | undefined) => (c ? `${c[1].toFixed(6)}, ${c[0].toFixed(6)}` : "—");

export default function AccelLanePanel({ rampId, map, onOverlays, onFocus }: {
  rampId: string;
  map: MlMap | null;
  onOverlays: (o: Overlay[]) => void;
  onFocus: (b: [number, number, number, number]) => void;
}) {
  const qc = useQueryClient();
  const L = useLayers();
  const q = useQuery({ queryKey: ["accel", rampId], queryFn: () => api.get<AccelData>(`/ramps/${rampId}/accel_lane`) });
  const [mode, setMode] = useState<"view" | "start" | "end" | "preview">("view");
  const [draft, setDraft] = useState<{ start?: LonLat; end?: LonLat }>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [comment, setComment] = useState("");
  const [err, setErr] = useState<string | null>(null);
  // 始点・終点の指定や拡大のときは、地図が見える位置までスクロールする（パネルは地図の下にある）
  const showMap = () => map?.getContainer().scrollIntoView({ block: "center", behavior: "auto" });
  const reset = () => { setMode("view"); setDraft({}); setPreview(null); setComment(""); setErr(null); };
  useEffect(reset, [rampId]);

  const refresh = (d: AccelData) => {
    qc.setQueryData(["accel", rampId], d);
    qc.invalidateQueries({ queryKey: ["ramp", rampId] });
  };
  const save = useMutation({
    mutationFn: (body: object) => api.post<AccelData>(`/ramps/${rampId}/accel_lane`, body),
    onSuccess: (d) => { refresh(d); reset(); },
    onError: (e) => setErr(errMsg(e)),
  });
  const revert = useMutation({
    mutationFn: (editId: string) => api.post(`/edits/${editId}/revert`, {}),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["accel", rampId] }); qc.invalidateQueries({ queryKey: ["ramp", rampId] }); },
    onError: (e) => setErr(errMsg(e)),
  });

  // 地図のクリックで始点・終点を指定する
  useEffect(() => {
    if (!map || (mode !== "start" && mode !== "end")) return;
    const canvas = map.getCanvas();
    canvas.style.cursor = "crosshair";
    const click = (e: MapMouseEvent) => {
      const p: LonLat = [e.lngLat.lng, e.lngLat.lat];
      if (mode === "start") {
        setDraft({ start: p });
        setMode("end");
        return;
      }
      const d = { ...draft, end: p };
      setDraft(d);
      setErr(null);
      api.post<Preview>(`/ramps/${rampId}/accel_lane/measure`, { start: d.start, end: d.end })
        .then((r) => { setPreview(r); setMode("preview"); })
        .catch((x) => { setErr(errMsg(x)); setMode("start"); setDraft({}); });
    };
    map.on("click", click);
    return () => {
      map.off("click", click);
      canvas.style.cursor = "";
    };
  }, [map, mode, draft, rampId]);

  // 地図への重ね描き：道筋（白）、自動（水色の線・緑の始点・赤の終点）、手動（橙）、指定中（桃）
  const d = q.data;
  const overlays = useMemo<Overlay[]>(() => {
    if (!d) return [];
    const o: Overlay[] = [{ id: "accel-path", data: fc(d.path), color: COLORS.path, width: 2 }];
    if (d.auto_span) o.push({ id: "accel-auto", data: fc(d.auto_span), color: COLORS.auto, width: 4 });
    if (d.manual?.status !== "not_accel_lane" && d.manual_span) o.push({ id: "accel-manual", data: fc(d.manual_span), color: COLORS.manual, width: 4 });
    if (preview) o.push({ id: "accel-draft", data: fc(preview.span), color: COLORS.draft, width: 4 });
    o.push({ id: "accel-auto-s", data: fc(pt(d.auto.start)), color: COLORS.start, width: 7, kind: "circle" });
    o.push({ id: "accel-auto-e", data: fc(pt(d.auto.end)), color: COLORS.end, width: 7, kind: "circle" });
    if (d.manual?.start) o.push({ id: "accel-manual-pts", data: fc(pt(d.manual.start), pt(d.manual.end)), color: COLORS.manual, width: 11, kind: "circle" });
    const dp = preview ? [preview.start, preview.end] : [draft.start, draft.end];
    o.push({ id: "accel-draft-pts", data: fc(pt(dp[0]), pt(dp[1])), color: COLORS.draft, width: 9, kind: "circle" });
    return o;
  }, [d, preview, draft]);
  useEffect(() => onOverlays(overlays), [overlays, onOverlays]);

  const zoomTo = () => {
    const cs: LonLat[] = [];
    for (const g of [d?.auto_span, d?.manual_span, preview?.span]) if (g?.type === "LineString") cs.push(...(g.coordinates as LonLat[]));
    if (!cs.length && d?.path?.type === "LineString") cs.push(...(d.path.coordinates as LonLat[]));
    if (!cs.length) return;
    const xs = cs.map((c) => c[0]), ys = cs.map((c) => c[1]);
    const pad = 0.0015;
    onFocus([Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) + pad, Math.max(...ys) + pad]);
    showMap();
  };

  if (q.isLoading) return <div className="skel" style={{ height: 160 }} />;
  if (q.error || !d) return <div className="warn">加速車線の情報を読み込めません：{errMsg(q.error)}</div>;
  const a = d.auto, m = d.manual;
  return (
    <div data-testid="accel-panel">
      <div className="row">
        <h3 style={{ margin: 0 }}>加速車線の確認・修正</h3>
        <span className="row" style={{ marginLeft: "auto" }}>
          <button onClick={zoomTo} data-testid="accel-zoom">合流部を拡大</button>
          <button className={L.visible.aerial ? "primary" : ""} onClick={() => L.toggle("aerial")} data-testid="accel-aerial">航空写真</button>
        </span>
      </div>
      <div className="note">
        始点＝隔て部分（ゼブラ・縁石）が終わり車線境界が破線になる点、終点＝加速車線が完全になくなる点（テーパーの終わり）。
        手動の値は自動の値とは別に保存され、自動の値は変わりません。
      </div>
      <table className="dt" data-testid="accel-compare">
        <thead><tr><th></th><th>自動</th><th>手動</th></tr></thead>
        <tbody>
          <tr><td>状態</td>
            <td>{STATUS[a.status ?? ""] ?? "—"}{a.status === "excluded" && <div className="note">{a.reason_text}</div>}</td>
            <td data-testid="accel-manual-status">{m ? MANUAL[m.status ?? ""] ?? m.status : "未確認"}</td></tr>
          <tr><td>長さ</td><td className="num">{fmt(a.length_m, 0, " m")}</td>
            <td className="num" data-testid="accel-manual-length">{fmt(m?.length_m, 0, " m")}</td></tr>
          <tr><td>差（手動−自動）</td><td></td>
            <td className="num" data-testid="accel-diff">{d.difference_m == null ? "—" : `${d.difference_m > 0 ? "+" : ""}${fmt(d.difference_m, 0, " m")}`}</td></tr>
          <tr><td>始点 <span style={{ color: COLORS.start }}>●</span></td><td className="mono">{ll(a.start)}</td><td className="mono">{ll(m?.start)}</td></tr>
          <tr><td>終点 <span style={{ color: COLORS.end }}>●</span></td><td className="mono">{ll(a.end)}</td><td className="mono">{ll(m?.end)}</td></tr>
          <tr><td>判定方法・信頼度</td><td>{a.method ? `${METHOD[a.method] ?? a.method}・${CONF[a.confidence ?? ""] ?? "—"}` : "—"}
            {a.notes && <div className="note">{a.notes}</div>}</td>
            <td>{m ? `${m.user ?? ""} ${m.at?.slice(0, 16).replace("T", " ") ?? ""}` : ""}{m?.note && <div className="note">{m.note}</div>}</td></tr>
        </tbody>
      </table>
      <div className="note row" style={{ gap: 12 }}>地図：<span style={{ color: COLORS.auto }}>━ 自動</span><span style={{ color: COLORS.manual }}>━ 手動</span>
        <span style={{ color: COLORS.draft }}>━ 指定中</span><span style={{ color: "#bbb" }}>━ 道筋（ランプ＋下流の本線）</span></div>

      {mode === "view" ? (
        <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
          <button disabled={a.status !== "measured" || save.isPending} onClick={() => save.mutate({ action: "confirmed", comment: comment || null })}
            data-testid="accel-confirm">自動値どおりと確認</button>
          <button className="primary" onClick={() => { setErr(null); setMode("start"); setDraft({}); setPreview(null); showMap(); }} data-testid="accel-correct">
            始点・終点を指定して修正</button>
          <button disabled={save.isPending} onClick={() => save.mutate({ action: "not_accel_lane", comment: comment || null })}
            data-testid="accel-not">加速車線ではない</button>
          <input placeholder="メモ（確認に使った資料など）" value={comment} onChange={(e) => setComment(e.target.value)} style={{ flex: 1, minWidth: 160 }}
            data-testid="accel-comment" />
        </div>
      ) : (
        <div className="card" style={{ marginTop: 8 }} data-testid="accel-picking">
          {mode === "start" && <div>地図で<b>始点</b>（隔て部分が終わる点）をクリックしてください。</div>}
          {mode === "end" && <div>地図で<b>終点</b>（加速車線が完全になくなる点）をクリックしてください。OSM で本線に接続する点より先でも指定できます。</div>}
          {mode === "preview" && preview && (
            <>
              <div>指定した区間の長さ：<b className="num" data-testid="accel-preview-length">{fmt(preview.length_m, 0, " m")}</b>
                （自動 {fmt(a.length_m, 0, " m")}）</div>
              {preview.warnings.map((w) => <div key={w} className="warn">⚠ {w}</div>)}
              <input placeholder="メモ（確認に使った資料など）" value={comment} onChange={(e) => setComment(e.target.value)} style={{ width: "100%", marginTop: 6 }}
                data-testid="accel-comment" />
            </>
          )}
          <div className="row" style={{ marginTop: 6 }}>
            {mode === "preview" && (
              <button className="primary" disabled={save.isPending || !!preview?.warnings.length}
                onClick={() => save.mutate({ action: "corrected", start: draft.start, end: draft.end, comment: comment || null })}
                data-testid="accel-save">修正を保存</button>
            )}
            <button onClick={() => { setMode("start"); setDraft({}); setPreview(null); }}>指定し直す</button>
            <button onClick={reset}>キャンセル</button>
          </div>
        </div>
      )}
      {err && <div className="warn" data-testid="accel-error">⚠ {err}</div>}

      {d.history.length > 0 && (
        <>
          <div className="note" style={{ marginTop: 8 }}>履歴（追記のみ。取り消しも記録されます）</div>
          <table className="dt" data-testid="accel-history">
            <tbody>
              {[...d.history].reverse().map((h) => (
                <tr key={h.edit_id}>
                  <td className="mono">{h.created_at?.slice(0, 16).replace("T", " ")}</td>
                  <td>{MANUAL[h.new_value?.action] ?? h.new_value?.action}</td>
                  <td className="num">{fmt(h.new_value?.length_m, 0, " m")}</td>
                  <td>{h.user_name ?? ""}</td>
                  <td>{h.comment ?? ""}</td>
                  <td>{h.edit_id === m?.edit_id && (
                    <button disabled={revert.isPending} onClick={() => revert.mutate(h.edit_id)} data-testid="accel-revert">取り消す</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
