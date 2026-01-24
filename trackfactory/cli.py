from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from trackfactory.geom import run_qa
from trackfactory.ingest import ingest_osm_payload, ingest_svg
from trackfactory.render import render_debug_svg, render_wikipedia_svg
from trackfactory.resolver import search_commons, search_osm, search_wikipedia
from trackfactory.resolver.model import Candidate, TrackCanonical
from trackfactory.store.tracks import slugify, track_config_dir, write_canonical, write_sources, write_svg


app = typer.Typer(add_completion=False)
console = Console()


def _serialize_candidate(candidate: Candidate) -> dict:
    return candidate.model_dump()


def _combine_candidates(query: str, limit: int) -> list[Candidate]:
    candidates = []
    candidates.extend(search_commons(query, limit=limit))
    candidates.extend(search_wikipedia(query, limit=limit))
    candidates.extend(search_osm(query, limit=limit))
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def _apply_track_id(canonical: TrackCanonical, track_id: str) -> TrackCanonical:
    data = canonical.model_dump()
    data["track_id"] = track_id
    return TrackCanonical(**data)


@app.command()
def search(query: str, limit: int = 5):
    candidates = _combine_candidates(query, limit)
    if not candidates:
        console.print("[red]No candidates found[/red]")
        raise typer.Exit(code=2)
    if not console.is_terminal:
        for idx, candidate in enumerate(candidates):
            print(
                f"{idx}\t{candidate.score:.2f}\t{candidate.source_type}\t{candidate.title}\t{candidate.url}"
            )
        return
    table = Table(title="Candidates")
    table.add_column("#", justify="right")
    table.add_column("score", justify="right")
    table.add_column("type")
    table.add_column("title")
    table.add_column("url")
    for idx, candidate in enumerate(candidates):
        table.add_row(
            str(idx),
            f"{candidate.score:.2f}",
            candidate.source_type,
            candidate.title,
            candidate.url,
        )
    console.print(table)


@app.command()
def build(query: str, out_slug: str | None = None, config: str = "default"):
    candidates = _combine_candidates(query, limit=5)
    if not candidates:
        console.print("[red]No candidates found[/red]")
        raise typer.Exit(code=2)
    canonical = None
    chosen = None
    for candidate in candidates:
        try:
            if candidate.source_type == "wikimedia_svg":
                canonical = ingest_svg(candidate.url, name=query, config=config)
            elif candidate.source_type == "osm":
                payload = candidate.payload.get("overpass")
                if not payload:
                    console.print("[red]Missing Overpass payload for OSM candidate[/red]")
                    continue
                canonical = ingest_osm_payload(payload, name=query, config=config)
            else:
                console.print(f"[red]Unsupported source type: {candidate.source_type}[/red]")
                continue
        except Exception as exc:
            console.print(f"[yellow]Candidate failed ({candidate.source_type}): {exc}[/yellow]")
            canonical = None
        if canonical is not None:
            chosen = candidate
            break

    if canonical is None or chosen is None:
        console.print("[red]No candidates succeeded[/red]")
        raise typer.Exit(code=3)

    track_id = slugify(out_slug or query)
    canonical = _apply_track_id(canonical, track_id)
    centerline = [(p[0], p[1]) for p in canonical.centerline]
    qa, failures = run_qa(centerline)
    canonical.qa = qa

    svg_text = render_wikipedia_svg(centerline)
    debug_svg = render_debug_svg(centerline)

    write_canonical(track_id, config, canonical.model_dump())
    write_svg(track_id, config, "track.svg", svg_text)
    write_svg(track_id, config, "debug.svg", debug_svg)
    write_sources(
        track_id,
        {
            "query": query,
            "chosen": _serialize_candidate(chosen),
            "candidates": [_serialize_candidate(c) for c in candidates],
        },
    )

    if failures:
        console.print(f"[yellow]QA warnings: {', '.join(failures)}[/yellow]")
    console.print(f"[green]Built {track_id} ({config})[/green]")


@app.command()
def ingest(source: str, type: str = typer.Option(..., "--type"), name: str | None = None, out_slug: str | None = None):
    track_name = name or source
    if type == "wikimedia_svg":
        canonical = ingest_svg(source, name=track_name)
    elif type == "osm":
        candidates = search_osm(source, limit=1)
        if not candidates:
            console.print("[red]No OSM candidates found[/red]")
            raise typer.Exit(code=2)
        payload = candidates[0].payload.get("overpass")
        canonical = ingest_osm_payload(payload, name=track_name)
    elif type == "pdf":
        console.print("[red]PDF ingest is not implemented in v1[/red]")
        raise typer.Exit(code=3)
    else:
        console.print(f"[red]Unknown type: {type}[/red]")
        raise typer.Exit(code=3)

    track_id = slugify(out_slug or track_name)
    canonical = _apply_track_id(canonical, track_id)
    centerline = [(p[0], p[1]) for p in canonical.centerline]
    qa, failures = run_qa(centerline)
    canonical.qa = qa
    write_canonical(track_id, canonical.config, canonical.model_dump())
    console.print(f"[green]Ingested {track_id}[/green]")
    if failures:
        console.print(f"[yellow]QA warnings: {', '.join(failures)}[/yellow]")


@app.command()
def qa(track_id: str, config: str = "default"):
    canonical_path = track_config_dir(track_id, config) / "canonical.json"
    if not canonical_path.exists():
        console.print(f"[red]Missing canonical: {canonical_path}[/red]")
        raise typer.Exit(code=2)
    data = json.loads(canonical_path.read_text(encoding="utf-8"))
    centerline = [(p[0], p[1]) for p in data.get("centerline", [])]
    qa_data, failures = run_qa(centerline)
    data["qa"] = qa_data
    canonical_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if failures:
        console.print(f"[yellow]QA warnings: {', '.join(failures)}[/yellow]")
    console.print(f"[green]QA updated for {track_id} ({config})[/green]")


@app.command()
def render(track_id: str, config: str = "default", style: str = "wikipedia"):
    canonical_path = track_config_dir(track_id, config) / "canonical.json"
    if not canonical_path.exists():
        console.print(f"[red]Missing canonical: {canonical_path}[/red]")
        raise typer.Exit(code=2)
    data = json.loads(canonical_path.read_text(encoding="utf-8"))
    centerline = [(p[0], p[1]) for p in data.get("centerline", [])]
    if style != "wikipedia":
        console.print(f"[red]Unsupported style: {style}[/red]")
        raise typer.Exit(code=3)
    svg_text = render_wikipedia_svg(centerline)
    debug_svg = render_debug_svg(centerline)
    write_svg(track_id, config, "track.svg", svg_text)
    write_svg(track_id, config, "debug.svg", debug_svg)
    console.print(f"[green]Rendered {track_id} ({config})[/green]")


@app.command()
def batch(list: str = typer.Option(..., "--list")):
    list_path = Path(list)
    if not list_path.exists():
        console.print(f"[red]Missing list: {list_path}[/red]")
        raise typer.Exit(code=2)
    queries = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    failures = 0
    for query in queries:
        try:
            build(query)
        except typer.Exit as exc:
            code = getattr(exc, "code", getattr(exc, "exit_code", 1))
            if code != 0:
                failures += 1
    if failures:
        console.print(f"[red]Batch completed with {failures} failures[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Batch completed successfully[/green]")


if __name__ == "__main__":
    app()
