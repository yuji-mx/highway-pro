"""FastAPI アプリ（プレフィックス /api）。Web UI（Vite ビルド成果物）を同じプロセスで静的配信する。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from roadmri import __version__
from roadmri.analysis.query import QueryError
from roadmri.analysis.ranges import RangeError
from roadmri.api.deps import AppState
from roadmri.api.routers import (
    changes,
    compare,
    edits,
    export,
    facilities,
    images,
    jobs,
    layers,
    meta,
    quality,
    ramps,
    regions,
    reports,
    route,
    search,
    sections,
    similar,
)
from roadmri.config import Config, get_config
from roadmri.export.tabular import ExportError
from roadmri.store.duckdb_store import Store
from roadmri.store.jobs import JobManager
from roadmri.store.manual_edits import EditError


def problem(status: int, title: str, detail: Any, instance: str | None = None) -> JSONResponse:
    """RFC 9457 Problem Details（detail は日本語）。"""
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": instance},
        status_code=status,
        media_type="application/problem+json",
    )


def create_app(cfg: Config | None = None, store: Store | None = None) -> FastAPI:
    cfg = cfg or get_config()
    store = store or Store(cfg.path("processed_db"))
    app = FastAPI(
        title="ROAD MRI API",
        version=__version__,
        description="高速道路ネットワーク解析・可視化システム。エラーは RFC 9457 形式（detail は日本語）。",
    )
    app.state.roadmri = AppState(cfg=cfg, store=store, jobs=JobManager(store))
    for r in (
        meta,
        regions,
        jobs,
        layers,
        facilities,
        ramps,
        sections,
        search,
        similar,
        compare,
        route,
        export,
        images,
        reports,
        edits,
        quality,
        changes,
    ):
        app.include_router(r.router, prefix="/api")

    @app.exception_handler(HTTPException)
    async def http_exc(req: Request, exc: HTTPException) -> JSONResponse:
        title = {404: "見つかりません", 409: "競合", 422: "入力が不正です"}.get(exc.status_code, "エラー")
        return problem(exc.status_code, title, exc.detail, str(req.url.path))

    @app.exception_handler(RequestValidationError)
    async def val_exc(req: Request, exc: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return problem(
            422,
            "入力が不正です",
            {"message": "リクエストの形式が正しくありません", "errors": errs},
            str(req.url.path),
        )

    @app.exception_handler(LookupError)
    async def not_found(req: Request, exc: LookupError) -> JSONResponse:
        return problem(404, "見つかりません", str(exc).strip("'\""), str(req.url.path))

    for et in (QueryError, RangeError, EditError, ExportError):

        @app.exception_handler(et)
        async def bad(req: Request, exc: Exception) -> JSONResponse:
            return problem(422, "入力が不正です", str(exc), str(req.url.path))

    @app.exception_handler(Exception)
    async def internal(req: Request, exc: Exception) -> JSONResponse:
        return problem(500, "内部エラー", f"処理中にエラーが発生しました：{exc}", str(req.url.path))

    dist = cfg.root / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        for sub in ("fonts",):
            if (dist / sub).exists():
                app.mount(f"/{sub}", StaticFiles(directory=dist / sub), name=sub)

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404, "API が見つかりません")
            f = (dist / path).resolve()
            if path and f.is_file() and f.is_relative_to(dist.resolve()):
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    @app.on_event("shutdown")
    def _shutdown() -> None:
        app.state.roadmri.jobs.shutdown()

    return app
