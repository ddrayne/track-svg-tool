from __future__ import annotations

import re

import httpx

from .model import Candidate


OVERPASS_API = "https://overpass-api.de/api/interpreter"


def _build_overpass_query(query: str) -> str:
    escaped = re.sub(r'(["\\])', r"\\\1", query)
    return (
        '[out:json];'
        f'(way["highway"="raceway"]["name"~"{escaped}",i];'
        f'relation["type"="route"]["route"="raceway"]["name"~"{escaped}",i];'
        f'relation["route"="raceway"]["name"~"{escaped}",i];'
        f'relation["name"~"{escaped}",i];);'
        "out body;"
        ">;"
        "out geom;"
    )


def _score_overpass_json(payload: dict, query: str) -> float:
    score = 0.6
    elements = payload.get("elements", [])
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
    overpass_query = _build_overpass_query(query)
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(OVERPASS_API, data=overpass_query)
        resp.raise_for_status()
        payload = resp.json()

    candidates: list[Candidate] = []
    candidates.append(
        Candidate(
            source_type="osm",
            title=f"OSM raceway match for '{query}'",
            url=OVERPASS_API,
            score=_score_overpass_json(payload, query),
            payload={"overpass": payload, "overpass_query": overpass_query},
        )
    )
    return candidates[:limit]
