/** 加速車線長の短い表示（ADR-0016〜0018）。自動の値を基本とし、確認済みなら手動の値も併記する（自動の値は置き換えない）。 */
import { fmt } from "../common/ui";

export function accelShort(r: any): string {
  if (!r?.accel_lane_status) return "—"; // 本線への合流ではない
  const auto = r.accel_lane_status === "measured" ? fmt(r.accel_lane_len_m, 0, " m") : "対象外";
  switch (r.accel_manual_status) {
    case "confirmed":
      return `${auto}（確認済み）`;
    case "corrected":
      return `${auto}（手動 ${fmt(r.accel_manual_len_m, 0, " m")}）`;
    case "not_accel_lane":
      return `${auto}（手動：加速車線ではない）`;
    default:
      return auto;
  }
}
