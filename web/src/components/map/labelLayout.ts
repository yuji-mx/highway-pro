/** 施設名の配置（重なりを避ける）。地図の施設名は HTML 要素なので、画面上の矩形で衝突を判定する。
 *
 * 優先度の高い順に、点の上・下・右・左の順で置ける位置を探し、どこにも置けなければ出さない
 * （施設の記号は残るので、ホバー・クリックで名称を確認できる）。
 */
export interface LabelItem {
  key: string;
  x: number; // 施設の画面座標
  y: number;
  w: number; // 名称の幅・高さ（px）
  h: number;
}

export interface Placed {
  key: string;
  dx: number; // 施設の点から名称の左上までのずれ（px）
  dy: number;
}

interface Box { l: number; t: number; r: number; b: number }

const hit = (a: Box, b: Box) => a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b;

/** items は優先度の高い順。gap は名称どうし・記号との間隔、mark は施設の記号の半径。 */
export function placeLabels(items: LabelItem[], view: { w: number; h: number }, gap: number, mark: number): Placed[] {
  const boxes: Box[] = items.map((it) => ({ l: it.x - mark, t: it.y - mark, r: it.x + mark, b: it.y + mark })); // 記号の上には置かない
  const out: Placed[] = [];
  for (const it of items) {
    const cands: [number, number][] = [
      [-it.w / 2, -mark - gap - it.h], // 上
      [-it.w / 2, mark + gap], // 下
      [mark + gap, -it.h / 2], // 右
      [-mark - gap - it.w, -it.h / 2], // 左
    ];
    for (const [dx, dy] of cands) {
      const box = { l: it.x + dx - gap, t: it.y + dy - gap, r: it.x + dx + it.w + gap, b: it.y + dy + it.h + gap };
      const inside = box.l >= 0 && box.t >= 0 && box.r <= view.w && box.b <= view.h;
      if (inside && !boxes.some((o) => hit(o, box))) {
        boxes.push(box);
        out.push({ key: it.key, dx, dy });
        break;
      }
    }
  }
  return out;
}

/** 地図に出す短い名称：長い名称は末尾の括弧書き（行先など）を省く。正式名称はホバー・情報パネルで確認する。 */
export function shortLabel(name: string, maxChars: number): string {
  if (name.length <= maxChars) return name;
  const m = name.match(/^(.*?)\s*[（(][^（）()]*[）)]\s*$/);
  return m && m[1] ? m[1] : name;
}
