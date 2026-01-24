from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from trackfactory.geom import run_qa
from trackfactory.ingest import (
    extract_outline_svg,
    ingest_osm_payload,
    ingest_svg,
    svg_mentions_query,
    text_mentions_query,
)
from trackfactory.render import render_debug_svg, render_wikipedia_svg
from trackfactory.resolver import search_commons, search_osm, search_wikipedia
from trackfactory.resolver.model import Candidate, TrackCanonical
from trackfactory.llm import run_llm
from trackfactory.store.tracks import slugify, track_config_dir, write_canonical, write_sources, write_svg


app = typer.Typer(add_completion=False)
console = Console()


@dataclass
class LlmVariantResult:
    ordered_titles: list[str]
    variants: list[dict]


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


def _extract_json_payload(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```json\\s*(\\{.*?\\})\\s*```", text, flags=re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        snippet = text[brace_start : brace_end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


def _is_track_svg_candidate(title: str, query: str) -> bool:
    lowered = title.lower()
    bad_terms = (
        "flag",
        "logo",
        "seal",
        "shield",
        "icon",
        "map of",
        "county",
        "city",
        "state",
        "province",
        "country",
        "wikivoyage",
        "commons logo",
        "symbol",
        "question book",
        "oojs",
        "north america",
    )
    if any(term in lowered for term in bad_terms):
        return False
    track_terms = (
        "track",
        "circuit",
        "speedway",
        "raceway",
        "road course",
        "layout",
        "oval",
        "gp",
        "grand prix",
        "prix",
    )
    tokens = [token for token in re.split(r"\\W+", query.lower()) if token]
    if tokens and all(token in lowered for token in tokens):
        return True
    return any(term in lowered for term in track_terms)


def _llm_prompt_variants(query: str, candidates: list[Candidate]) -> str:
    items = [
        f"- title: {c.title}\n  url: {c.url}"
        for c in candidates
        if c.source_type == "wikimedia_svg"
    ]
    return (
        "You are helping pick track layout variants from Wikimedia SVGs.\n"
        f"Track: {query}\n"
        "Candidates:\n"
        + "\n".join(items)
        + "\n\nReturn JSON with keys:\n"
        '- "ordered_titles": list of titles in preferred order\n'
        '- "variants": list of objects with fields: title, slug, variant_type, year, notes\n'
        "Only return JSON."
    )


def _write_llm_raw(track_id: str, payload: dict) -> Path:
    out_dir = Path(__file__).resolve().parents[2] / "tracks" / track_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "llm-raw.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def _llm_rank_svg_candidates(
    query: str, svg_candidates: list[Candidate], track_id: str | None = None, verbose: bool = False
) -> LlmVariantResult | None:
    if not svg_candidates:
        return None
    prompt = _llm_prompt_variants(query, svg_candidates)
    output = run_llm(prompt)
    if not output:
        return None
    llm_path = None
    if track_id:
        llm_path = _write_llm_raw(
            track_id,
            {
                "query": query,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "prompt": prompt,
                "raw_output": output,
            },
        )
    payload = _extract_json_payload(output)
    if not payload:
        console.print("[yellow]LLM returned invalid JSON; skipping[/yellow]")
        return None
    ordered_titles = payload.get("ordered_titles") or []
    variants = payload.get("variants") or []
    if not isinstance(ordered_titles, list) or not isinstance(variants, list):
        console.print("[yellow]LLM response missing expected keys; skipping[/yellow]")
        return None
    result = LlmVariantResult(ordered_titles=ordered_titles, variants=variants)
    if verbose:
        console.print(f"[blue]LLM ordered_titles[/blue]: {ordered_titles}")
        console.print(f"[blue]LLM variants[/blue]: {len(variants)}")
        if llm_path:
            console.print(f"[blue]LLM raw[/blue]: {llm_path}")
    return result


