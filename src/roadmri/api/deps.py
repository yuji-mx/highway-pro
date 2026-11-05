"""API の依存（設定・ストア・ジョブ・ネットワークのキャッシュ）。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from roadmri.config import Config
from roadmri.store.duckdb_store import Store
from roadmri.store.jobs import JobManager


@dataclass
class AppState:
    cfg: Config
    store: Store
    jobs: JobManager
    _net: Any = None
    _net_run: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def network(self) -> Any:
        """ルート解析用：最新 run の中間成果物（区間段）からネットワークを読み込む（キャッシュ）。"""
        from roadmri.pipeline import load_state

        run = self.store.current_run()
        if run is None:
            raise LookupError("データがありません。先に roadmri build を実行してください")
        with self.lock:
            if self._net is None or self._net_run != run["run_id"]:
                self._net = load_state(self.cfg, run["region_id"], "sections")
                self._net_run = run["run_id"]
            return self._net

    def run_info(self) -> dict[str, Any]:
        run = self.store.current_run() or {}
        out = {
            k: run.get(k)
            for k in (
                "run_id",
                "created_at",
                "config_hash",
                "classifier_version",
                "region_id",
                "manual_edit_count",
                "git_commit",
                "duration_s",
            )
        }
        prov = run.get("provenance")
        out["provenance"] = json.loads(prov) if isinstance(prov, str) else prov
        return out


def state(request: Request) -> AppState:
    return request.app.state.roadmri  # type: ignore[no-any-return]
