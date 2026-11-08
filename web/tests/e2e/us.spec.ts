import { expect, test, type Page } from "@playwright/test";

/** 米国対応（日米横断の DB：宇都宮IC〜鹿沼IC ＋ Ann Arbor, MI の I-94 / US-23）。 */
test.use({ baseURL: "http://127.0.0.1:8767" });

async function api(page: Page, path: string, body?: unknown) {
  const r = body === undefined ? await page.request.get(`/api${path}`) : await page.request.post(`/api${path}`, { data: body });
  return r.json();
}

test.afterEach(async ({ page }) => {
  await page.evaluate(() => localStorage.removeItem("roadmri.units"));
});

test("US-1. 日米のデータが共存し、MRI ビューを米国に切り替えて I-94 Exit 177 をクリック", async ({ page }) => {
  const f = (await api(page, `/facilities?q=${encodeURIComponent("Exit 177")}`)).items[0];
  expect(f.facility_id.startsWith("US-F-")).toBe(true);
  await page.goto("/mri");
  await expect(page.getByTestId("country-chip")).toHaveText("日本・米国");
  await page.getByTestId("country-US").click();
  await page.waitForFunction(() => (window as any).__roadmriMaps?.["mri-map"]?.loaded());
  await page.waitForTimeout(1200);
  const pt = await page.evaluate(([x, y]) => {
    const m = (window as any).__roadmriMaps["mri-map"];
    const p = m.project([x, y]);
    const r = m.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, [f.lon, f.lat]);
  await page.mouse.click(pt.x, pt.y);
  await expect(page.getByTestId("info-panel")).toContainText("I-94 Exit 177");
  await expect(page.getByTestId("info-panel")).toContainText(/東行き|西行き/);
});

test("US-2. 単位をヤード・ポンド法に切り替えるとランプ詳細が ft・mph 表示になる", async ({ page }) => {
  const f = (await api(page, `/facilities?q=${encodeURIComponent("Exit 177")}`)).items[0];
  const d = await api(page, `/facilities/${f.facility_id}`);
  const ramp = d.ramps.find((r: any) => r.maxspeed_kmh) ?? d.ramps[0];
  await page.goto(`/ramp/${ramp.ramp_id}`);
  await expect(page.getByTestId("ramp-detail")).toContainText("ランプ長");
  await expect(page.locator(".kpi").first()).toContainText("m");
  await page.getByTestId("units-toggle").click();
  await expect(page.locator(".kpi").first()).toContainText("ft");
  const ft = Number((await page.locator(".kpi .v").first().innerText()).replace(/[^\d.]/g, ""));
  expect(ft).toBeCloseTo(ramp.length_m / 0.3048, -1);
  await page.reload();
  await expect(page.locator(".kpi").first()).toContainText("ft");
});

test("US-3. 条件検索の国フィルタ（米国だけ／日米横断）", async ({ page }) => {
  const all = (await api(page, "/search/facility", { where: {} })).count;
  const us = (await api(page, "/search/facility", { where: { all: [{ field: "country", op: "eq", value: "US" }] } })).count;
  expect(us).toBeGreaterThan(0);
  expect(all).toBeGreaterThan(us);
  await page.goto("/search?mode=facility");
  await page.getByTestId("f-country").selectOption("US");
  await expect(page.getByTestId("query-text")).toContainText("国 ＝ 米国");
  await page.getByTestId("run-search").click();
  await expect(page.getByTestId("result-count")).toHaveText(`${us} 件`);
  await expect(page.getByTestId("result-table")).toContainText("I-94 Exit");
  await expect(page.getByTestId("result-table")).not.toContainText("宇都宮IC");
});

test("US-4. 範囲統計比較の「日本 vs 米国」プリセット", async ({ page }) => {
  await page.goto("/stats");
  await page.getByTestId("stats-preset").selectOption({ label: "日本 vs 米国" });
  await expect(page.getByTestId("range-card-a")).toContainText("日本");
  await expect(page.getByTestId("range-card-b")).toContainText("米国");
  await expect(page.getByTestId("stats-table")).toBeVisible();
  await expect(page.getByTestId("insights")).toContainText("該当率");
});

test("US-5. 地域選択で北米を選ぶと米国の州が並ぶ", async ({ page }) => {
  await page.goto("/region");
  await page.getByRole("button", { name: "北米" }).click();
  await expect(page.getByTestId("na-note")).toContainText("USGS 3DEP");
  await expect(page.getByTestId("region-table")).toContainText("ミシガン州");
  await expect(page.getByTestId("region-table")).toContainText("Ann Arbor");
  await expect(page.getByTestId("region-table")).toContainText("処理済み");
});
