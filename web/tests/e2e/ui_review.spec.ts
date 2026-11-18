import { expect, test, type Page } from "@playwright/test";

/** 画面操作の見直し（ツールチップ・地域選択・名称の重なり・パネル配置・縮尺の案内・表示単位）の回帰テスト。
 * 日本のみ（8766）・日米（8767）・米国のみ（8768）のテスト用 DB を使う（保存を伴う操作はしない）。 */
const JP = "http://127.0.0.1:8766", MIX = "http://127.0.0.1:8767", US = "http://127.0.0.1:8768";

async function facility(page: Page, base: string, q: string) {
  return (await (await page.request.get(`${base}/api/facilities?q=${encodeURIComponent(q)}`)).json()).items[0];
}
async function mapReady(page: Page, id = "mri-map") {
  await page.waitForFunction((i) => (window as any).__roadmriMaps?.[i]?.loaded(), id);
}
async function toScreen(page: Page, lonlat: [number, number], id = "mri-map") {
  return page.evaluate(([c, i]) => {
    const m = (window as any).__roadmriMaps[i as string];
    const p = m.project(c);
    const r = m.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, [lonlat, id] as const);
}
const tip = (page: Page) => page.locator(".maplibregl-popup");

test("ツールチップ：止めて約 0.5 秒後に表示し、対象の上で動かしても消えない。離れる・クリック・地図の移動で消える", async ({ page }) => {
  const f = await facility(page, JP, "宇都宮");
  const d = await (await page.request.get(`${JP}/api/facilities/${f.facility_id}`)).json();
  const ramp = d.ramps.find((r: any) => r.length_m > 300);
  const g = await (await page.request.get(`${JP}/api/ramps/${ramp.ramp_id}`)).json();
  const mid = g.geometry.coordinates[Math.floor(g.geometry.coordinates.length / 2)];
  await page.goto(`${JP}/mri`);
  await mapReady(page);
  await page.evaluate((c) => (window as any).__roadmriMaps["mri-map"].jumpTo({ center: c, zoom: 16 }), mid);
  await page.waitForTimeout(2000);
  const pt = await toScreen(page, mid);
  await page.mouse.move(pt.x, pt.y);
  await page.waitForTimeout(200);
  await expect(tip(page)).toHaveCount(0); // すぐには出さない
  await expect(tip(page)).toContainText(ramp.ramp_id, { timeout: 2000 });
  await expect(tip(page)).toContainText("m");
  for (let i = 0; i < 10; i++) {
    await page.mouse.move(pt.x + (i % 2), pt.y);
    await page.waitForTimeout(60);
    await expect(tip(page)).toHaveCount(1); // ちらつかない
  }
  await page.mouse.move(pt.x + 250, pt.y + 250);
  await expect(tip(page)).toHaveCount(0, { timeout: 1000 });
  await page.mouse.move(pt.x, pt.y);
  await expect(tip(page)).toHaveCount(1, { timeout: 2000 });
  await page.mouse.click(pt.x, pt.y); // 情報パネルを開く → 閉じる
  await expect(tip(page)).toHaveCount(0);
  await expect(page.getByTestId("info-panel")).toContainText(ramp.ramp_id);
  // 施設の記号の上では施設を優先して、名称・種別を表示
  await page.evaluate((c) => (window as any).__roadmriMaps["mri-map"].jumpTo({ center: c, zoom: 15 }), [f.lon, f.lat]);
  await page.waitForTimeout(1500);
  const fp2 = await toScreen(page, [f.lon, f.lat]);
  await page.mouse.move(fp2.x, fp2.y);
  await expect(tip(page)).toContainText("宇都宮IC", { timeout: 2000 });
  await page.evaluate(() => (window as any).__roadmriMaps["mri-map"].panBy([30, 0], { duration: 400 }));
  await expect(tip(page)).toHaveCount(0); // 地図の移動中は消す
});

test("地域選択：米国のみは北米・米国の範囲、日米は日本、地域ごとに入力を保持、別の地域の座標は適用させない", async ({ page }) => {
  await page.goto(`${US}/region`);
  await expect(page.locator("[data-testid=area-NA].primary")).toBeVisible();
  await expect(page.getByTestId("bbox-input")).toHaveValue(/^-83\./);
  await page.getByTestId("area-JP").click();
  await expect(page.getByTestId("bbox-input")).toHaveValue(""); // データのない地域は空欄（州全域などを誤って適用しない）
  await expect(page.getByTestId("apply-range")).toBeDisabled();
  await page.getByTestId("bbox-input").fill("-83.77,42.215,-83.645,42.245");
  await expect(page.getByTestId("range-issue")).toContainText("「北米」の座標のようです");
  await expect(page.getByTestId("apply-range")).toBeDisabled();
  await page.getByTestId("area-NA").click();
  await expect(page.getByTestId("bbox-input")).toHaveValue(/^-83\./);
  await expect(page.getByTestId("apply-range")).toBeEnabled();
  await page.getByTestId("area-JP").click();
  await expect(page.getByTestId("bbox-input")).toHaveValue("-83.77,42.215,-83.645,42.245"); // 地域ごとに入力を保持
  await page.goto(`${MIX}/region`);
  await expect(page.locator("[data-testid=area-JP].primary")).toBeVisible();
  await expect(page.getByTestId("data-areas")).toContainText("日本・北米");
  await page.getByTestId("area-NA").click();
  await expect(page.getByTestId("bbox-input")).toHaveValue(/^-83\./); // 北米に切り替えると米国のデータの範囲
});

test("米国の地図：施設名が重ならず、長い名称は短くして正式名称はホバーで確認できる", async ({ page }) => {
  const f = await facility(page, US, "Exit 177");
  await page.goto(`${US}/mri`);
  await mapReady(page);
  for (const z of [11, 12, 13]) {
    await page.evaluate(([lon, lat, zz]) => (window as any).__roadmriMaps["mri-map"].jumpTo({ center: [lon, lat], zoom: zz }), [f.lon + 0.03, f.lat, z]);
    await page.waitForTimeout(1200);
    const r = await page.evaluate(() => {
      const bs = [...document.querySelectorAll(".maplabel")].map((e) => e.getBoundingClientRect());
      let ov = 0;
      for (let i = 0; i < bs.length; i++) for (let j = i + 1; j < bs.length; j++) {
        const a = bs[i], b = bs[j];
        if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) ov++;
      }
      return { n: bs.length, ov };
    });
    expect(r.n).toBeGreaterThan(0);
    expect(r.ov).toBe(0);
  }
  await expect(page.locator(".maplabel", { hasText: "I-94 Exit 177" }).first()).not.toContainText("State Street");
  await expect(page.locator(".maplabel", { hasText: "I-94 Exit 177" }).first()).toHaveAttribute("title", /State Street/);
});

