from __future__ import annotations

import re

import httpx

from .model import Candidate


OVERPASS_API = "https://overpass-api.de/api/interpreter"
NOMINATIM_API = "https://nominatim.openstreetmap.org/search"


def _build_overpass_query(query: str) -> str:
    escaped = re.sub(r'(["\\])', r"\\\1", query)
    return (
        '[out:json];'
        f'(way["highway"="raceway"]["name"~"{escaped}",i];'
        f'way["leisure"="track"]["name"~"{escaped}",i];'
        f'relation["type"="route"]["route"="raceway"]["name"~"{escaped}",i];'
        f'relation["route"="raceway"]["name"~"{escaped}",i];'
        f'relation["leisure"="track"]["name"~"{escaped}",i];'
        f'relation["name"~"{escaped}",i];);'
        "out body;"
        ">;"
        "out geom;"
    )


def _build_overpass_bbox_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    return (
        "[out:json];"
        f'(way["highway"="raceway"]({south},{west},{north},{east});'
        f'way["leisure"="track"]({south},{west},{north},{east});'
        f'relation["type"="route"]["route"="raceway"]({south},{west},{north},{east});'
        f'relation["route"="raceway"]({south},{west},{north},{east});'
        f'relation["leisure"="track"]({south},{west},{north},{east});'
        f'relation["type"="multipolygon"]["leisure"="track"]({south},{west},{north},{east}););'
        "out body;"
        ">;"
        "out geom;"
    )


def _score_nominatim_result(result: dict, query: str) -> float:
    score = float(result.get("importance") or 0.0)
    display_name = str(result.get("display_name") or "").lower()
    class_name = str(result.get("class") or "").lower()
    type_name = str(result.get("type") or "").lower()
    keywords = ("circuit", "raceway", "track", "speedway", "motorsport")
    if any(keyword in display_name for keyword in keywords):
        score += 0.3
    if any(keyword in type_name for keyword in keywords):
        score += 0.3
    if class_name in {"leisure", "sport"}:
        score += 0.15
    query_tokens = [token.lower() for token in re.split(r"\W+", query) if token]
    if query_tokens and all(token in display_name for token in query_tokens):
        score += 0.2
    return score


def _best_nominatim_bbox(results: list[dict], query: str) -> tuple[float, float, float, float] | None:
    if not results:
        return None
    best = max(results, key=lambda result: _score_nominatim_result(result, query))
    bbox = best.get("boundingbox") or []
    if len(bbox) != 4:
        return None
    south, north, west, east = (float(value) for value in bbox)
    return (south, west, north, east)


def _search_nominatim(client: httpx.Client, query: str) -> tuple[float, float, float, float] | None:
    query_variants = [
        query,
        f"{query} circuit",
        f"{query} raceway",
        f"{query} track",
        f"{query} motorsport",
    ]
    results: list[dict] = []
    for variant in query_variants:
        params = {"q": variant, "format": "json", "limit": 5}
        resp = client.get(NOMINATIM_API, params=params)
        resp.raise_for_status()
        results.extend(resp.json())
    return _best_nominatim_bbox(results, query)


def _score_overpass_json(payload: dict, query: str) -> float:
    score = 0.6
    elements = payload.get("elements", [])
    if not elements:
        return 0.1
    closed = 0
    name_match = 0
    for el in elements:
        if el.get("type") == "way":
            nodes = el.get("nodes") or []
            if len(nodes) > 3 and nodes[0] == nodes[-1]:
                closed += 1
        tags = el.get("tags") or {}
        name = tags.get("name", "")
        if query.lower() in name.lower():
            name_match += 1
    if closed:
        score += 0.15
    if name_match:
        score += 0.05
    return min(score, 1.0)


def search_osm(query: str, limit: int = 5) -> list[Candidate]:
    candidates: list[Candidate] = []
    with httpx.Client(timeout=60.0) as client:
        bbox = None
        try:
            bbox = _search_nominatim(client, query)
        except httpx.HTTPStatusError:
            bbox = None

        queries: list[tuple[str, str, float]] = [("name", _build_overpass_query(query), 0.0)]
        if bbox:
            queries.append(("bbox", _build_overpass_bbox_query(bbox), 0.08))

        for kind, overpass_query, bonus in queries:
            try:
                resp = client.post(OVERPASS_API, data=overpass_query)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            payload = resp.json()
            score = min(_score_overpass_json(payload, query) + bonus, 1.0)
            candidates.append(
                Candidate(
                    source_type="osm",
                    title=f"OSM {kind} match for '{query}'",
                    url=OVERPASS_API,
                    score=score,
                    payload={
                        "overpass": payload,
                        "overpass_query": overpass_query,
                        "nominatim_bbox": bbox,
                    },
                )
            )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
