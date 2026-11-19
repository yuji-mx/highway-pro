/** 加速車線の確認・修正（ADR-0017）。自動の測定値は変えず、手動の値を別に保存して比較する。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { FeatureCollection, Geometry } from "geojson";
import type { Map as MlMap, MapMouseEvent } from "maplibre-gl";
import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "../../api/client";
import { useLayers } from "../../state/stores";
import { fmt } from "../common/ui";
import { PIN_SYMBOL, type Overlay, type Pin } from "../map/RoadMriMap";

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
  manual: (Part & { note: string | null; edit_id: string; user: string | null; at: string | null; exclusion_reason?: string | null }) | null;
  display: AccelDisplay | null;
  difference_m: number | null;
  path: Geometry | null;
  auto_span: Geometry | null;
  manual_span: Geometry | null;
  history: { edit_id: string; created_at: string; user_name: string | null; comment: string | null; new_value: any; status?: string }[];
}

/** 端点ごとの根拠（API の display.endpoints）。evaluated=false は以前のデータ（「未評価」）。 */
export interface EndpointInfo {
  which: "start" | "end"; label: string; lonlat: LonLat | null; method: string | null; method_text: string | null;
  confidence: string | null; confidence_text: string | null; pending: string | null; pending_text: string | null; evaluated: boolean;
}
export interface AccelDisplay {
  state: "provisional" | "pending" | "excluded" | "confirmed"; label: string; length_m: number | null;
  provisional_length_m: number | null; endpoints: { start: EndpointInfo; end: EndpointInfo }; source: string;
}

interface Preview {
  length_m: number;
  start: LonLat;
  end: LonLat;
  span: Geometry;
  warnings: string[];
}

const COLORS = { auto: "#38e1ff", start: "#47e08f", end: "#ff5b4f", manual: "#ffb53d", draft: "#ff4fd8", path: "rgba(255,255,255,0.35)" };
const MANUAL: Record<string, string> = { confirmed: "自動値どおりと確認", corrected: "修正", not_accel_lane: "加速車線ではない", pending: "判定保留" };
// 手動の判定保留の理由・対象外の理由（API と同じコード）
const PENDING_REASONS: [string, string][] = [["shadow", "影で目印が見えない"], ["bridge", "橋・高架で目印が見えない"], ["out_of_image", "航空写真の範囲外"],
  ["adjacent_ramp", "隣接するランプの線と重なる"], ["construction", "工事中・撮影時期による違い"], ["other", "その他（メモ参照）"]];
const EXCLUSIONS: [string, string][] = [["lane_addition", "対象外・車線追加"], ["auxiliary_to_exit", "対象外・出口まで続く付加車線"], ["other", "対象外（その他）"]];
// 地図の目印：形と文字で区別（色だけに頼らない）
const PINS: Record<string, { shape: Pin["shape"]; color: string; text: string }> = {
  autoStart: { shape: "triangle", color: "#38e1ff", text: "始点（自動）" },
  autoEnd: { shape: "square", color: "#38e1ff", text: "終点（自動）" },
  manualStart: { shape: "triangle", color: "#ffb53d", text: "始点（手動）" },
  manualEnd: { shape: "square", color: "#ffb53d", text: "終点（手動）" },
  draftStart: { shape: "diamond", color: "#ff4fd8", text: "始点（指定中）" },
  draftEnd: { shape: "diamond", color: "#ff4fd8", text: "終点（指定中）" },
};

/** 端点の根拠の 1 行（方法・信頼度・保留理由。記録がなければ「未評価」）。 */
export function endpointText(e?: EndpointInfo | null): string {
  if (!e) return "—";
  if (!e.evaluated) return "未評価";
  if (e.pending) return `判定保留：${e.pending_text ?? e.pending}`;
  return `${e.method_text ?? e.method ?? "—"}・信頼度 ${e.confidence_text ?? "—"}`;
}
const METHOD: Record<string, string> = { geometry: "並走部から", lanes: "車線数の変化から", "geometry+lanes": "並走部と車線数の変化から" };
const CONF: Record<string, string> = { high: "高", medium: "中", low: "低" };

