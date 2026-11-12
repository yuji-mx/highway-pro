import { expect, test } from "@playwright/test";

/** 視覚回帰：主要 9 画面（しきい値 0.2 %）。初回は --update-snapshots で基準画像を作る。 */
test.describe("視覚回帰", () => {
  test("主要 9 画面", async ({ page, request }) => {
    const f = (await (await request.get("/api/facilities?q=" + encodeURIComponent("鹿沼"))).json()).items[0];
    const d = await (await request.get(`/api/facilities/${f.facility_id}`)).json();
    const pages: [string, string][] = [
      ["01-region", "/region"], ["02-mri", "/mri"], ["03-list", "/facilities"], ["04-facility", `/facility/${f.facility_id}`],
      ["05-ramp", `/ramp/${d.ramps[0].ramp_id}`], ["06-sections", "/sections"], ["07-search", "/search?mode=ramp"],
      ["08-stats", "/stats"], ["09-compare", "/compare"],
    ];
    for (const [name, path] of pages) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(1500);
      await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: false, mask: [page.getByTestId("run-chip")] });
    }
  });
});
