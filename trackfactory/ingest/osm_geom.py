from __future__ import annotations

import math

from trackfactory.geom import close_loop, length as line_length, project_latlon_to_utm, resample_closed
from trackfactory.resolver.model import Provenance, TrackCanonical
from trackfactory.store.provenance import now_utc_iso


def _rough_length(coords: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
    return total


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


def _select_best_way(payload: dict) -> list[tuple[float, float]]:
    elements = payload.get("elements", [])
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in elements if el.get("type") == "node"}
    closed_candidates: list[list[tuple[float, float]]] = []
    open_candidates: list[list[tuple[float, float]]] = []
    all_ways: list[list[tuple[float, float]]] = []
    for el in elements:
        if el.get("type") != "way":
            continue
        if el.get("geometry"):
            coords = [(pt["lon"], pt["lat"]) for pt in el.get("geometry", [])]
        else:
            node_ids = el.get("nodes") or []
            coords = [nodes[n] for n in node_ids if n in nodes]
        if len(coords) < 4:
            continue
        all_ways.append(coords)
        if coords[0] == coords[-1]:
            closed_candidates.append(coords)
        else:
            open_candidates.append(coords)
    if closed_candidates:
        return max(closed_candidates, key=_rough_length)
    merged = _merge_way_segments(all_ways)
    if merged:
        return merged
    if open_candidates:
        return max(open_candidates, key=_rough_length)
    if not all_ways:
        raise ValueError("No usable OSM ways found")
    return max(all_ways, key=_rough_length)


def ingest_osm_payload(payload: dict, name: str, config: str = "default") -> TrackCanonical:
    coords = _select_best_way(payload)
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
