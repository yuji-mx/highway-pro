import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, downloadBlob, errMsg, waitJob } from "../api/client";
import { ComparePanel, CompareTray } from "../components/compare/ComparePanel";
import { b64 } from "../components/common/queryText";
import { useToast } from "../state/stores";

const DATASETS = [["facilities", "IC/JCT マスター"], ["ramps", "ランプマスター"], ["sections", "区間マスター"], ["samples", "サンプルポイント"], ["section_distances", "区間距離（定義別）"]];
const FORMATS = [["csv", "CSV"], ["xlsx", "Excel"], ["geojson", "GeoJSON"], ["gpkg", "GeoPackage"], ["parquet", "Parquet"], ["sqlite", "SQLite"]];
const SIZES: Record<string, [number, number] | null> = { current: null, "1920x1080": [1920, 1080], "3840x2160": [3840, 2160], "1200x1200": [1200, 1200], custom: null };

function lastSearch(): any | null {
  const m = sessionStorage.getItem("roadmri.lastSearch");
  return m ? b64.dec(m) : null;
}

export default function CompareExport() {
  const toast = useToast((s) => s.push);
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<any>("/meta") });
  const [ds, setDs] = useState("ramps");
  const [fmt, setFmt] = useState("csv");
  const [scope, setScope] = useState("all");
  const [prog, setProg] = useState<string>("");
  const [img, setImg] = useState({ size: "1920x1080", w: 1920, h: 1080, scale: "", bg: "#000000", line: 1, format: "png", metric: "road_type", labels: true });
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => api.get<any[]>("/presets") });
  const job = async (path: string, body: unknown, name: string) => {
    const j: any = await api.post(path, body);
    const done = await waitJob(j.job_id, (p, m) => setProg(`${Math.round(p * 100)}% ${m ?? ""}`));
    const r = await fetch(done.download_url);
    downloadBlob(await r.blob(), name);
    setProg("完了");
  };
  const query = () => (scope === "search" ? lastSearch() : scope === "spec" ? presets.data?.find((p) => p.id === "spec_example")?.query : null);
  const exp = useMutation({ mutationFn: () => job("/export", { dataset: ds, format: fmt, query: query() }, `${ds}_${fmt}.zip`), onError: (e) => toast(errMsg(e), true) });
  const size = (): [number, number] => SIZES[img.size] ?? (img.size === "current" ? [window.innerWidth, window.innerHeight] : [img.w, img.h]);
  const single = useMutation({
    mutationFn: async () => {
      const [w, h] = size();
      const blob = await api.blob("/images/render", { width: w, height: h, format: img.format, background: img.bg, line_scale: img.line, metric: img.metric,
        labels: img.labels, scale_m_per_px: img.scale ? Number(img.scale) : null });
      downloadBlob(blob, `road_mri.${img.format}`);
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const batch = useMutation({
    mutationFn: () => {
      const [w, h] = size();
      return job("/images", { width: w, height: h, format: img.format, metric: img.metric, query: query() ?? presets.data?.find((p) => p.id === "spec_example")?.query, batch: true }, "images.zip");
    },
    onError: (e) => toast(errMsg(e), true),
  });
  const rep = useMutation({ mutationFn: () => job("/reports", { query: query() ?? presets.data?.find((p) => p.id === "spec_example")?.query, limit: 20 }, "report.zip"), onError: (e) => toast(errMsg(e), true) });
  const prov = meta.data?.run?.provenance ?? {};
  return (
    <div className="page">
      <h1>比較・出力</h1>
      <div className="card"><h3>IC/JCT 構造比較（条件検索の比較トレイと共有）</h3><CompareTray /><div style={{ marginTop: 10 }}><ComparePanel /></div></div>
      <div className="grid2" style={{ marginTop: 12 }}>
        <div className="card" data-testid="export-card"><h3>データ出力</h3>
          <div className="form">
            <label>データセット<select value={ds} onChange={(e) => setDs(e.target.value)} data-testid="export-dataset">{DATASETS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label>形式<select value={fmt} onChange={(e) => setFmt(e.target.value)} data-testid="export-format">{FORMATS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label>対象<select value={scope} onChange={(e) => setScope(e.target.value)}><option value="all">全件</option><option value="search">現在の検索結果</option><option value="spec">仕様書の例のクエリ</option></select></label>
          </div>
          <button className="primary" style={{ marginTop: 8 }} onClick={() => exp.mutate()} disabled={exp.isPending} data-testid="export-run">出力（ZIP、provenance.json 同梱）</button>
          <div className="note">{prog}</div>
        </div>
        <div className="card" data-testid="image-card"><h3>画像出力</h3>
          <div className="form">
            <label>サイズ<select value={img.size} onChange={(e) => setImg({ ...img, size: e.target.value })}>{Object.keys(SIZES).map((k) => <option key={k} value={k}>{k === "current" ? "現在表示" : k === "custom" ? "任意" : k}</option>)}</select></label>
            {img.size === "custom" && <><label>幅<input value={img.w} onChange={(e) => setImg({ ...img, w: Number(e.target.value) })} /></label><label>高さ<input value={img.h} onChange={(e) => setImg({ ...img, h: Number(e.target.value) })} /></label></>}
            <label>縮尺 [m/px]<input value={img.scale} placeholder="自動" onChange={(e) => setImg({ ...img, scale: e.target.value })} /></label>
            <label>背景色<input type="color" value={img.bg} onChange={(e) => setImg({ ...img, bg: e.target.value })} /></label>
            <label>線幅倍率<input type="number" step="0.1" value={img.line} onChange={(e) => setImg({ ...img, line: Number(e.target.value) })} /></label>
            <label>形式<select value={img.format} onChange={(e) => setImg({ ...img, format: e.target.value })} data-testid="image-format">{["png", "jpeg", "svg"].map((f) => <option key={f}>{f}</option>)}</select></label>
            <label>色分け<select value={img.metric} onChange={(e) => setImg({ ...img, metric: e.target.value })}>{["road_type", "grade", "elevation", "radius", "quality"].map((f) => <option key={f}>{f}</option>)}</select></label>
            <label className="row"><input type="checkbox" checked={img.labels} onChange={(e) => setImg({ ...img, labels: e.target.checked })} />ラベル・出典の描き込み</label>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" onClick={() => single.mutate()} disabled={single.isPending} data-testid="image-single">単発出力</button>
            <button onClick={() => batch.mutate()} disabled={batch.isPending}>一括（検索結果ごと、ZIP）</button>
            <button onClick={() => rep.mutate()} disabled={rep.isPending} data-testid="report-run">画像レポート生成</button>
          </div>
        </div>
      </div>
      <div className="card" style={{ marginTop: 12 }} data-testid="provenance"><h3>再現性情報</h3>
        <table className="dt"><tbody>
          <tr><td>run_id</td><td className="mono">{prov.run_id}</td></tr>
          <tr><td>コード版（git commit）</td><td className="mono">{prov.git_commit}</td></tr>
          <tr><td>設定ハッシュ</td><td className="mono">{prov.config_hash}</td></tr>
          <tr><td>分類ロジック</td><td>{prov.classifier_version}</td></tr>
          <tr><td>OSM</td><td>{(prov.osm_sources ?? []).map((s: any) => `${s.name}（${s.osm_timestamp ?? ""}、sha256 ${String(s.sha256).slice(0, 12)}…）`).join(" / ")}</td></tr>
          <tr><td>DEM</td><td>{(prov.dem_datasets ?? []).map((d: any) => `${d.name}（${d.resolution_m} m、${d.vertical_datum}、欠損率 ${(d.missing_ratio * 100).toFixed(1)}%）`).join(" / ")}</td></tr>
          <tr><td>補間・平滑化・勾配・曲率</td><td className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 11 }}>{JSON.stringify(prov.params ?? {}, null, 0).slice(0, 600)}</td></tr>
          <tr><td>手動修正</td><td>{prov.manual_edit_count} 件（最終 {prov.last_edit_id ?? "—"}）</td></tr>
          <tr><td>出典</td><td>{prov.attribution}</td></tr>
        </tbody></table>
      </div>
    </div>
  );
}
