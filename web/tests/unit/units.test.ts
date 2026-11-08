import { afterEach, describe, expect, it } from "vitest";
import { fmt } from "../../src/components/common/ui";
import { convert, unitLabel, useSettings } from "../../src/state/units";

describe("表示単位（メートル法 ⇄ ヤード・ポンド法）", () => {
  afterEach(() => useSettings.getState().setUnits("metric"));
  it("メートル法では換算しない", () => {
    expect(convert(100, " m", "metric")).toEqual([100, " m"]);
    expect(fmt(1234.5, 1, " m")).toBe("1,234.5 m");
  });
  it("ヤード・ポンド法：m→ft、km→mi、km/h→mph、m/km→ft/mi", () => {
    expect(convert(100, "m", "imperial")[0]).toBeCloseTo(328.084, 3);
    expect(convert(1.609344, " km", "imperial")).toEqual([1, " mi"]);
    expect(convert(112.65408, "km/h", "imperial")[0]).toBeCloseTo(70, 6);
    expect(convert(1, "m/km", "imperial")[0]).toBeCloseTo(5.28, 2);
    expect(convert(5, "%", "imperial")).toEqual([5, "%"]);
    expect(unitLabel("km/h", "imperial")).toBe("mph");
  });
  it("設定を切り替えると fmt の表示も切り替わる", () => {
    useSettings.getState().setUnits("imperial");
    expect(fmt(1000, 0, " m")).toBe("3,281 ft");
    expect(fmt(113, 0, " km/h")).toBe("70 mph");
  });
});
