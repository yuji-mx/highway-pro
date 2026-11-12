import ja from "./ja.json";

const dict: Record<string, string> = ja;

/** UI 文言（英語化できるよう ja.json に集約）。 */
export function t(key: string, fallback?: string): string {
  return dict[key] ?? fallback ?? key;
}
