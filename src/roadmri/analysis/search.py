"""検索の実行。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from roadmri.analysis.query import compile_query, normalize_target
from roadmri.analysis.ranges import resolve_facilities
from roadmri.store.duckdb_store import Store


def run_query(store: Store, q: dict[str, Any]) -> pd.DataFrame:
    target = normalize_target(str(q.get("target", "ramp")))
    q = {**q, "target": target}
    ids = None
    if q.get("range"):
        fac = store.df(
            "SELECT facility_id, lon, lat, route_refs, shape_class, admin_code, country FROM facility"
        )
        ids = resolve_facilities(q["range"], fac, lambda qq: run_query(store, qq))
    sql, params = compile_query(q, ids)
    return store.df(sql, params)


def count_total(store: Store, target: str) -> int:
    t = normalize_target(target)
    table = {"ramp": "ramp", "facility": "facility", "section": "section"}[t]
    return int(store.scalar(f"SELECT count(*) FROM {table} WHERE coalesce(excluded, false) = false") or 0)
