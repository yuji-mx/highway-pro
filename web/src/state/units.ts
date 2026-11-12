import { create } from "zustand";

/** 表示単位（内部・API は SI。§5.9：北米表示は mph・ft に切替可）。 */
export type Units = "metric" | "imperial";

const KEY = "roadmri.units";
function initial(): Units {
  try {
    return localStorage.getItem(KEY) === "imperial" ? "imperial" : "metric";
  } catch {
    return "metric";
  }
}

interface SettingsState {
  units: Units;
  setUnits: (u: Units) => void;
}

export const useSettings = create<SettingsState>((set) => ({
  units: initial(),
  setUnits: (u) => {
    try {
      localStorage.setItem(KEY, u);
    } catch {
      /* 保存できない環境では無視 */
    }
    set({ units: u });
  },
}));

const IMPERIAL: Record<string, [string, number]> = {
  m: ["ft", 1 / 0.3048],
  km: ["mi", 1 / 1.609344],
  "km/h": ["mph", 1 / 1.609344],
  "m/km": ["ft/mi", 1.609344 / 0.3048],
};

/** 値と単位を表示単位に換算する（対象外の単位はそのまま）。 */
export function convert(v: number, unit: string, units: Units = useSettings.getState().units): [number, string] {
  const lead = unit.startsWith(" ") ? " " : "";
  const u = unit.trim();
  if (units !== "imperial" || !(u in IMPERIAL)) return [v, unit];
  const [nu, k] = IMPERIAL[u];
  return [v * k, `${lead}${nu}`];
}

/** 表示単位でのラベル（"m" → "ft" など）。 */
export function unitLabel(unit: string, units: Units = useSettings.getState().units): string {
  return convert(1, unit, units)[1];
}
