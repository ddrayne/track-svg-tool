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


def _merge_way_segments(
    ways: list[list[tuple[float, float]]],
    max_gap: float = 0.0015,
) -> list[tuple[float, float]]:
    """Merge way segments into a single polyline.

    First builds connected clusters via exact endpoint matching, then bridges
    between clusters using proximity within *max_gap* degrees (~167 m).
    Tries multiple seeds (one per cluster) and returns the longest result.
    """
    if not ways:
        return []
    ways = [w for w in ways if len(w) >= 2]
    if not ways:
        return []

    def key(pt: tuple[float, float]) -> tuple[float, float]:
        return (round(pt[0], 6), round(pt[1], 6))

    def _extend_exact(current: list, used: set) -> list:
        """Greedily extend *current* chain by exact endpoint matching."""
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

    # Phase 1: build clusters via exact matching from each unused way
    clusters: list[tuple[list, set]] = []
    globally_used: set[int] = set()
    for seed_idx in range(len(ways)):
        if seed_idx in globally_used:
            continue
        used: set[int] = {seed_idx}
        chain = _extend_exact(list(ways[seed_idx]), used)
        clusters.append((chain, used))
        globally_used |= used

    # Sort clusters longest-first so the main circuit is the base chain
    clusters.sort(key=lambda c: _rough_length(c[0]), reverse=True)

    # Phase 2: greedily bridge clusters by proximity
    current = clusters[0][0]
    merged_ids: set[int] = set(clusters[0][1])

    def _nearest_cluster_endpoint(
        target: tuple[float, float],
    ) -> tuple[int, str, float]:
        best_ci, best_end, best_dist = -1, "start", float("inf")
        for ci, (chain, ids) in enumerate(clusters):
            if ids & merged_ids:
                continue
            for end_label, pt in (("start", chain[0]), ("end", chain[-1])):
                d = math.hypot(target[0] - pt[0], target[1] - pt[1])
                if d < best_dist:
                    best_ci, best_end, best_dist = ci, end_label, d
        return best_ci, best_end, best_dist

    while True:
        end_ci, end_end, end_dist = _nearest_cluster_endpoint(current[-1])
        start_ci, start_end, start_dist = _nearest_cluster_endpoint(current[0])

        if end_dist <= max_gap and end_dist <= start_dist:
            chain = clusters[end_ci][0]
            if end_end == "start":
                current.extend(chain)
            else:
                current.extend(list(reversed(chain)))
            merged_ids |= clusters[end_ci][1]
        elif start_dist <= max_gap:
            chain = clusters[start_ci][0]
            if start_end == "end":
                current = chain + current
            else:
                current = list(reversed(chain)) + current
            merged_ids |= clusters[start_ci][1]
        else:
            break

    return current


def _select_best_way(payload: dict, query: str) -> list[tuple[float, float]]:
    elements = payload.get("elements", [])
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in elements if el.get("type") == "node"}
    ways_by_id = {el["id"]: el for el in elements if el.get("type") == "way"}
    closed_candidates: list[list[tuple[float, float]]] = []
    open_candidates: list[list[tuple[float, float]]] = []
    all_ways: list[list[tuple[float, float]]] = []
    all_ways_tags: dict[str, str] = {"name": query}
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
        for k in ("highway", "leisure", "route", "type", "sport"):
            if k in tags and k not in all_ways_tags:
                all_ways_tags[k] = tags[k]
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
        merged_score = _score_candidate(all_ways_tags, query, merged)
        # Closed merge of all ways is likely the full circuit — boost it
        if merged[0] == merged[-1] and len(all_ways) > 1:
            merged_score = (merged_score[0] + 3.0, merged_score[1], merged_score[2])
        scored_candidates.append((merged_score, merged))
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
