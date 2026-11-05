from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["similar"])
# 類似検索は /facilities/{id}/similar と /ramps/{id}/similar（facilities.py・ramps.py）で提供する。
