import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState,
} from "@tanstack/react-table";
import { useState, type ReactNode } from "react";
import { convert, unitLabel } from "../../state/units";

export interface Col<T> {
  key: string;
  label: string;
  num?: boolean;
  /** SI の単位（m、km、km/h 等）。表示単位の設定に従って値と見出しを換算する。 */
  unit?: string;
  get?: (r: T) => unknown;
  render?: (r: T) => ReactNode;
  sortable?: boolean;
}

/** 並べ替え・固定ヘッダー付きの表（TanStack Table）。 */
export default function DataTable<T extends Record<string, any>>({
  rows, cols, onRow, rowClass, maxHeight = 520, testId, onRowHover, serverSort, onServerSort,
}: {
  rows: T[];
  cols: Col<T>[];
  onRow?: (r: T) => void;
  rowClass?: (r: T) => string;
  maxHeight?: number | string;
  testId?: string;
  onRowHover?: (r: T | null) => void;
  /** サーバー側で並べ替える場合の現在の並び（指定時は表の中では並べ替えない）。 */
  serverSort?: { key: string; desc: boolean };
  onServerSort?: (key: string) => void;
}) {
  const [localSorting, setSorting] = useState<SortingState>([]);
  const sorting: SortingState = serverSort ? [{ id: serverSort.key, desc: serverSort.desc }] : localSorting;
  const columns: ColumnDef<T>[] = cols.map((c) => ({
    id: c.key,
    header: c.unit ? `${c.label} [${unitLabel(c.unit)}]` : c.label,
    accessorFn: (r: T) => (c.get ? c.get(r) : r[c.key]),
    enableSorting: c.sortable !== false,
    sortUndefined: "last",
    cell: (info) => (c.render ? c.render(info.row.original) : fmtCell(c.unit && typeof info.getValue() === "number" ? convert(info.getValue() as number, c.unit)[0] : info.getValue())),
    meta: { num: c.num },
  }));
  const table = useReactTable({
    data: rows, columns, state: { sorting }, onSortingChange: setSorting, manualSorting: !!serverSort,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  });
  return (
    <div className="tablewrap" style={{ maxHeight }} data-testid={testId}>
      <table className="dt">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th key={h.id} onClick={onServerSort ? (h.column.getCanSort() ? () => onServerSort(h.column.id) : undefined) : h.column.getToggleSortingHandler()} aria-sort={h.column.getIsSorted() === "asc" ? "ascending" : h.column.getIsSorted() === "desc" ? "descending" : "none"}>
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted() as string] ?? ""}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((r) => (
            <tr key={r.id} className={`${onRow ? "click" : ""} ${rowClass?.(r.original) ?? ""}`} onClick={() => onRow?.(r.original)}
              onMouseEnter={() => onRowHover?.(r.original)} onMouseLeave={() => onRowHover?.(null)}>
              {r.getVisibleCells().map((c) => (
                <td key={c.id} className={(c.column.columnDef.meta as any)?.num ? "n" : ""}>
                  {flexRender(c.column.columnDef.cell, c.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtCell(v: unknown): ReactNode {
  if (v == null) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("ja-JP") : v.toFixed(2);
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "○" : "";
  return String(v);
}

/** CSV（UTF-8 BOM 付き）をダウンロードする。 */
export function downloadCsv<T extends Record<string, any>>(rows: T[], cols: Col<T>[], name: string): void {
  const esc = (v: unknown) => {
    const s = v == null ? "" : Array.isArray(v) ? v.join(";") : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [cols.map((c) => esc(c.label)).join(","), ...rows.map((r) => cols.map((c) => esc(c.get ? c.get(r) : r[c.key])).join(","))];
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
}
