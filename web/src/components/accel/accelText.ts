/** 加速車線長の短い表示（ADR-0020）。「暫定値」「判定保留」「対象外」「確認済み」を区別する。
 * 状態と文言は API（accel_display）が決める。自動の値は置き換えない（確認済みでも自動の値は別に残る）。 */
import { fmt } from "../common/ui";

export function accelShort(r: any): string {
  const d = r?.accel_display;
  if (!d) return r?.accel_lane_status ? "—" : "—"; // 本線への合流ではない（または以前の API）
  switch (d.state) {
    case "provisional":
      return `暫定値 ${fmt(d.length_m, 0, " m")}（地図データ）`;
    case "confirmed":
      return `${fmt(d.length_m, 0, " m")}（${d.label}）`;
    case "pending":
      return d.provisional_length_m != null ? `${d.label}・地図データによる暫定値 ${fmt(d.provisional_length_m, 0, " m")}` : d.label;
    default:
      return d.label;
  }
}

/** 状態のバッジ用のクラス名。 */
export const accelStateClass = (r: any): string => `state-badge state-${r?.accel_display?.state ?? "excluded"}`;
