"""非同期ジョブ（標準の concurrent.futures ＋ DuckDB の job テーブル）。MVP は単一プロセス。"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from roadmri.provenance import new_ulid
from roadmri.store.duckdb_store import Store

Progress = Callable[[float, str], None]


class JobManager:
    def __init__(self, store: Store, max_workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="roadmri-job")
        self.futures: dict[str, Future[Any]] = {}

    def submit(self, kind: str, params: dict[str, Any], fn: Callable[[Progress], str | None]) -> str:
        job_id = new_ulid()
        self.store.append(
            "job",
            [
                {
                    "job_id": job_id,
                    "kind": kind,
                    "params": params,
                    "status": "queued",
                    "progress": 0.0,
                    "message": None,
                    "result_path": None,
                    "error": None,
                    "created_at": datetime.now(UTC),
                    "finished_at": None,
                }
            ],
        )

        def progress(p: float, msg: str = "") -> None:
            self.store.execute(
                "UPDATE job SET status='running', progress=?, message=? WHERE job_id=?",
                [float(p), msg, job_id],
            )

        def run() -> None:
            try:
                progress(0.0, "開始")
                path = fn(progress)
                self.store.execute(
                    "UPDATE job SET status='succeeded', progress=1.0, result_path=?, finished_at=? WHERE job_id=?",
                    [path, datetime.now(UTC), job_id],
                )
            except Exception as e:
                self.store.execute(
                    "UPDATE job SET status='failed', error=?, finished_at=? WHERE job_id=?",
                    [f"{e}\n{traceback.format_exc(limit=3)}", datetime.now(UTC), job_id],
                )

        self.futures[job_id] = self.pool.submit(run)
        return job_id

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        f = self.futures.get(job_id)
        if f is not None:
            f.result(timeout=timeout)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.store.one("SELECT * FROM job WHERE job_id=?", [job_id])

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
