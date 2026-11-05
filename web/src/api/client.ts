/** API クライアント（/api）。エラーは RFC 9457 Problem Details。 */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail: unknown = r.statusText;
    try {
      const j = await r.json();
      detail = j.detail ?? j;
    } catch {
      /* 本文なし */
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  get: <T>(path: string) => fetch(`/api${path}`).then((r) => handle<T>(r)),
  post: <T>(path: string, body: unknown) =>
    fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(
      (r) => handle<T>(r),
    ),
  del: <T>(path: string) => fetch(`/api${path}`, { method: "DELETE" }).then((r) => handle<T>(r)),
  blob: (path: string, body: unknown) =>
    fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(
      async (r) => {
        if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({}))).detail ?? r.statusText);
        return r.blob();
      },
    ),
  bytes: (path: string) =>
    fetch(`/api${path}`).then(async (r) => {
      if (!r.ok) throw new ApiError(r.status, r.statusText);
      return new Uint8Array(await r.arrayBuffer());
    }),
};

export function downloadBlob(blob: Blob, name: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const d = e.detail as any;
    if (typeof d === "string") return d;
    if (d && typeof d.message === "string") return d.message;
    return `エラー（${e.status}）`;
  }
  return e instanceof Error ? e.message : String(e);
}

/** ジョブの完了を待つ（進捗コールバック付き）。 */
export async function waitJob(jobId: string, onProgress?: (p: number, msg?: string) => void): Promise<any> {
  for (;;) {
    const j: any = await api.get(`/jobs/${jobId}`);
    onProgress?.(j.progress ?? 0, j.message);
    if (j.status === "succeeded") return j;
    if (j.status === "failed") throw new Error(j.error ?? "ジョブが失敗しました");
    await new Promise((r) => setTimeout(r, 500));
  }
}
