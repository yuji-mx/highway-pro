from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ROADMRI_ROOT", str(ROOT))

from roadmri.config import Config, load_config  # noqa: E402


@pytest.fixture(scope="session")
def cfg() -> Config:
    return load_config(ROOT)


@pytest.fixture(scope="session")
def analysis_cfg(cfg: Config) -> dict:
    return cfg.analysis


def _cfg_for(tmp: Path, base: Config) -> Config:
    return base.with_overrides(
        {
            "settings.paths.processed_db": str(tmp / "roadmri.duckdb"),
            "settings.paths.interim_dir": str(tmp / "interim"),
            "settings.paths.tiles_dir": str(tmp / "tiles"),
            "settings.paths.exports_dir": str(tmp / "exports"),
            "settings.paths.logs_dir": str(tmp / "logs"),
            "settings.paths.raw_dir": str(tmp / "raw"),
            "dem_sources.offline": True,
        }
    )


def _build(tmp: Path, base: Config, region: str):
    from roadmri.pipeline import run_pipeline
    from roadmri.store.duckdb_store import Store

    cfg = _cfg_for(tmp, base)
    store = Store(cfg.path("processed_db"))
    result = run_pipeline(cfg, region, store)
    return cfg, store, result


@pytest.fixture(scope="session")
def synthetic_db(tmp_path_factory, cfg):
    """合成ネットワークで全パイプラインを実行した DuckDB（セッション共有、読み取り用途）。"""
    return _build(tmp_path_factory.mktemp("syn"), cfg, "synthetic")


@pytest.fixture(scope="session")
def tochigi_db(tmp_path_factory, cfg):
    """実データ fixture（宇都宮IC〜鹿沼IC周辺）と同梱 DEM で全パイプラインを実行した DuckDB。"""
    return _build(tmp_path_factory.mktemp("tochigi"), cfg, "tochigi_small")


@pytest.fixture
def fresh_synthetic(tmp_path, cfg):
    """変更を加えるテスト用の独立した合成 DB。"""
    return _build(tmp_path, cfg, "synthetic")
