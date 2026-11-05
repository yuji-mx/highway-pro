"""再現性情報（run_id、来歴）の生成。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from roadmri import __version__
from roadmri.config import Config

OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def git_commit(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def make_run_id(config_hash: str, input_hash: str, range_spec: dict[str, Any]) -> str:
    """同じ入力・同じ設定では同じ run_id になるよう、時刻部を固定した決定的 ULID を生成する。

    ULID の 48bit 時刻部は入力ダイジェストから決めるので、設定や入力が 1 つでも変われば run_id が変わる。
    """
    digest = hashlib.sha256(
        (config_hash + input_hash + json.dumps(range_spec, sort_keys=True, ensure_ascii=False)).encode()
    ).digest()
    return str(ULID.from_bytes(digest[:16]))


def new_ulid() -> str:
    return str(ULID())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_params(cfg: Config) -> dict[str, Any]:
    a = cfg.analysis
    return {
        "resample": a.get("resample"),
        "geometry_smoothing": a.get("geometry_smoothing"),
        "curvature": a.get("curvature"),
        "elevation_smoothing": a.get("elevation", {}).get("smoothing"),
        "structure_correction": a.get("elevation", {}).get("structure_correction"),
        "grade": {"method": "window_mean", **a.get("grade", {})},
        "classifier_version": cfg.classification_rules.get("version"),
        "difficulty": cfg.difficulty,
    }


def provenance_doc(
    cfg: Config,
    run_id: str,
    range_spec: dict[str, Any],
    sources: list[dict[str, Any]],
    dems: list[dict[str, Any]],
    manual_edit_count: int,
    last_edit_id: str | None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "app_version": __version__,
        "git_commit": git_commit(cfg.root),
        "range_spec": range_spec,
        "osm_sources": sources,
        "dem_datasets": dems,
        "params": build_params(cfg),
        "classifier_version": cfg.classification_rules.get("version"),
        "manual_edit_count": manual_edit_count,
        "last_edit_id": last_edit_id,
        "config_hash": cfg.config_hash,
        "attribution": attribution_text(dems),
    }


def attribution_text(dems: list[dict[str, Any]] | None = None) -> str:
    parts = [OSM_ATTRIBUTION]
    for d in dems or []:
        attr = d.get("attribution")
        if attr and attr not in parts:
            parts.append(str(attr))
    return " / ".join(parts)
