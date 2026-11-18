"""地図の背景の航空写真（加速車線の確認用。ADR-0017）。外部のタイルは ingest/http.py 経由で取得し、ディスクに保存する。

表示した範囲のタイルだけを要求する（一括ダウンロードはしない）。オフラインでは保存済みのタイルだけを返す。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from roadmri.api.deps import AppState, state
from roadmri.ingest.http import HttpClient, NotFoundError, OfflineError

router = APIRouter(tags=["layers"])
_client: dict[int, HttpClient] = {}
_lock = threading.Lock()


def _http(s: AppState) -> HttpClient:
    with _lock:
        key = id(s)
        if key not in _client:
            _client[key] = HttpClient(s.cfg.settings)
        return _client[key]


def _media(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _cache(s: AppState, source: str, z: int, x: int, y: int) -> Path:
    return s.cfg.path("cache_dir") / "imagery" / source / str(z) / str(x) / f"{y}.img"


@router.get("/imagery/{source}/{z}/{x}/{y}", response_class=Response)
def imagery_tile(source: str, z: int, x: int, y: int, s: AppState = Depends(state)) -> Response:
    cfg: dict[str, Any] = s.cfg.settings.get("imagery", {}) or {}
    spec = (cfg.get("sources") or {}).get(source)
    if spec is None:
        raise HTTPException(404, f"航空写真の取得元が見つかりません: {source}")
    if not 0 <= z <= int(spec["max_zoom"]) or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise HTTPException(404, f"タイルの範囲外です: {z}/{x}/{y}")
    path = _cache(s, source, z, x, y)
    missing = path.with_suffix(".missing")
    headers = {"Cache-Control": f"public, max-age={int(float(cfg.get('browser_cache_days', 1)) * 86400)}"}
    if path.exists():
        data = path.read_bytes()
    elif missing.exists():
        raise HTTPException(404, "この位置の航空写真はありません")
    else:
        url = str(spec["url"]).format(z=z, x=x, y=y)
        try:
            data = _http(s).get_bytes(url)
        except OfflineError as e:
            raise HTTPException(404, "オフラインのため、保存されていない航空写真は表示できません") from e
        except NotFoundError as e:
            missing.parent.mkdir(parents=True, exist_ok=True)
            missing.write_text("404")
            raise HTTPException(404, "この位置の航空写真はありません") from e
        except httpx.HTTPError as e:
            raise HTTPException(502, f"航空写真を取得できませんでした: {e}") from e
        if _media(data) is None:
            raise HTTPException(502, "航空写真の応答が画像ではありません")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return Response(data, media_type=_media(data) or "application/octet-stream", headers=headers)
