import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HoverTip } from "../../src/components/map/hoverTip";
import { displayName } from "../../src/components/common/names";

describe("HoverTip（ツールチップの表示・非表示）", () => {
  let log: string[];
  let tip: HoverTip<number>;
  beforeEach(() => {
    vi.useFakeTimers();
    log = [];
    tip = new HoverTip<number>({ show_delay_ms: 500, hide_delay_ms: 200 }, (k, at) => log.push(`show ${k}@${at}`), () => log.push("hide"));
  });
  afterEach(() => vi.useRealTimers());

  it("止めてから 0.5 秒後に表示し、対象の上で動かしても消えない", () => {
    tip.move("r:1", 1);
    vi.advanceTimersByTime(499);
    expect(log).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(log).toEqual(["show r:1@1"]);
    for (let i = 0; i < 20; i++) { tip.move("r:1", 2 + i); vi.advanceTimersByTime(50); }
    expect(log).toEqual(["show r:1@1"]); // ちらつかない（消して出し直さない）
  });

  it("動いている間は表示を待ち、止まった位置に表示する", () => {
    for (let i = 0; i < 10; i++) { tip.move("r:1", i); vi.advanceTimersByTime(100); }
    expect(log).toEqual([]);
    vi.advanceTimersByTime(500);
    expect(log).toEqual(["show r:1@9"]);
  });

  it("対象から離れると 0.2 秒後に消える。すぐ戻れば消えない", () => {
    tip.move("f:A", 0);
    vi.advanceTimersByTime(500);
    tip.move(null, 1);
    vi.advanceTimersByTime(100);
    tip.move("f:A", 2); // 0.2 秒以内に戻る
    vi.advanceTimersByTime(1000);
    expect(log).toEqual(["show f:A@0"]);
    tip.move(null, 3);
    vi.advanceTimersByTime(200);
    expect(log).toEqual(["show f:A@0", "hide"]);
  });

  it("別の対象へ移ると前の表示は 0.2 秒後に消え、新しい対象は止めてから表示", () => {
    tip.move("r:1", 0);
    vi.advanceTimersByTime(500);
    tip.move("r:2", 1);
    vi.advanceTimersByTime(200);
    expect(log).toEqual(["show r:1@0", "hide"]);
    vi.advanceTimersByTime(300);
    expect(log).toEqual(["show r:1@0", "hide", "show r:2@1"]);
  });

  it("クリック・地図の移動では、予約も含めてすぐ消す", () => {
    tip.move("r:1", 0);
    vi.advanceTimersByTime(500);
    tip.cancel();
    expect(log).toEqual(["show r:1@0", "hide"]);
    tip.move("r:2", 1);
    tip.cancel(); // 表示前の予約も取り消す
    vi.advanceTimersByTime(1000);
    expect(log).toEqual(["show r:1@0", "hide"]);
  });
});

describe("displayName（施設名の表示用の整形）", () => {
  it("区切り文字「;」を「／」に置き換え、重複と空を除く", () => {
    expect(displayName("上河内SA;上河内スマートIC")).toBe("上河内SA ／ 上河内スマートIC");
    expect(displayName("宇都宮IC")).toBe("宇都宮IC");
    expect(displayName("A; A ;")).toBe("A");
    expect(displayName(null)).toBe("名称不明");
  });
});

import { placeLabels, shortLabel } from "../../src/components/map/labelLayout";

describe("placeLabels（施設名の重なりを避ける）", () => {
  const view = { w: 800, h: 600 };
  const hits = (ps: { key: string; dx: number; dy: number }[], items: { key: string; x: number; y: number; w: number; h: number }[]) => {
    const boxes = ps.map((p) => { const it = items.find((i) => i.key === p.key)!; return { l: it.x + p.dx, t: it.y + p.dy, r: it.x + p.dx + it.w, b: it.y + p.dy + it.h }; });
    let n = 0;
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      if (a.l < b.r && b.l < a.r && a.t < b.b && b.t < a.b) n++;
    }
    return n;
  };
  it("近い 2 施設の名称は、上と下などに分けて重ならないようにする", () => {
    const items = [{ key: "a", x: 400, y: 300, w: 160, h: 14 }, { key: "b", x: 420, y: 305, w: 160, h: 14 }];
    const ps = placeLabels(items, view, 3, 5);
    expect(ps.map((p) => p.key)).toEqual(["a", "b"]);
    expect(hits(ps, items)).toBe(0);
    expect(ps[0].dy).toBeLessThan(0); // 優先の高い名称は点の上
  });
  it("置ける場所がなければ出さない（優先度の低いものから）", () => {
    const items = Array.from({ length: 12 }, (_, i) => ({ key: String(i), x: 400 + i, y: 300, w: 200, h: 14 }));
    const ps = placeLabels(items, view, 3, 5);
    expect(ps.length).toBeLessThan(items.length);
    expect(ps[0].key).toBe("0");
    expect(hits(ps, items)).toBe(0);
  });
  it("画面の外にはみ出す位置には置かない", () => {
    const ps = placeLabels([{ key: "e", x: 795, y: 300, w: 120, h: 14 }], view, 3, 5);
    expect(ps).toEqual([{ key: "e", dx: -5 - 3 - 120, dy: -7 }]); // 右端なので左に置く
  });
});

describe("shortLabel（地図に出す短い名称）", () => {
  it("長い名称は末尾の括弧書きを省き、短い名称・日本語の名称はそのまま", () => {
    expect(shortLabel("I-94 Exit 177（State Street）", 16)).toBe("I-94 Exit 177");
    expect(shortLabel("I-94 Exit 175 (Ann Arbor-Saline Road)", 16)).toBe("I-94 Exit 175");
    expect(shortLabel("宇都宮IC", 16)).toBe("宇都宮IC");
    expect(shortLabel("鹿沼IC（栃木）", 16)).toBe("鹿沼IC（栃木）");
    expect(shortLabel("I-94 Exit 180 / US-23 Exit 35（I 94 BL East、Detroit）", 16)).toBe("I-94 Exit 180 / US-23 Exit 35");
  });
});
