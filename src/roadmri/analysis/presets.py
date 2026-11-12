"""目的別プリセット（§4.15.3）。定義は config/presets.yaml。"""

from __future__ import annotations

from typing import Any

from roadmri.config import Config


def list_presets(cfg: Config) -> list[dict[str, Any]]:
    return list(cfg.presets.get("presets", []))


def get_preset(cfg: Config, preset_id: str) -> dict[str, Any]:
    for p in list_presets(cfg):
        if p["id"] == preset_id:
            return p
    raise KeyError(f"プリセットが見つかりません: {preset_id}")
