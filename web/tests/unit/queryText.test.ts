import { describe, expect, it } from "vitest";
import { b64, toText, usesInstantGrade } from "../../src/components/common/queryText";

describe("QueryDSL → 日本語文", () => {
  it("仕様書の例", () => {
    const q = {
      target: "ramp" as const,
      where: { all: [
        { field: "flow", op: "eq", value: "entry" },
        { field: "length_m", op: "gte", value: 500 },
        { field: "min_radius_m", op: "lte", value: 100 },
        { field: "grade_up_max_w100", op: "gte", value: 5 },
        { field: "dem_quality", op: "gte", value: "medium" },
      ] },
      sort: [{ field: "difficulty", dir: "desc" as const }],
      limit: 500,
    };
    expect(toText(q)).toBe(
      "ランプ：区分 ＝ 流入 かつ 長さ ≥ 500 m かつ 最小R ≤ 100 m かつ 100m上り ≥ 5% かつ DEM品質 中以上、難易度候補値の降順、上限 500 件",
    );
  });
  it("AND/OR のネストと between", () => {
    const q = { target: "ramp" as const, where: {
      all: [{ field: "flow", op: "eq", value: "entry" }],
      any: [{ field: "accel_lane_len_m", op: "lte", value: 200 }, { field: "length_m", op: "between", value: [100, 450] }],
    } };
    expect(toText(q)).toBe("ランプ：区分 ＝ 流入 かつ （加速車線長 ≤ 200 m または 長さ 100 m〜450 m）");
  });
  it("条件なし・対象名", () => {
    expect(toText({ target: "section", where: {} })).toBe("区間：条件なし");
    expect(toText({ target: "facility", where: { all: [{ field: "has_loop", op: "eq", value: true }] } })).toBe("IC/JCT：ループあり ＝ はい");
  });
  it("最大瞬間勾配の警告", () => {
    expect(usesInstantGrade({ target: "ramp", where: { any: [{ all: [{ field: "grade_inst_max", op: "gte", value: 8 }] }] } })).toBe(true);
    expect(usesInstantGrade({ target: "ramp", where: { all: [{ field: "grade_up_max_w100", op: "gte", value: 8 }] } })).toBe(false);
  });
  it("URL 用 base64 は日本語を往復できる", () => {
    const o = { a: "宇都宮IC", n: [1, 2] };
    expect(b64.dec(b64.enc(o))).toEqual(o);
    expect(b64.dec("!!!")).toBeNull();
  });
});
