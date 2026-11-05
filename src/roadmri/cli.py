"""roadmri コマンド（Typer）。"""

from __future__ import annotations

import json
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from roadmri.config import get_config, load_config
from roadmri.logging import setup_logging

app = typer.Typer(
    help="ROAD MRI：高速道路ネットワーク解析・可視化システム", no_args_is_help=True, add_completion=False
)


def _region_id(range_: str) -> str:
    p = Path(range_)
    return p.stem if p.suffix in (".yaml", ".yml") else range_


def _store(cfg: Any) -> Any:
    from roadmri.store.duckdb_store import Store

    return Store(cfg.path("processed_db"))


@app.command()
def fetch(
    range_: Annotated[str, typer.Option("--range", help="地域 ID または config/regions/*.yaml")] = "tochigi",
    source: Annotated[
        str, typer.Option(help="pbf（既定）または overpass（半径 30 km 以下の試験用）")
    ] = "pbf",
    force: Annotated[bool, typer.Option(help="キャッシュがあっても再取得する")] = False,
) -> None:
    """OSM・DEM を取得する（キャッシュ利用）。"""
    from roadmri.analysis.ranges import range_geometry
    from roadmri.ingest.http import HttpClient
    from roadmri.ingest.overpass import fetch_overpass

    cfg = get_config()
    setup_logging(cfg.path("logs_dir"))
    region = cfg.region(_region_id(range_))
    http = HttpClient(cfg.settings)
    today = datetime.now(UTC).strftime("%Y%m%d")
    pbf = region.get("pbf", {})
    if source == "overpass":
        g = range_geometry(region["range"])
        if g is None:
            raise typer.BadParameter("Overpass は矩形・円で指定できる範囲のみ対応します")
        b = list(g.bounds)
        span_km = max((b[2] - b[0]) * 90, (b[3] - b[1]) * 111)
        if span_km > 60:
            raise typer.BadParameter("Overpass は半径 30 km 以下の試験用です。PBF を使ってください")
        dest = cfg.path("raw_dir") / "osm" / "overpass" / today / f"{region['id']}.osm"
        fetch_overpass(
            http,
            cfg.settings["osm"]["overpass_url"],
            b,
            dest,
            int(cfg.settings["osm"]["overpass_timeout_s"]),
            force,
        )
        typer.echo(f"取得済み: {dest}")
        return
    if pbf.get("local"):
        typer.echo(f"ローカル PBF を使用します: {pbf['local']}")
        return
    for url in [pbf["url"], *pbf.get("extra", [])]:
        name = Path(url).name
        existing = sorted((cfg.path("raw_dir") / "osm" / "geofabrik").glob(f"*/{name}"))
        if existing and not force:
            typer.echo(f"キャッシュあり（取得をスキップ）: {existing[-1]}")
            continue
        dest = cfg.path("raw_dir") / "osm" / "geofabrik" / today / name
        typer.echo(f"取得中: {url}")
        http.download(url, dest, force=force)
        typer.echo(f"保存: {dest}")


@app.command()
def build(
    range_: Annotated[str, typer.Option("--range", help="地域 ID")] = "tochigi",
    from_step: Annotated[str | None, typer.Option("--from-step")] = None,
    to_step: Annotated[str | None, typer.Option("--to-step")] = None,
    source: Annotated[str | None, typer.Option(help="overpass で Overpass 取得データを使う")] = None,
    offline: Annotated[bool, typer.Option(help="DEM をキャッシュだけで処理する")] = False,
) -> None:
    """パイプラインを実行して DuckDB に反映する。"""
    from roadmri.pipeline import run_pipeline

    cfg = get_config()
    if offline:
        cfg = cfg.with_overrides({"dem_sources.offline": True})
    setup_logging(cfg.path("logs_dir"))
    store = _store(cfg)
    res = run_pipeline(
        cfg,
        _region_id(range_),
        store,
        from_step,
        to_step,
        source,
        progress=lambda p, m: typer.echo(f"[{p * 100:5.1f}%] {m}"),
    )
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


@app.command()
def serve(
    port: Annotated[int, typer.Option()] = 0,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
    host: Annotated[str | None, typer.Option(help="既定は 127.0.0.1（ローカルのみ）")] = None,
) -> None:
    """API と Web UI を起動する。"""
    import uvicorn

    from roadmri.api.main import create_app

    cfg = get_config()
    setup_logging(cfg.path("logs_dir"))
    h = host or cfg.settings["server"]["host"]
    p = port or int(cfg.settings["server"]["port"])
    if not no_browser:
        webbrowser.open(f"http://{h}:{p}")
    uvicorn.run(create_app(cfg), host=h, port=p, log_level="info")


def _load_query(q: str, cfg: Any) -> dict[str, Any]:
    p = Path(q)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    from roadmri.analysis.presets import get_preset

    name = q.split("/")[-1]
    out: dict[str, Any] = get_preset(cfg, name)["query"]
    return out


