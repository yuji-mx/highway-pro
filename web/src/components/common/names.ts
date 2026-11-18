/** 施設名の表示用の整形。元のデータ（OSM の name）は変えず、画面に出すときだけ読みやすくする。
 *
 * OSM では 1 つの値に複数の名前を「;」で区切って入れることがある（例：「上河内SA;上河内スマートIC」）。
 * 画面では「／」でつないで表示する（区切り文字を利用者に見せない）。
 */
export function displayName(name: unknown): string {
  if (name == null) return "名称不明";
  const parts = String(name).split(";").map((s) => s.trim()).filter(Boolean);
  return parts.length ? [...new Set(parts)].join(" ／ ") : "名称不明";
}