const fc = (...gs: (Geometry | null | undefined)[]): FeatureCollection => ({
  type: "FeatureCollection",
  features: gs.filter((g): g is Geometry => !!g).map((g) => ({ type: "Feature", properties: {}, geometry: g })),
});
const ll = (c: LonLat | null | undefined) => (c ? `${c[1].toFixed(6)}, ${c[0].toFixed(6)}` : "—");

export default function AccelLanePanel({ rampId, map, onOverlays, onPins, onFocus }: {
  rampId: string;
  map: MlMap | null;
  onOverlays: (o: Overlay[]) => void;
  onPins: (p: Pin[]) => void;
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
  const [pendingForm, setPendingForm] = useState<{ start: string; end: string } | null>(null);
  const [exclusion, setExclusion] = useState("lane_addition");
  // 始点・終点の指定や拡大のときは、地図が見える位置までスクロールする（パネルは地図の下にある）
  const showMap = () => map?.getContainer().scrollIntoView({ block: "center", behavior: "auto" });
  const reset = () => { setMode("view"); setDraft({}); setPreview(null); setComment(""); setErr(null); setPendingForm(null); };
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

  // 地図への重ね描き：道筋（白）、自動の区間（水色）、手動の区間（橙）、指定中（桃）。始点・終点は形と文字の目印（pins）
  const d = q.data;
  const overlays = useMemo<Overlay[]>(() => {
    if (!d) return [];
    const o: Overlay[] = [{ id: "accel-path", data: fc(d.path), color: COLORS.path, width: 2 }];
    if (d.auto_span) o.push({ id: "accel-auto", data: fc(d.auto_span), color: COLORS.auto, width: 4 });
    if (d.manual?.status !== "not_accel_lane" && d.manual_span) o.push({ id: "accel-manual", data: fc(d.manual_span), color: COLORS.manual, width: 4 });
    if (preview) o.push({ id: "accel-draft", data: fc(preview.span), color: COLORS.draft, width: 4 });
    return o;
  }, [d, preview]);
  useEffect(() => onOverlays(overlays), [overlays, onOverlays]);
  const pins = useMemo<Pin[]>(() => {
    if (!d) return [];
    const out: Pin[] = [];
    const add = (id: string, key: keyof typeof PINS, c: LonLat | null | undefined) => { if (c) out.push({ id, lonlat: c, ...PINS[key] }); };
    add("auto-start", "autoStart", d.auto.start);
    add("auto-end", "autoEnd", d.auto.end);
    if (d.manual?.status === "corrected") {
      add("manual-start", "manualStart", d.manual.start);
      add("manual-end", "manualEnd", d.manual.end);
    }
    add("draft-start", "draftStart", preview?.start ?? draft.start);
    add("draft-end", "draftEnd", preview?.end ?? draft.end);
    return out;
  }, [d, preview, draft]);
  useEffect(() => onPins(pins), [pins, onPins]);

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
            <td colSpan={2}><span className={`state-badge state-${d.display?.state ?? "excluded"}`} data-testid="accel-state">{d.display?.label ?? "—"}</span>
              {d.display?.state === "pending" && d.display.provisional_length_m != null && (
                <span className="note" style={{ marginLeft: 6 }}>地図データによる暫定値 {fmt(d.display.provisional_length_m, 0, " m")}</span>)}
              {a.status !== "measured" && a.reason_text && <div className="note">{a.reason_text}</div>}</td></tr>
          <tr><td>確認</td><td className="note">自動（地図データ）</td>
            <td data-testid="accel-manual-status">{m ? MANUAL[m.status ?? ""] ?? m.status : "未確認"}</td></tr>
          <tr><td>長さ</td><td className="num">{fmt(a.length_m, 0, " m")}</td>
            <td className="num" data-testid="accel-manual-length">{fmt(m?.length_m, 0, " m")}</td></tr>
          <tr><td>差（手動−自動）</td><td></td>
            <td className="num" data-testid="accel-diff">{d.difference_m == null ? "—" : `${d.difference_m > 0 ? "+" : ""}${fmt(d.difference_m, 0, " m")}`}</td></tr>
          <tr><td>始点 <span style={{ color: PINS.autoStart.color }}>{PIN_SYMBOL.triangle}</span></td><td className="mono">{ll(a.start)}</td><td className="mono">{ll(m?.start)}</td></tr>
          <tr><td>始点の根拠</td><td colSpan={2} data-testid="accel-start-evidence">{endpointText(d.display?.endpoints.start)}</td></tr>
          <tr><td>終点 <span style={{ color: PINS.autoEnd.color }}>{PIN_SYMBOL.square}</span></td><td className="mono">{ll(a.end)}</td><td className="mono">{ll(m?.end)}</td></tr>
          <tr><td>終点の根拠</td><td colSpan={2} data-testid="accel-end-evidence">{endpointText(d.display?.endpoints.end)}</td></tr>
          <tr><td>判定方法・信頼度</td><td>{a.method ? `${METHOD[a.method] ?? a.method}・${CONF[a.confidence ?? ""] ?? "—"}` : "—"}
            {a.notes && <div className="note">{a.notes}</div>}</td>
            <td>{m ? `${m.user ?? ""} ${m.at?.slice(0, 16).replace("T", " ") ?? ""}` : ""}{m?.note && <div className="note">{m.note}</div>}</td></tr>
        </tbody>
      </table>
      <div className="note row" style={{ gap: 10, flexWrap: "wrap" }} data-testid="accel-legend">地図の目印：
        {Object.values(PINS).map((x) => <span key={x.text}><span style={{ color: x.color }}>{PIN_SYMBOL[x.shape]}</span> {x.text}</span>)}
        <span><span style={{ color: COLORS.auto }}>━</span> 自動の区間</span><span><span style={{ color: COLORS.manual }}>━</span> 手動の区間</span>
        <span><span style={{ color: COLORS.draft }}>━</span> 指定中</span><span><span style={{ color: "#bbb" }}>━</span> 道筋（ランプ＋下流の本線）</span></div>

      {mode === "view" ? (
        <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
          <button disabled={a.status !== "measured" || save.isPending} onClick={() => save.mutate({ action: "confirmed", comment: comment || null })}
            data-testid="accel-confirm">自動値どおりと確認</button>
          <button className="primary" onClick={() => { setErr(null); setMode("start"); setDraft({}); setPreview(null); showMap(); }} data-testid="accel-correct">
            始点・終点を指定して修正</button>
          <select value={exclusion} onChange={(e) => setExclusion(e.target.value)} data-testid="accel-exclusion" aria-label="対象外の理由">
            {EXCLUSIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <button disabled={save.isPending} onClick={() => save.mutate({ action: "not_accel_lane", exclusion_reason: exclusion, comment: comment || null })}
            data-testid="accel-not">対象外にする</button>
          <button onClick={() => setPendingForm(pendingForm ? null : { start: "", end: "shadow" })} data-testid="accel-pending">判定保留</button>
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
      {mode === "view" && pendingForm && (
        <div className="card row" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }} data-testid="accel-pending-form">
          <span className="note">確認できなかった端点と理由：</span>
          {(["start", "end"] as const).map((w) => (
            <label key={w}>{w === "start" ? "始点" : "終点"}
              <select value={pendingForm[w]} onChange={(e) => setPendingForm({ ...pendingForm, [w]: e.target.value })} data-testid={`accel-pending-${w}`}>
                <option value="">確認できた</option>{PENDING_REASONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          ))}
          <button className="primary" disabled={save.isPending || (!pendingForm.start && !pendingForm.end)} data-testid="accel-pending-save"
            onClick={() => save.mutate({ action: "pending", comment: comment || null,
              pending: Object.fromEntries((["start", "end"] as const).filter((w) => pendingForm[w]).map((w) => [w, pendingForm[w]])) })}>
            判定保留として保存</button>
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
