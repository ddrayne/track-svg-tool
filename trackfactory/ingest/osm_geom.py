from __future__ import annotations

import math
import re

from trackfactory.geom import close_loop, length as line_length, project_latlon_to_utm, resample_closed
from trackfactory.resolver.model import Provenance, TrackCanonical
from trackfactory.store.provenance import now_utc_iso


def _rough_length(coords: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
    return total


def _name_from_tags(tags: dict) -> str:
    return str(tags.get("name") or "").strip()


def _is_excluded_way(tags: dict) -> bool:
    name = _name_from_tags(tags).lower()
    if "pit lane" in name or "pitlane" in name:
        return True
    if any(token in name for token in ("kart", "flat track", "drag strip", "dragstrip")):
        return True
    if str(tags.get("sport") or "").lower() == "karting":
        return True
    return False


def _score_candidate(tags: dict, query: str, coords: list[tuple[float, float]]) -> tuple[float, int, float]:
    name = _name_from_tags(tags).lower()
    tokens = [token for token in re.split(r"\W+", query.lower()) if token]
    score = 0.0
    if tokens and all(token in name for token in tokens):
        score += 4.0
    if "road course" in name or "road_course" in name:
        score += 3.0
    if any(keyword in name for keyword in ("circuit", "speedway", "raceway", "track")):
        score += 1.0
    if _is_excluded_way(tags):
        score -= 4.0
    if tags.get("route") == "raceway" or tags.get("type") == "route":
        score += 1.0
    if tags.get("highway") == "raceway" or tags.get("leisure") == "track":
        score += 0.5
    closed = 1 if coords and coords[0] == coords[-1] else 0
    length = _rough_length(coords) if coords else 0.0
    return (score, closed, length)


def _coords_for_way(way: dict, nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    if way.get("geometry"):
        return [(pt["lon"], pt["lat"]) for pt in way.get("geometry", [])]
    node_ids = way.get("nodes") or []
    return [nodes[n] for n in node_ids if n in nodes]


def _merge_way_segments(ways: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    if not ways:
        return []
    ways = [w for w in ways if len(w) >= 2]
    if not ways:
        return []
    lengths = [_rough_length(w) for w in ways]
    current = list(ways[lengths.index(max(lengths))])
    used = {lengths.index(max(lengths))}

    def key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0], 6), round(pt[1], 6))

    while True:
        extended = False
        start_key = key(current[0])
        end_key = key(current[-1])
        for idx, coords in enumerate(ways):
            if idx in used:
                continue
            c_start = key(coords[0])
            c_end = key(coords[-1])
            if c_start == end_key:
                current.extend(coords[1:])
                used.add(idx)
                extended = True
                break
            if c_end == end_key:
                current.extend(list(reversed(coords[:-1])))
                used.add(idx)
                extended = True
                break
            if c_end == start_key:
                current = coords[:-1] + current
                used.add(idx)
                extended = True
                break
            if c_start == start_key:
                current = list(reversed(coords[1:])) + current
                used.add(idx)
                extended = True
                break
        if not extended:
            break
    return current


def _select_best_way(payload: dict, query: str) -> list[tuple[float, float]]:
    elements = payload.get("elements", [])
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in elements if el.get("type") == "node"}
    ways_by_id = {el["id"]: el for el in elements if el.get("type") == "way"}
    closed_candidates: list[list[tuple[float, float]]] = []
    open_candidates: list[list[tuple[float, float]]] = []
    all_ways: list[list[tuple[float, float]]] = []
    scored_candidates: list[tuple[tuple[float, int, float], list[tuple[float, float]]]] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags") or {}
        if _is_excluded_way(tags):
            continue
        coords = _coords_for_way(el, nodes)
        if len(coords) < 4:
            continue
        all_ways.append(coords)
        scored_candidates.append((_score_candidate(tags, query, coords), coords))
        if coords[0] == coords[-1]:
            closed_candidates.append(coords)
        else:
            open_candidates.append(coords)
    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags") or {}
        members = el.get("members") or []
        member_ways = []
        for member in members:
            if member.get("type") != "way":
                continue
            way = ways_by_id.get(member.get("ref"))
            if not way:
                continue
            way_tags = way.get("tags") or {}
            if _is_excluded_way(way_tags):
                continue
            coords = _coords_for_way(way, nodes)
            if len(coords) >= 2:
                member_ways.append(coords)
        merged = _merge_way_segments(member_ways)
        if merged:
            scored_candidates.append((_score_candidate(tags, query, merged), merged))
    merged = _merge_way_segments(all_ways)
    if merged:
        scored_candidates.append((_score_candidate({"name": query}, query, merged), merged))
    if scored_candidates:
        return max(scored_candidates, key=lambda item: item[0])[1]
    if closed_candidates:
        return max(closed_candidates, key=_rough_length)
    if open_candidates:
        return max(open_candidates, key=_rough_length)
    if not all_ways:
        raise ValueError("No usable OSM ways found")
    return max(all_ways, key=_rough_length)


def ingest_osm_payload(payload: dict, name: str, config: str = "default") -> TrackCanonical:
    coords = _select_best_way(payload, name)
    projected, _ = project_latlon_to_utm(coords)
    closed = close_loop(projected)
    total_len = line_length(closed)
    step = 2.0
    centerline = resample_closed(closed, step)
    canonical = TrackCanonical(
        track_id="",
        name=name,
        config=config,
        centerline=[[p[0], p[1]] for p in centerline],
        length_m=line_length(centerline),
        provenance=[
            Provenance(
                source_type="osm",
                url="https://overpass-api.de/api/interpreter",
                license="ODbL",
                attribution="OpenStreetMap contributors",
                retrieved_utc=now_utc_iso(),
                notes="Projected to UTM",
            )
        ],
    )
    return canonical
