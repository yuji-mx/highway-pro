import { expect, test, type Page } from "@playwright/test";

/** 地図上の経緯度をクリック（MapLibre の project でピクセル座標に変換）。 */
async function clickLonLat(page: Page, mapId: string, lon: number, lat: number) {
  await page.waitForFunction((id) => (window as any).__roadmriMaps?.[id]?.loaded(), mapId);
  await page.waitForTimeout(800);
  const pt = await page.evaluate(([id, x, y]) => {
    const m = (window as any).__roadmriMaps[id as string];
    const p = m.project([x, y]);
    const r = m.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, [mapId, lon, lat] as const);
  await page.mouse.click(pt.x, pt.y);
}

async function api(page: Page, path: string, body?: unknown) {
  const r = body === undefined ? await page.request.get(`/api${path}`) : await page.request.post(`/api${path}`, { data: body });
  return r.json();
}

async function facility(page: Page, q: string) {
  return (await api(page, `/facilities?q=${encodeURIComponent(q)}`)).items[0];
}

const external: string[] = [];
test.beforeEach(async ({ page }) => {
  page.on("request", (r) => {
    const u = r.url();
    if (!u.startsWith("http://127.0.0.1") && !u.startsWith("data:") && !u.startsWith("blob:")) external.push(u);
  });
});

test("1. MRI ビューで宇都宮IC をクリック → 情報パネル → 詳細を開く", async ({ page }) => {
  const f = await facility(page, "宇都宮");
  await page.goto("/mri");
  await clickLonLat(page, "mri-map", f.lon, f.lat);
  await expect(page.getByTestId("info-panel")).toContainText("宇都宮IC");
  await page.getByTestId("open-detail").click();
  await expect(page).toHaveURL(new RegExp(`/facility/${f.facility_id}`));
  await expect(page.getByTestId("facility-detail")).toContainText("複合型");
});

test("2. 色分けを勾配に変更 → 凡例が切り替わる", async ({ page }) => {
  await page.goto("/mri");
  await expect(page.getByTestId("legend")).toContainText("本線");
  await page.getByTestId("metric-select").selectOption("grade");
  await expect(page.getByTestId("legend")).toContainText("+8%");
  await expect(page.getByTestId("legend")).toContainText("勾配");
});

test("3. ランプ詳細のプロファイルにホバー → 地図上にカーソルマーカー", async ({ page }) => {
  const f = await facility(page, "鹿沼");
  const d = await api(page, `/facilities/${f.facility_id}`);
  await page.goto(`/ramp/${d.ramps[0].ramp_id}`);
  const chart = page.getByTestId("profile-chart");
  await expect(chart).toBeVisible();
  await page.waitForTimeout(800);
  const box = (await chart.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5);
  await expect(page.getByTestId("map-cursor")).toBeVisible();
});

test("4. 条件検索で「仕様書の例」→ 件数が API と一致 → MRI で強調表示", async ({ page }) => {
  const presets = await api(page, "/presets");
  const spec = presets.find((p: any) => p.id === "spec_example");
  const expected = (await api(page, "/search/ramp", spec.query)).count;
  await page.goto("/search?mode=ramp");
  await page.getByTestId("preset-spec_example").click();
  await expect(page.getByTestId("result-count")).toHaveText(`${expected} 件`);
  await expect(page.getByTestId("query-text")).toContainText("区分 ＝ 流入 かつ 長さ ≥ 500 m");
  // 小範囲の fixture では仕様書の例が 0 件のため、強調表示は旋回・駆動力評価で確認する
  const corner = presets.find((p: any) => p.id === "cornering");
  const n = (await api(page, "/search/ramp", corner.query)).count;
  await page.getByTestId("preset-cornering").click();
  await expect(page.getByTestId("result-count")).toHaveText(`${n} 件`);
  await page.getByTestId("highlight-mri").click();
  await expect(page).toHaveURL(/\/mri/);
  await expect(page.getByText(`検索結果を強調（${n}）`)).toBeVisible();
});

test("5. IC/JCT 検索で 2 施設を＋比較 → 比較パネル、差のある行を強調", async ({ page }) => {
  await page.goto("/search?mode=facility");
  await page.getByTestId("run-search").click();
  const rows = page.getByTestId("result-table").getByTestId("add-compare");
  await expect(rows.first()).toBeVisible();
  await rows.nth(0).click();
  await rows.nth(1).click();
  await page.getByTestId("tray-open").click();
  await expect(page.getByTestId("compare-panel")).toBeVisible();
  await expect(page.getByTestId("compare-table").locator('tr[data-differs="1"]').first()).toBeVisible();
  await expect(page.getByTestId("compare-table").locator("tr.diff").first()).toBeVisible();
});

test("6. IC/JCT 詳細の類似一覧から「比較」→ 比較パネルが A/B 設定済みで開く", async ({ page }) => {
  const f = await facility(page, "鹿沼");
  await page.goto(`/facility/${f.facility_id}`);
  await page.getByTestId("similar-compare").first().click();
  await expect(page).toHaveURL(/\/search\?mode=facility/);
  await expect(page.getByTestId("tray-a")).toContainText("鹿沼IC");
  await expect(page.getByTestId("tray-b")).not.toContainText("未登録");
  await expect(page.getByTestId("compare-panel")).toBeVisible();
});

test("7. ランプ詳細の「類似IC/JCTを検索」→ 類似パネルに基準施設", async ({ page }) => {
  const f = await facility(page, "鹿沼");
  const d = await api(page, `/facilities/${f.facility_id}`);
  await page.goto(`/ramp/${d.ramps[0].ramp_id}`);
  await page.getByTestId("btn-similar-facility").click();
  await expect(page.getByTestId("similar-panel")).toContainText("鹿沼IC");
  await expect(page.getByTestId("similar-base")).toHaveText(f.facility_id);
});

test("8. 検索結果を「範囲Aへ」→ 範囲統計比較で A が個別選択", async ({ page }) => {
  await page.goto("/search?mode=facility");
  await page.getByTestId("run-search").click();
  await expect(page.getByTestId("result-count")).toContainText("件");
  await page.getByTestId("to-range-a").click();
  await page.getByTestId("nav-08").click();
  await expect(page.getByTestId("range-card-a")).toContainText("個別選択");
  await expect(page.getByTestId("range-card-a")).toContainText("検索結果");
});

test("9. 範囲統計比較でプリセット切替 → 表・地図・分布図が更新 → CSV ダウンロード", async ({ page }) => {
  await page.goto("/stats");
  await expect(page.getByTestId("stats-table")).toBeVisible();
  const before = await page.getByTestId("range-card-a").innerText();
  const opts = await page.getByTestId("stats-preset").locator("option").allTextContents();
  await page.getByTestId("stats-preset").selectOption({ label: opts[opts.length - 2] });
  await expect(page.getByTestId("range-card-a")).not.toHaveText(before);
  await expect(page.getByTestId("stats-table")).toBeVisible();
  await expect(page.getByTestId("hist-chart")).toBeVisible();
  await expect(page.getByTestId("stats-map")).toBeVisible();
  const dl = page.waitForEvent("download");
  await page.getByTestId("stats-csv").click();
  expect((await dl).suggestedFilename()).toBe("stats_compare.csv");
});

test("10. 手動修正の保存 → 履歴に表示 → 再読込後も保持", async ({ page }) => {
  const f = await facility(page, "大谷");
  await page.goto(`/facility/${f.facility_id}`);
  await page.getByTestId("edit-review").selectOption("reviewed");
  await page.getByTestId("edit-comment").fill("E2E 確認");
  await page.getByTestId("edit-save").click();
  await expect(page.getByTestId("edit-history")).toContainText("E2E 確認");
  await page.reload();
  await expect(page.getByTestId("edit-history")).toContainText("review_status");
  await expect(page.getByTestId("facility-detail")).toContainText("確認済み");
  // 後続のテスト（視覚回帰）に影響しないよう、修正を取り消す（取り消しも履歴に残る）
  const edits = (await api(page, "/edits")).filter((e: any) => e.target_id === f.facility_id && e.field !== "__revert__");
  for (const e of edits.reverse()) await api(page, `/edits/${e.edit_id}/revert`, {});
  await page.reload();
  await expect(page.getByTestId("edit-history")).toContainText("取り消し");
});

test("11. 画像出力（PNG、SVG）と CSV、GeoPackage のダウンロード", async ({ page }) => {
  await page.goto("/compare");
  for (const fmt of ["png", "svg"]) {
    await page.getByTestId("image-format").selectOption(fmt);
    const dl = page.waitForEvent("download");
    await page.getByTestId("image-single").click();
    expect((await dl).suggestedFilename()).toBe(`road_mri.${fmt}`);
  }
  for (const [fmt, name] of [["csv", "ramps_csv.zip"], ["gpkg", "ramps_gpkg.zip"]]) {
    await page.getByTestId("export-format").selectOption(fmt);
    const dl = page.waitForEvent("download", { timeout: 60_000 });
    await page.getByTestId("export-run").click();
    expect((await dl).suggestedFilename()).toBe(name);
  }
});

test("12. ブラウザの戻る・進むで画面と選択状態が戻る", async ({ page }) => {
  const f = await facility(page, "宇都宮");
  await page.goto("/facilities");
  await page.getByTestId("filter-type").selectOption("IC");
  await expect(page).toHaveURL(/type=IC/);
  await page.goto(`/mri?kind=facility&sel=${f.facility_id}`);
  await expect(page.getByTestId("info-panel")).toContainText("宇都宮IC");
  await page.goto("/search?mode=facility");
  await page.goBack();
  await expect(page.getByTestId("info-panel")).toContainText("宇都宮IC");
  await page.goBack();
  await expect(page).toHaveURL(/type=IC/);
  await expect(page.getByTestId("filter-type")).toHaveValue("IC");
  await page.goForward();
  await expect(page.getByTestId("info-panel")).toBeVisible();
});

test("13. IC/JCT 一覧：検索語でサーバー側を絞り込み、見出しクリックでサーバー側の並べ替え、CSV は同じ条件の全件", async ({ page }) => {
  await page.goto("/facilities");
  const table = page.getByTestId("facility-table");
  await expect(table.locator("tbody tr").first()).toBeVisible();
  const all = await table.locator("tbody tr").count();
  await page.getByTestId("facility-search").fill("宇都宮");
  await expect(page).toHaveURL(/q=%E5%AE%87%E9%83%BD%E5%AE%AE/);
  await expect(table.locator("tbody tr")).toHaveCount(1);
  await expect(table).toContainText("宇都宮IC");
  await page.getByTestId("facility-search").fill("");
  await expect(table.locator("tbody tr")).toHaveCount(all);
  await table.locator("th", { hasText: "名称" }).click();
  await expect(page).toHaveURL(/sort=name&order=desc|order=desc.*sort=name/);
  const names = await table.locator("tbody tr td:first-child").allTextContents();
  expect(names).toEqual([...names].sort().reverse());
  await expect(page.getByTestId("facility-pager")).toContainText("1 / 1");
  const href = await page.getByTestId("facility-csv").getAttribute("href");
  const csv = await page.request.get(href!);
  const lines = (await csv.text()).replace(/^\uFEFF/, "").trim().split("\n");
  expect(lines[0]).toContain("施設ID,名称,種別");
  expect(lines.length - 1).toBe(all);
});

test("オフライン：外部（CDN 等）へのリクエストが 0 件", async ({ page }) => {
  for (const p of ["/region", "/mri", "/facilities", "/sections", "/search", "/stats", "/compare"]) {
    await page.goto(p);
    await page.waitForLoadState("networkidle");
  }
  expect(external).toEqual([]);
});