def _write_variants(track_id: str, variants_payload: dict) -> Path:
    out_dir = Path(__file__).resolve().parents[2] / "tracks" / track_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "variants.json"
    out_path.write_text(json.dumps(variants_payload, indent=2), encoding="utf-8")
    return out_path


def _finalize_build(
    canonical: TrackCanonical,
    track_id: str,
    config: str,
    query: str,
    chosen: Candidate,
    candidates: list[Candidate],
    source_svg: str | None = None,
) -> list[str]:
    canonical = _apply_track_id(canonical, track_id)
    centerline = [(p[0], p[1]) for p in canonical.centerline]
    qa, failures = run_qa(centerline)
    canonical.qa = qa

    svg_text = render_wikipedia_svg(centerline)
    debug_svg = render_debug_svg(centerline)

    write_canonical(track_id, config, canonical.model_dump())
    if chosen.source_type == "wikimedia_svg" and source_svg:
        write_svg(track_id, config, "track.svg", source_svg)
        outline_svg = extract_outline_svg(source_svg.encode("utf-8"))
        if outline_svg:
            write_svg(track_id, config, "centerline.svg", outline_svg)
        else:
            write_svg(track_id, config, "centerline.svg", svg_text)
    else:
        write_svg(track_id, config, "track.svg", svg_text)
    write_svg(track_id, config, "debug.svg", debug_svg)
    write_sources(
        track_id,
        {
            "query": query,
            "chosen": _serialize_candidate(chosen),
            "candidates": [_serialize_candidate(c) for c in candidates],
        },
        config=config,
    )
    return failures


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
def build(query: str, out_slug: str | None = None, config: str = "default", verbose: bool = False):
    candidates = _combine_candidates(query, limit=5)
    if not candidates:
        console.print("[red]No candidates found[/red]")
        raise typer.Exit(code=2)
    if verbose:
        console.print(f"[blue]Candidates[/blue]: {len(candidates)}")
    canonical = None
    chosen = None
    svg_candidates = [c for c in candidates if c.source_type == "wikimedia_svg"]
    filtered_svg = [c for c in svg_candidates if _is_track_svg_candidate(c.title, query)]
    if filtered_svg:
        if verbose and len(filtered_svg) != len(svg_candidates):
            dropped = [c.title for c in svg_candidates if c not in filtered_svg]
            console.print(f"[yellow]Filtered non-track SVGs[/yellow]: {dropped}")
        svg_candidates = filtered_svg
    if verbose:
        console.print(
            "[blue]SVG candidates[/blue]: "
            + (", ".join([c.title for c in svg_candidates]) if svg_candidates else "none")
        )
    llm_result = _llm_rank_svg_candidates(
        query,
        svg_candidates,
        track_id=slugify(out_slug or query),
        verbose=verbose,
    )
    if llm_result and llm_result.ordered_titles:
        title_order = {title: idx for idx, title in enumerate(llm_result.ordered_titles)}
        svg_candidates.sort(key=lambda c: title_order.get(c.title, len(title_order)))
    if svg_candidates:
        road_course = [c for c in svg_candidates if "road course" in c.title.lower()]
        if road_course:
            svg_candidates = road_course + [c for c in svg_candidates if c not in road_course]
        else:
            non_moto = [c for c in svg_candidates if "moto" not in c.title.lower()]
            if non_moto:
                svg_candidates = non_moto + [c for c in svg_candidates if c not in non_moto]
    other_candidates = [c for c in candidates if c.source_type != "wikimedia_svg"]
    ordered_candidates = svg_candidates + other_candidates
    if verbose:
        console.print(
            "[blue]Ordered candidates[/blue]: "
            + ", ".join([f"{c.source_type}:{c.title}" for c in ordered_candidates])
        )
    source_svg = None
    for candidate in ordered_candidates:
        try:
            if candidate.source_type == "wikimedia_svg":
                canonical, source_svg = ingest_svg(candidate.url, name=query, config=config)
                if not svg_mentions_query(source_svg, query) and not text_mentions_query(
                    candidate.title or "", query
                ):
                    raise ValueError("SVG content does not mention track name")
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
            if verbose:
                console.print(
                    f"[green]Chosen[/green]: {candidate.source_type} {candidate.title}"
                )
            break

    if canonical is None or chosen is None:
        console.print("[red]No candidates succeeded[/red]")
        raise typer.Exit(code=3)

    track_id = slugify(out_slug or query)
    source_svg_text = source_svg.decode("utf-8", errors="ignore") if source_svg else None
    failures = _finalize_build(
        canonical,
        track_id,
        config,
        query,
        chosen,
        candidates,
        source_svg=source_svg_text,
    )
    if chosen.source_type == "wikimedia_svg" and source_svg_text:
        write_svg(track_id, config, "source_wiki.svg", source_svg_text)

    if failures:
        console.print(f"[yellow]QA warnings: {', '.join(failures)}[/yellow]")
    console.print(f"[green]Built {track_id} ({config})[/green]")


