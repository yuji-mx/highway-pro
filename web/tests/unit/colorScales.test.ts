import { describe, expect, it } from "vitest";
import { GRADE_STOPS, elevColor, gradeColor, interp, legendFor, radiusColor } from "../../src/components/map/colorScales";

describe("カラースケール", () => {
  it("勾配：-8% 青、0% 白、+8% 赤（モックアップの stop 値）", () => {
    expect(gradeColor(-8)).toBe("rgb(60,120,255)");
    expect(gradeColor(0)).toBe("rgb(255,255,255)");
    expect(gradeColor(8)).toBe("rgb(255,60,60)");
    expect(gradeColor(20)).toBe("rgb(255,60,60)");
    expect(gradeColor(null)).toBe("#7d8b9a");
  });
  it("線形補間", () => {
    expect(interp([[0, [0, 0, 0]], [10, [100, 200, 50]]], 5)).toEqual([50, 100, 25]);
    expect(interp(GRADE_STOPS, Number.NaN)).toEqual([125, 139, 154]);
  });
  it("曲率半径（対数）：R30 赤 → R3km 白", () => {
    expect(radiusColor(30)).toBe("rgb(255,60,60)");
    expect(radiusColor(3000)).toBe("rgb(255,255,255)");
    expect(radiusColor(10)).toBe("rgb(255,60,60)");
  });
  it("標高：範囲の下限が紫、上限が白", () => {
    expect(elevColor(30, 30, 550)).toBe("rgb(120,60,200)");
    expect(elevColor(550, 30, 550)).toBe("rgb(255,255,255)");
  });
  it("凡例は数値・名称を併記する", () => {
    const g = legendFor("grade");
    expect(g.map((x) => x.label)).toEqual(["-8%", "-4%", "0%", "+3%", "+5.5%", "+8%"]);
    expect(legendFor("elevation", [100, 300])[3].label).toBe("300 m");
    expect(legendFor("road_type").length).toBeGreaterThan(3);
  });
});