for (const [w, h] of [[1440, 900], [1280, 720], [800, 700]]) {
  test(`情報パネルと凡例が重ならず、長い名称が切れない（${w}×${h}）`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    const f = await facility(page, US, "Exit 180");
    await page.goto(`${US}/mri?kind=facility&sel=${f.facility_id}`);
    await expect(page.getByTestId("info-panel")).toContainText("I-94 Exit 181");
    const r = await page.evaluate(() => {
      const g = (s: string) => document.querySelector(s)!.getBoundingClientRect();
      const P = g("[data-testid=info-float]"), L = g("[data-testid=legend]"), M = g("[data-testid=mri-map]");
      const ov = Math.max(0, Math.min(P.right, L.right) - Math.max(P.left, L.left)) * Math.max(0, Math.min(P.bottom, L.bottom) - Math.max(P.top, L.top));
      const out = [...document.querySelectorAll("[data-testid=info-float] td")].filter((e) => e.getBoundingClientRect().right > P.right + 1).length;
      return { ov, out, mapW: M.width, mapH: M.height };
    });
    expect(r.ov).toBe(0);
    expect(r.out).toBe(0);
    expect(r.mapW).toBeGreaterThan(300); // 狭い画面でも地図が表示される
    expect(r.mapH).toBeGreaterThan(300);
  });
}

test("縮尺の案内：一般道・橋梁は最小ズーム、名称は IC と JCT の違い、データのない種類は「この範囲になし」", async ({ page }) => {
  const f = await facility(page, JP, "宇都宮");
  await page.goto(`${JP}/mri`);
  await mapReady(page);
  await page.evaluate(([lon, lat]) => (window as any).__roadmriMaps["mri-map"].jumpTo({ center: [lon, lat], zoom: 9.5 }), [f.lon, f.lat]);
  await expect(page.getByTestId("layer-hint-general")).toHaveText("ズーム10以上");
  await expect(page.getByTestId("layer-hint-labels")).toContainText("IC・SA/PA名はズーム11以上");
  await expect(page.getByTestId("layer-hint-jct")).toHaveText("この範囲になし"); // このデータには JCT がない（縮尺の問題ではない）
  await page.evaluate(() => (window as any).__roadmriMaps["mri-map"].zoomTo(12, { duration: 0 }));
  await expect(page.getByTestId("layer-hint-general")).toHaveCount(0);
  await expect(page.getByTestId("layer-hint-labels")).toHaveCount(0);
});

test("表示単位：米国のみは ft・mph、日米・日本は m・km/h。利用者が選んだ単位はデータの既定で上書きしない", async ({ page }) => {
  await page.goto(`${US}/mri`);
  await expect(page.getByTestId("units-toggle")).toHaveText("ft・mph");
  await page.goto(`${MIX}/mri`);
  await expect(page.getByTestId("units-toggle")).toHaveText("m・km/h");
  await page.goto(`${US}/mri`);
  await page.evaluate(() => localStorage.removeItem("roadmri.units"));
  await page.reload();
  await expect(page.getByTestId("units-toggle")).toHaveText("ft・mph");
  await page.getByTestId("units-toggle").click(); // 明示的に m を選ぶ
  await page.reload();
  await expect(page.getByTestId("units-toggle")).toHaveText("m・km/h");
  await page.evaluate(() => localStorage.removeItem("roadmri.units"));
});
