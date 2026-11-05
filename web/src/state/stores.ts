import { create } from "zustand";
import type { RangeSpec } from "../api/types";

/** 地図レイヤーと色分け（§11.4）。 */
export const LAYER_KEYS = ["mainline", "ramps", "general", "ic", "jct", "sapa", "tolls", "bridges", "tunnels", "target", "warnings", "labels"] as const;
export type LayerKey = (typeof LAYER_KEYS)[number];

interface LayersState {
  visible: Record<LayerKey, boolean>;
  metric: string;
  glow: boolean;
  widthByCurvature: boolean;
  highlightTargets: boolean;
  toggle: (k: LayerKey) => void;
  setMetric: (m: string) => void;
  set: (p: Partial<Omit<LayersState, "toggle" | "setMetric" | "set">>) => void;
}

export const useLayers = create<LayersState>((set) => ({
  visible: Object.fromEntries(LAYER_KEYS.map((k) => [k, true])) as Record<LayerKey, boolean>,
  metric: "road_type",
  glow: true,
  widthByCurvature: false,
  highlightTargets: true,
  toggle: (k) => set((s) => ({ visible: { ...s.visible, [k]: !s.visible[k] } })),
  setMetric: (m) => set({ metric: m }),
  set: (p) => set(p),
}));

/** 解析対象（検索結果）の強調表示。 */
interface TargetsState {
  rampIds: string[];
  facilityIds: string[];
  setTargets: (rampIds: string[], facilityIds: string[]) => void;
}
export const useTargets = create<TargetsState>((set) => ({
  rampIds: [],
  facilityIds: [],
  setTargets: (rampIds, facilityIds) => set({ rampIds, facilityIds }),
}));

/** 比較トレイ（A・B の 2 枠。3 件目を登録すると古い方を押し出す。§4.13）。 */
export interface TrayItem {
  facility_id: string;
  name: string | null;
}
interface CompareTrayState {
  a: TrayItem | null;
  b: TrayItem | null;
  open: boolean;
  add: (it: TrayItem) => void;
  remove: (slot: "a" | "b") => void;
  swap: () => void;
  setBoth: (a: TrayItem, b: TrayItem) => void;
  setOpen: (o: boolean) => void;
}
export const useCompareTray = create<CompareTrayState>((set) => ({
  a: null,
  b: null,
  open: false,
  add: (it) =>
    set((s) => {
      if (s.a?.facility_id === it.facility_id || s.b?.facility_id === it.facility_id) return s;
      if (!s.a) return { a: it };
      if (!s.b) return { b: it };
      return { a: s.b, b: it };
    }),
  remove: (slot) => set(slot === "a" ? { a: null } : { b: null }),
  swap: () => set((s) => ({ a: s.b, b: s.a })),
  setBoth: (a, b) => set({ a, b, open: true }),
  setOpen: (o) => set({ open: o }),
}));

/** 統計比較の範囲 A・B（§4.14.2）。 */
interface RangesState {
  a: RangeSpec | null;
  b: RangeSpec | null;
  setRange: (slot: "a" | "b", r: RangeSpec | null) => void;
  swap: () => void;
}
export const useRanges = create<RangesState>((set) => ({
  a: null,
  b: null,
  setRange: (slot, r) => set(slot === "a" ? { a: r } : { b: r }),
  swap: () => set((s) => ({ a: s.b, b: s.a })),
}));

/** 地図カーソル（プロファイルと地図の連動）。 */
interface CursorState {
  pos: [number, number] | null;
  setPos: (p: [number, number] | null) => void;
}
export const useCursor = create<CursorState>((set) => ({ pos: null, setPos: (p) => set({ pos: p }) }));

/** 類似パネルの基準（条件検索画面）。 */
interface SimilarState {
  base: { kind: "facility" | "ramp"; id: string } | null;
  setBase: (b: { kind: "facility" | "ramp"; id: string } | null) => void;
}
export const useSimilar = create<SimilarState>((set) => ({ base: null, setBase: (b) => set({ base: b }) }));

/** トースト。 */
interface ToastState {
  items: { id: number; text: string; err?: boolean }[];
  push: (text: string, err?: boolean) => void;
}
let tid = 0;
export const useToast = create<ToastState>((set) => ({
  items: [],
  push: (text, err) => {
    const id = ++tid;
    set((s) => ({ items: [...s.items, { id, text, err }] }));
    setTimeout(() => set((s) => ({ items: s.items.filter((x) => x.id !== id) })), 4000);
  },
}));
