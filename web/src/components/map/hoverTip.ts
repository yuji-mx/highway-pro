/** 地図のホバー表示（ツールチップ）の表示・非表示の制御と、カーソル下の対象の選び方。
 *
 * 以前は地図のコンポーネントが再描画されるたび（道路MRIビューではマウス移動ごとにカーソル座標を親へ渡して
 * 再描画していた）にイベントを付け直し、そのたびにツールチップを消していたため、一瞬だけ表示されて消えていた。
 * ここではタイマーで「止めてから表示・離れてから消す」を行い、同じ対象の上にいる間は表示を保つ。
 */
import type { Map as MlMap, MapGeoJSONFeature, PointLike } from "maplibre-gl";

export interface TipTiming {
  show_delay_ms: number; // 対象の上でマウスを止めてから表示するまで
  hide_delay_ms: number; // 対象から離れてから消すまで
}

export class HoverTip<T> {
  shown: string | null = null;
  private pending: string | null = null;
  private showTimer: ReturnType<typeof setTimeout> | null = null;
  private hideTimer: ReturnType<typeof setTimeout> | null = null;
  private latest: T | null = null;

  constructor(
    private timing: TipTiming,
    private onShow: (key: string, at: T) => void,
    private onHide: () => void,
  ) {}

  /** マウスの移動ごとに呼ぶ。key はカーソル下の対象（なければ null）、at は表示位置などの情報。 */
  move(key: string | null, at: T): void {
    this.latest = at;
    if (key !== null && key === this.shown) {
      // 表示中の対象の上にいる：消す予約を取り消して表示を保つ
      this.clearHide();
      this.clearShow();
      return;
    }
    if (this.shown !== null && this.hideTimer === null) {
      // 表示中の対象から離れた：少し待ってから消す
      this.hideTimer = setTimeout(() => {
        this.hideTimer = null;
        this.shown = null;
        this.onHide();
      }, this.timing.hide_delay_ms);
    }
    if (key === null) {
      this.clearShow();
      return;
    }
    // 新しい対象：マウスが止まってから表示する（動いている間は待ち直す）
    this.clearShow();
    this.pending = key;
    this.showTimer = setTimeout(() => {
      this.showTimer = null;
      this.clearHide();
      this.shown = this.pending;
      this.pending = null;
      if (this.shown !== null && this.latest !== null) this.onShow(this.shown, this.latest);
    }, this.timing.show_delay_ms);
  }

  /** クリック・地図の移動・ズーム・地図の外へ出たときなど：すぐに消し、予約も取り消す。 */
  cancel(): void {
    this.clearShow();
    this.clearHide();
    if (this.shown !== null) {
      this.shown = null;
      this.onHide();
    }
  }

  setTiming(t: TipTiming): void {
    this.timing = t;
  }

  private clearShow(): void {
    if (this.showTimer !== null) clearTimeout(this.showTimer);
    this.showTimer = null;
    this.pending = null;
  }

  private clearHide(): void {
    if (this.hideTimer !== null) clearTimeout(this.hideTimer);
    this.hideTimer = null;
  }
}

export interface PickOpts {
  hit_px: number; // ランプ（線）を拾う範囲
  facility_hit_px: number; // 施設（点）を拾う範囲
}

/** 点と線分の距離（画面のピクセル）。 */
function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax, dy = by - ay;
  const L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / L)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** 画面上で、カーソルから地物までの最短距離（ピクセル）。 */
export function screenDistance(m: MlMap, f: MapGeoJSONFeature, x: number, y: number): number {
  const g = f.geometry as any;
  const pr = (c: [number, number]) => m.project(c);
  if (g.type === "Point") {
    const p = pr(g.coordinates);
    return Math.hypot(p.x - x, p.y - y);
  }
  const lines: [number, number][][] = g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? g.coordinates : [];
  let best = Infinity;
  for (const line of lines) {
    let prev = pr(line[0]);
    for (let i = 1; i < line.length; i++) {
      const cur = pr(line[i]);
      best = Math.min(best, segDist(x, y, prev.x, prev.y, cur.x, cur.y));
      prev = cur;
    }
  }
  return best;
}

/** カーソル下の対象。施設はカーソルが施設の記号の上（facility_hit_px 以内）にあれば優先し、
 * それ以外はカーソルに最も近いランプ（hit_px 以内）。IC 付近ではランプが密集するため、
 * 施設を選びたいときは記号に重ね、ランプを選びたいときは記号から少し離せば選び分けられる。 */
export function pickTarget(m: MlMap, x: number, y: number, o: PickOpts): MapGeoJSONFeature | null {
  const r = Math.max(o.hit_px, o.facility_hit_px);
  const box: [PointLike, PointLike] = [[x - r, y - r], [x + r, y + r]];
  const layers = ["facilities-2", "ramps-all-2", "ramps-all-0"].filter((id) => m.getLayer(id));
  const fs = m.queryRenderedFeatures(box, { layers });
  let fac: MapGeoJSONFeature | null = null, facD = Infinity, ramp: MapGeoJSONFeature | null = null, rampD = Infinity;
  for (const f of fs) {
    const d = screenDistance(m, f, x, y);
    if (f.source === "facilities" && d < facD) [fac, facD] = [f, d];
    else if (f.source === "ramps" && d < rampD) [ramp, rampD] = [f, d];
  }
  if (fac && facD <= o.facility_hit_px) return fac;
  if (ramp && rampD <= o.hit_px) return ramp;
  return null;
}

/** 対象の識別子（同じ対象の上にいるかの判定に使う）。 */
export function targetKey(f: MapGeoJSONFeature | null): string | null {
  if (!f) return null;
  const pr = f.properties as any;
  return f.source === "facilities" ? `f:${pr.facility_id}` : `r:${pr.ramp_id}`;
}