@app.command()
def export(
    dataset: Annotated[str, typer.Option(help="facilities / ramps / sections / samples")] = "ramps",
    fmt: Annotated[
        str, typer.Option("--format", help="csv / xlsx / geojson / gpkg / parquet / sqlite")
    ] = "csv",
    query: Annotated[str | None, typer.Option(help="QueryDSL の JSON ファイルまたはプリセット名")] = None,
    out: Annotated[Path | None, typer.Option(help="出力先ディレクトリ")] = None,
) -> None:
    """データを出力する（run_id と provenance.json を同梱）。"""
    from roadmri.export.tabular import export_dataset

    cfg = get_config()
    store = _store(cfg)
    q = _load_query(query, cfg) if query else None
    path = export_dataset(store, cfg, dataset, fmt, q, out)
    typer.echo(f"出力: {path}")


@app.command()
def images(
    query: Annotated[str, typer.Option(help="QueryDSL の JSON ファイルまたはプリセット名")] = "spec_example",
    fmt: Annotated[str, typer.Option("--format")] = "png",
    size: Annotated[str, typer.Option(help="幅x高さ（px）")] = "1920x1080",
    metric: Annotated[str, typer.Option(help="色分け指標")] = "road_type",
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """検索結果の各件について道路 MRI 画像を一括生成し ZIP にまとめる。"""
    from roadmri.analysis.search import run_query
    from roadmri.export.images import batch_images

    cfg = get_config()
    store = _store(cfg)
    q = _load_query(query, cfg)
    res = run_query(store, q)
    w, h = (int(v) for v in size.lower().split("x"))
    path = batch_images(store, cfg, res, fmt, (w, h), metric, out)
    typer.echo(f"出力: {path}（{len(res)} 件）")


@app.command()
def report(
    query: Annotated[str, typer.Option()] = "spec_example",
    out: Annotated[Path | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option()] = 20,
) -> None:
    """検索結果の画像レポート（自己完結 HTML ＋画像フォルダ）を生成する。"""
    from roadmri.analysis.search import run_query
    from roadmri.export.report import build_report

    cfg = get_config()
    store = _store(cfg)
    q = _load_query(query, cfg)
    res = run_query(store, q).head(limit)
    path = build_report(store, cfg, res, q, out)
    typer.echo(f"出力: {path}")


@app.command()
def search(
    query: Annotated[str, typer.Option()] = "spec_example",
    fmt: Annotated[str, typer.Option("--format", help="table / csv / json")] = "table",
) -> None:
    """QueryDSL（またはプリセット）で検索して結果を表示する。"""
    from roadmri.analysis.query import to_text
    from roadmri.analysis.search import run_query

    cfg = get_config()
    store = _store(cfg)
    q = _load_query(query, cfg)
    res = run_query(store, q)
    typer.echo(f"検索式: {to_text(q)}  → {len(res)} 件", err=True)
    if fmt == "csv":
        res.to_csv(sys.stdout, index=False)
    elif fmt == "json":
        typer.echo(res.to_json(orient="records", force_ascii=False))
    else:
        cols = [
            c
            for c in (
                "ramp_id",
                "facility_id",
                "name",
                "flow",
                "length_m",
                "min_radius_m",
                "grade_up_max_w100",
                "difficulty",
                "section_id",
                "dist_standard_m",
            )
            if c in res.columns
        ]
        typer.echo(res[cols].to_string(index=False))


@app.command()
def stats(
    a: Annotated[Path, typer.Option(help="範囲 A の RangeSpec JSON")],
    b: Annotated[Path, typer.Option(help="範囲 B の RangeSpec JSON")],
    out: Annotated[Path, typer.Option()] = Path("stats.csv"),
    hit_query: Annotated[
        str, typer.Option(help="該当率を比較する検索条件（JSON ファイルまたはプリセット名）")
    ] = "spec_example",
) -> None:
    """2 つの範囲の統計比較を行い、統計表 CSV を出力する。"""
    from roadmri.analysis.compare import compare_ranges, stats_table

    cfg = get_config()
    store = _store(cfg)
    req = {
        "range_a": json.loads(a.read_text(encoding="utf-8")),
        "range_b": json.loads(b.read_text(encoding="utf-8")),
    }
    res = compare_ranges(store, cfg, req)
    stats_table(res).to_csv(out, index=False, encoding="utf-8-sig")
    for s in res["insights_ja"]:
        typer.echo(s)
    typer.echo(f"出力: {out}")


@app.command()
def diff(
    base: Annotated[str, typer.Option()],
    new: Annotated[str, typer.Option()],
) -> None:
    """2 つの run の差分（P4。現状は施設の新設・廃止・名称変更のみ）。"""
    typer.echo("差分更新は P4 で実装予定です（change_event テーブルは作成済み）。")
    raise typer.Exit(code=2)


@app.command()
def regions() -> None:
    """登録済みの地域プリセットを表示する。"""
    cfg = load_config()
    for r in cfg.regions():
        typer.echo(f"{r['id']:16s} {r.get('label', '')}")


if __name__ == "__main__":
    app()
