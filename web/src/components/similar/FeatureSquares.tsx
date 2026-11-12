/** 特徴量ごとの近さ（7 マスの濃淡、ホバーで値）。 */
export default function FeatureSquares({ features, labels, squares, values }: {
  features: Record<string, number | null>;
  labels: Record<string, string>;
  squares: string[];
  values?: Record<string, unknown>;
}) {
  return (
    <span className="squares" data-testid="feature-squares">
      {squares.map((k) => {
        const v = features[k];
        const val = values?.[k];
        return (
          <i key={k} title={`${labels[k] ?? k}：近さ ${v == null ? "—" : v.toFixed(2)}${val != null ? `（値 ${typeof val === "number" ? val.toFixed(1) : val}）` : ""}`}
            style={{ opacity: v == null ? 0.1 : 0.15 + 0.85 * v, background: v == null ? "var(--dim)" : "var(--cyan)" }} />
        );
      })}
    </span>
  );
}
