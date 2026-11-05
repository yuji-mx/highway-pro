"""テスト fixture の作成。

1. OSM XML（OSM API 0.6 の map 応答や Overpass の応答）を複数読み込み、道路関連だけを抽出して PBF に書き出す。
     uv run python scripts/make_fixture.py osm <dir_of_osm_xml> tests/fixtures/tochigi_small.osm.pbf
2. 合成ネットワーク（tests/fixtures/synthetic/synthetic.osm.pbf）を生成する。
     uv run python scripts/make_fixture.py synthetic
3. fixture 範囲の地理院 DEM タイルを取得して tests/fixtures/dem/gsi/ に保存する（ネットワーク必要）。
     uv run python scripts/make_fixture.py dem tests/fixtures/tochigi_small.osm.pbf
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import osmium

KEEP_HW = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "services",
    "rest_area",
}


class Collector(osmium.SimpleHandler):  # type: ignore[misc]
    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[int, Any] = {}
        self.tagged_nodes: dict[int, Any] = {}
        self.ways: dict[int, Any] = {}
        self.rels: dict[int, Any] = {}

    def node(self, n: Any) -> None:
        self.nodes[n.id] = (
            n.replace(tags={k: v for k, v in n.tags})
            if False
            else osmium.osm.mutable.Node(n, tags={t.k: t.v for t in n.tags})
        )
        if n.tags.get("highway") == "motorway_junction" or n.tags.get("barrier") == "toll_booth":
            self.tagged_nodes[n.id] = True

    def way(self, w: Any) -> None:
        if w.tags.get("highway") in KEEP_HW:
            self.ways[w.id] = osmium.osm.mutable.Way(
                w, nodes=[nd.ref for nd in w.nodes], tags={t.k: t.v for t in w.tags}
            )

    def relation(self, r: Any) -> None:
        if r.tags.get("type") == "route" and r.tags.get("route") == "road":
            prev = self.rels.get(r.id)
            members = [(m.type, m.ref, m.role) for m in r.members]
            if prev is None or len(members) > len(prev.members):
                self.rels[r.id] = osmium.osm.mutable.Relation(
                    r, members=members, tags={t.k: t.v for t in r.tags}
                )


def osm_to_pbf(src_dir: Path, dest: Path) -> None:
    c = Collector()
    for f in sorted(src_dir.glob("*.osm")):
        c.apply_file(str(f))
    keep_nodes = set(c.tagged_nodes)
    for w in c.ways.values():
        keep_nodes.update(w.nodes)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    wr = osmium.SimpleWriter(str(dest))
    for nid in sorted(keep_nodes):
        if nid in c.nodes:
            wr.add_node(c.nodes[nid])
    for wid in sorted(c.ways):
        wr.add_way(c.ways[wid])
    for rid in sorted(c.rels):
        r = c.rels[rid]
        r.members = [m for m in r.members if m[0] == "w" and m[1] in c.ways]
        if r.members:
            wr.add_relation(r)
    wr.close()
    print(f"wrote {dest}: nodes={len(keep_nodes)} ways={len(c.ways)} rels={len(c.rels)}")


def fetch_dem(pbf: Path, out_dir: Path) -> None:
    import numpy as np

    from roadmri.config import load_config
    from roadmri.ingest.dem import GsiTileProvider
    from roadmri.ingest.http import HttpClient
    from roadmri.ingest.osm_pbf import read_osm

    cfg = load_config()
    osm = read_osm(pbf, highways={"motorway", "motorway_link", "trunk", "trunk_link"})
    lons = np.concatenate([np.asarray(x) for x in osm.ways["lons"]])
    lats = np.concatenate([np.asarray(x) for x in osm.ways["lats"]])
    http = HttpClient(cfg.settings)
    for dem_id in ("gsi_dem5a", "gsi_dem10b"):
        spec = cfg.dem_sources["sources"][dem_id]
        p = GsiTileProvider(dem_id, spec, {"gsi_cache_dirs": [out_dir], "http": http})
        tiles = p.tiles_for(lons, lats)
        for t in sorted(tiles):
            p.load_tile(*t)
        print(dem_id, "tiles", len(tiles), "fetched", p.tiles_fetched)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "osm":
        osm_to_pbf(Path(sys.argv[2]), Path(sys.argv[3]))
    elif cmd == "synthetic":
        from roadmri.ingest.synthetic import write_synthetic_fixture

        write_synthetic_fixture(Path("tests/fixtures/synthetic"))
    elif cmd == "dem":
        fetch_dem(
            Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else Path("tests/fixtures/dem/gsi")
        )
    else:
        raise SystemExit(__doc__)
