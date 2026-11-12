import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { t } from "../../i18n";
import { useToast } from "../../state/stores";
import { convert } from "../../state/units";

/** 数値の整形。単位（m、km、km/h、m/km）は表示単位の設定に従って換算する。 */
export const fmt = (v: unknown, d = 0, unit = ""): string => {
  if (v == null || (typeof v === "number" && !Number.isFinite(v))) return "—";
  if (typeof v === "number") {
    const [x, u] = unit ? convert(v, unit) : [v, unit];
    return `${x.toLocaleString("ja-JP", { minimumFractionDigits: d, maximumFractionDigits: d })}${u}`;
  }
  return String(v);
};

export function Kpi({ label, value, unit, d = 0 }: { label: string; value: unknown; unit?: string; d?: number }) {
  const [v, u] = typeof value === "number" && unit ? convert(value, unit) : [value, unit ?? ""];
  return (
    <div className="kpi">
      <div className="l">{label}</div>
      <div className="v">
        {fmt(v, d)}
        {u && value != null ? <small>{u}</small> : null}
      </div>
    </div>
  );
}

export function Badge({ kind, children, title }: { kind: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge b-${kind}`} title={title}>
      {children}
    </span>
  );
}

export const QualityBadge = ({ q, prefix = "" }: { q?: string | null; prefix?: string }) =>
  q ? <Badge kind={q}>{prefix}{t(`quality.${q}`, q)}</Badge> : <span className="dim">—</span>;

export const TypeBadge = ({ type }: { type?: string | null }) => (type ? <Badge kind={type}>{type}</Badge> : null);

export const ReviewBadge = ({ s }: { s?: string | null }) =>
  s ? <Badge kind={s === "needs_review" ? "review" : s === "reviewed" ? "ok" : "medium"}>{t(`review.${s}`, s)}</Badge> : null;

export function ConfBar({ v }: { v?: number | null }) {
  const p = Math.max(0, Math.min(1, v ?? 0));
  return (
    <span className="conf" title={`信頼度 ${fmt(v, 2)}`}>
      <span className="bar">
        <i style={{ width: `${p * 100}%`, background: p < 0.7 ? "var(--amber)" : "var(--cyan)" }} />
      </span>
      <span className="num">{fmt(v, 2)}</span>
    </span>
  );
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="skel" style={{ height: h }} aria-label={t("common.loading")} />;
}

export function Empty({ text }: { text?: string }) {
  return <div className="empty">{text ?? t("common.empty")}</div>;
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  const nodata = /データがありません|未取得/.test(msg);
  return (
    <div className="error" role="alert">
      {nodata ? t("common.nodata") : `${t("common.error")}：${msg}`}
      <div className="row" style={{ justifyContent: "center", marginTop: 8 }}>
        {retry && <button onClick={retry}>{t("common.retry")}</button>}
        {nodata && <Link to="/region">{t("common.toRegion")}</Link>}
      </div>
    </div>
  );
}

export function Toasts() {
  const items = useToast((s) => s.items);
  return (
    <div className="toasts" aria-live="polite">
      {items.map((x) => (
        <div key={x.id} className={`toast${x.err ? " err" : ""}`}>
          {x.text}
        </div>
      ))}
    </div>
  );
}

export function QueryText({ text, warnings = [] }: { text: string; warnings?: string[] }) {
  return (
    <div>
      <div className="qtext" data-testid="query-text">{text}</div>
      {warnings.map((w) => (
        <div key={w} className="warn">⚠ {w}</div>
      ))}
    </div>
  );
}

/** 難易度候補値の 5 要素積み上げバー（§4.15.2）。 */
export const DIFF_COLORS: Record<string, string> = { R: "#ff5b4f", G: "#ffb53d", L: "#38e1ff", K: "#a98bff", M: "#47e08f" };
export const DIFF_LABELS: Record<string, string> = { R: "曲率", G: "上り勾配", L: "ランプ長", K: "曲率変化", M: "合流条件" };
export function DifficultyBar({ comp, value }: { comp?: any; value?: number | null }) {
  const c = comp?.contributions ?? {};
  return (
    <span className="row" style={{ gap: 6 }} title={Object.entries(c).map(([k, v]) => `${DIFF_LABELS[k]} ${fmt(v as number, 1)}`).join(" / ")}>
      <span className="dbar">
        {Object.keys(DIFF_COLORS).map((k) => (
          <i key={k} style={{ width: `${Math.min(100, (c[k] ?? 0))}%`, background: DIFF_COLORS[k] }} />
        ))}
      </span>
      <span className="num">{fmt(value, 1)}</span>
    </span>
  );
}
