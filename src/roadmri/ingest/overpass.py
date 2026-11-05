"""Overpass API（小規模試験専用。§4.1.2）。1 リクエスト／10 秒以上、タイムアウト 180 s。"""

from __future__ import annotations

from pathlib import Path

from roadmri.ingest.http import HttpClient


def build_query(bbox: list[float], timeout_s: int = 180, general: bool = True) -> str:
    w, s, e, n = bbox
    b = f"({s},{w},{n},{e})"
    roads = "motorway|motorway_link|trunk|trunk_link"
    if general:
        roads += "|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link"
    return (
        f"[out:xml][timeout:{timeout_s}];\n(\n"
        f'  way["highway"~"^({roads})$"]{b};\n'
        f'  way["highway"~"^(services|rest_area)$"]{b};\n'
        f'  node["highway"="motorway_junction"]{b};\n'
        f'  node["barrier"="toll_booth"]{b};\n'
        f'  relation["type"="route"]["route"="road"]{b};\n'
        ");\n(._;>;);\nout meta;"
    )


def fetch_overpass(
    http: HttpClient, url: str, bbox: list[float], dest: Path, timeout_s: int = 180, force: bool = False
) -> Path:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest
    q = build_query(bbox, timeout_s)
    r = http.request("POST", url, data={"data": q})
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest
