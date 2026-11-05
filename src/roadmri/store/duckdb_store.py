"""DuckDB ストア。パイプライン結果テーブルは run ごとに置き換え、手動修正・保存クエリ・ジョブは保持する。"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

RESULT_TABLES = (
    "facility",
    "facility_osm_map",
    "ramp",
    "ramp_way_map",
    "section",
    "section_distance",
    "sample_point",
    "classification_feature",
    "quality_flag",
    "display_feature",
    "dataset_source",
    "dem_dataset",
)
JSON_COLUMNS = {
    "facility": ("destinations", "classification_reasons", "merge_reason", "osm_ids"),
    "ramp": ("from_ref", "to_ref", "composite", "difficulty_components", "curves"),
    "classification_feature": ("features",),
    "quality_flag": ("detail",),
    "display_feature": ("props",),
    "run_metadata": ("range_spec", "params", "provenance"),
    "manual_edit": ("old_value", "new_value", "osm_anchor"),
    "saved_query": ("query",),
    "saved_range": ("spec",),
    "job": ("params",),
    "change_event": ("detail",),
}


def _json_default(o: Any) -> Any:
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def to_json(v: Any) -> str | None:
    if v is None:
        return None
    return json.dumps(v, ensure_ascii=False, default=_json_default, sort_keys=True)


class Store:
    def __init__(self, path: Path | str, read_only: bool = False):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.con = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            self.init_schema()

    def init_schema(self) -> None:
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        with self.cursor() as cur:
            cur.execute(sql)
        with self.lock:
            self.migrate()

    def migrate(self) -> None:
        mig = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        cur = self.con.execute("SELECT max(version) FROM schema_version").fetchall()[0]
        v = int(cur[0]) if cur and cur[0] is not None else 0
        for m in mig:
            num = int(m.name.split("_")[0])
            if num > v:
                self.con.execute(m.read_text(encoding="utf-8"))
                self.con.execute("INSERT INTO schema_version VALUES (?, ?)", [num, datetime.now(UTC)])

    def close(self) -> None:
        with self.lock:
            self.con.close()

    @contextmanager
    def cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self.lock:
            cur = self.con.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ---- 書込み -----------------------------------------------------------
    def _prepare(self, table: str, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for c in JSON_COLUMNS.get(table, ()):
            if c in df.columns:
                df[c] = [to_json(v) for v in df[c]]
        return df

    def replace_table(self, table: str, df: pd.DataFrame) -> None:
        df = self._prepare(table, df)
        with self.cursor() as cur:
            cols = [r[0] for r in cur.execute(f"DESCRIBE {table}").fetchall()]
            keep = [c for c in df.columns if c in cols]
            cur.execute("BEGIN")
            try:
                cur.execute(f"DELETE FROM {table}")
                if len(df):
                    cur.register("_df", df[keep])
                    collist = ", ".join(f'"{c}"' for c in keep)
                    cur.execute(f"INSERT INTO {table} ({collist}) SELECT {collist} FROM _df")
                    cur.unregister("_df")
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def append(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        df = self._prepare(table, pd.DataFrame(rows))
        with self.cursor() as cur:
            cur.register("_df", df)
            collist = ", ".join(f'"{c}"' for c in df.columns)
            cur.execute(f"INSERT INTO {table} ({collist}) SELECT {collist} FROM _df")
            cur.unregister("_df")

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        with self.cursor() as cur:
            cur.execute(sql, params or [])

    # ---- 読込み -----------------------------------------------------------
    def df(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self.cursor() as cur:
            return cur.execute(sql, params or []).fetchdf()

    def rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            res = cur.execute(sql, params or [])
            cols = [d[0] for d in res.description]
            return [dict(zip(cols, r, strict=True)) for r in res.fetchall()]

    def one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        r = self.rows(sql, params)
        return r[0] if r else None

    def scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        with self.cursor() as cur:
            r = cur.execute(sql, params or []).fetchone()
            return r[0] if r else None

    def current_run(self) -> dict[str, Any] | None:
        return self.one("SELECT * FROM run_metadata ORDER BY created_at DESC LIMIT 1")

    def has_data(self) -> bool:
        return bool(self.scalar("SELECT count(*) FROM facility"))
