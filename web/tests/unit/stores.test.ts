import { beforeEach, describe, expect, it } from "vitest";
import { useCompareTray, useRanges } from "../../src/state/stores";

describe("比較トレイ", () => {
  beforeEach(() => useCompareTray.setState({ a: null, b: null, open: false }));
  it("A・B の順に埋まり、3 件目で古い方を押し出す", () => {
    const s = useCompareTray.getState();
    s.add({ facility_id: "1", name: "一" });
    s.add({ facility_id: "2", name: "二" });
    expect(useCompareTray.getState().a?.facility_id).toBe("1");
    expect(useCompareTray.getState().b?.facility_id).toBe("2");
    useCompareTray.getState().add({ facility_id: "3", name: "三" });
    expect(useCompareTray.getState().a?.facility_id).toBe("2");
    expect(useCompareTray.getState().b?.facility_id).toBe("3");
  });
  it("同じ施設は二重登録しない・解除・入れ替え", () => {
    const s = useCompareTray.getState();
    s.add({ facility_id: "1", name: null });
    s.add({ facility_id: "1", name: null });
    expect(useCompareTray.getState().b).toBeNull();
    useCompareTray.getState().add({ facility_id: "2", name: null });
    useCompareTray.getState().swap();
    expect(useCompareTray.getState().a?.facility_id).toBe("2");
    useCompareTray.getState().remove("a");
    expect(useCompareTray.getState().a).toBeNull();
  });
  it("類似一覧の「比較」で A/B を設定してパネルを開く", () => {
    useCompareTray.getState().setBoth({ facility_id: "x", name: "X" }, { facility_id: "y", name: "Y" });
    const st = useCompareTray.getState();
    expect([st.a?.facility_id, st.b?.facility_id, st.open]).toEqual(["x", "y", true]);
  });
});

describe("範囲ストア", () => {
  it("範囲の設定と入れ替え", () => {
    useRanges.getState().setRange("a", { type: "route", route_refs: ["E4"], label: "東北道" });
    useRanges.getState().setRange("b", { type: "facilities", facility_ids: ["f1"] });
    useRanges.getState().swap();
    expect(useRanges.getState().a?.type).toBe("facilities");
    expect(useRanges.getState().b?.label).toBe("東北道");
    useRanges.getState().setRange("a", null);
    expect(useRanges.getState().a).toBeNull();
  });
});
