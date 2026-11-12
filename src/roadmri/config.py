"""設定ファイル（config/*.yaml）の読込。数値パラメータはすべてここから取得する。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILES = (
    "settings",
    "routes",
    "name_normalization",
    "name_aliases",
    "analysis",
    "classification_rules",
    "difficulty",
    "similarity",
    "presets",
    "stats",
    "dem_sources",
)


def project_root() -> Path:
    env = os.environ.get("ROADMRI_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for p in [Path.cwd(), *here.parents]:
        if (p / "config" / "analysis.yaml").exists():
            return p
    return Path.cwd()


@dataclass
class Config:
    root: Path
    data: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __getattr__(self, name: str) -> dict[str, Any]:
        data = self.__dict__.get("data", {})
        if name in data:
            return data[name]  # type: ignore[no-any-return]
        raise AttributeError(name)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def path(self, key: str) -> Path:
        p = Path(self.data["settings"]["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def config_hash(self) -> str:
        """解析結果に影響する設定のハッシュ。パス・ポート・HTTP・ユーザー名・オフライン指定など環境依存の値は除く。"""
        data = json.loads(json.dumps(self.data, default=str))
        settings = data.get("settings", {})
        for k in ("paths", "server", "http", "user"):
            settings.pop(k, None)
        data.get("dem_sources", {}).pop("offline", None)
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def with_overrides(self, overrides: dict[str, Any]) -> Config:
        """ドット区切りキーで上書きしたコピーを返す（テスト・再現性検証用）。"""
        data = json.loads(json.dumps(self.data, default=str))
        for key, value in overrides.items():
            parts = key.split(".")
            cur = data
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
        return Config(root=self.root, data=data)

    def region(self, region_id: str) -> dict[str, Any]:
        p = Path(region_id)
        if not p.suffix:
            p = self.root / "config" / "regions" / f"{region_id}.yaml"
        elif not p.is_absolute():
            p = self.root / p
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)  # type: ignore[no-any-return]

    def regions(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.root / "config" / "regions").glob("*.yaml")):
            with open(p, encoding="utf-8") as f:
                out.append(yaml.safe_load(f))
        return out

    @property
    def user_name(self) -> str:
        name = self.data["settings"].get("user", {}).get("name")
        if name:
            return str(name)
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def load_config(root: Path | None = None) -> Config:
    root = (root or project_root()).resolve()
    data: dict[str, dict[str, Any]] = {}
    for name in CONFIG_FILES:
        p = root / "config" / f"{name}.yaml"
        if p.exists():
            with open(p, encoding="utf-8") as f:
                data[name] = yaml.safe_load(f) or {}
        else:
            data[name] = {}
    return Config(root=root, data=data)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