@app.command()
def build_variants(query: str, out_slug: str | None = None, verbose: bool = False):
    candidates = _combine_candidates(query, limit=12)
    svg_candidates = [c for c in candidates if c.source_type == "wikimedia_svg"]
    if not svg_candidates:
        console.print("[red]No SVG candidates found[/red]")
        raise typer.Exit(code=2)
    track_id = slugify(out_slug or query)
    filtered_svg = [c for c in svg_candidates if _is_track_svg_candidate(c.title, query)]
    if filtered_svg:
        if verbose and len(filtered_svg) != len(svg_candidates):
            dropped = [c.title for c in svg_candidates if c not in filtered_svg]
            console.print(f"[yellow]Filtered non-track SVGs[/yellow]: {dropped}")
        svg_candidates = filtered_svg
    if verbose:
        console.print(f"[blue]SVG candidates[/blue]: {len(svg_candidates)}")
    llm_result = _llm_rank_svg_candidates(query, svg_candidates, track_id=track_id, verbose=verbose)
    if llm_result and llm_result.ordered_titles:
        title_order = {title: idx for idx, title in enumerate(llm_result.ordered_titles)}
        svg_candidates.sort(key=lambda c: title_order.get(c.title, len(title_order)))
    if llm_result and llm_result.variants:
        variants_payload = {
            "query": query,
            "variants": llm_result.variants,
            "candidates": [_serialize_candidate(c) for c in svg_candidates],
        }
        _write_variants(track_id, variants_payload)
    used_configs: set[str] = set()
    failures = 0
    for candidate in svg_candidates:
        config = slugify(candidate.title) or "variant"
        if llm_result and llm_result.variants:
            for variant in llm_result.variants:
                if variant.get("title") == candidate.title and variant.get("slug"):
                    config = slugify(str(variant.get("slug")))
                    break
        if config in used_configs:
            suffix = 2
            while f"{config}-{suffix}" in used_configs:
                suffix += 1
            config = f"{config}-{suffix}"
        used_configs.add(config)
        source_svg = None
        try:
            canonical, source_svg = ingest_svg(candidate.url, name=query, config=config)
            if not svg_mentions_query(source_svg, query) and not text_mentions_query(
                candidate.title or "", query
            ):
                raise ValueError("SVG content does not mention track name")
        except Exception as exc:
            console.print(f"[yellow]Variant failed ({candidate.title}): {exc}[/yellow]")
            failures += 1
            continue
        source_svg_text = source_svg.decode("utf-8", errors="ignore") if source_svg else None
        _finalize_build(
            canonical,
            track_id,
            config,
            query,
            candidate,
            candidates,
            source_svg=source_svg_text,
        )
        if source_svg_text:
            write_svg(track_id, config, "source_wiki.svg", source_svg_text)
        console.print(f"[green]Built {track_id} ({config})[/green]")
    if failures:
        console.print(f"[yellow]Completed with {failures} failures[/yellow]")


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
