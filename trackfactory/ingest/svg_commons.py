from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from lxml import etree
from svgpathtools import parse_path

from trackfactory.geom import close_loop, length as line_length, resample_closed
from trackfactory.resolver.model import Provenance, TrackCanonical
from trackfactory.store.provenance import now_utc_iso


def _read_svg_bytes(source: str) -> tuple[bytes, str]:
    path = Path(source)
    if path.exists():
        return path.read_bytes(), path.as_posix()
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(source)
        resp.raise_for_status()
        return resp.content, source


def _extract_paths(svg_bytes: bytes) -> list:
    root = etree.fromstring(svg_bytes)
    paths = []
    for element in root.iter():
        if element.tag.endswith("path"):
            d = element.get("d")
            if d:
                paths.append(parse_path(d))
    return paths


def _select_primary_path(paths: list) -> object | None:
    if not paths:
        return None
    closed = [p for p in paths if getattr(p, "isclosed", lambda: False)()]
    candidates = closed or paths
    return max(candidates, key=lambda p: p.length())


def _sample_path(path, count: int) -> list[tuple[float, float]]:
    total = path.length()
    points: list[tuple[float, float]] = []
    for i in range(count):
        s = total * (i / count)
        try:
            t = path.ilength(s)
        except Exception:
            t = i / count
        pt = path.point(t)
        points.append((float(pt.real), float(pt.imag)))
    return points


def ingest_svg(source: str, name: str, config: str = "default") -> TrackCanonical:
    svg_bytes, resolved_source = _read_svg_bytes(source)
    sha256 = hashlib.sha256(svg_bytes).hexdigest()

    paths = _extract_paths(svg_bytes)
    primary = _select_primary_path(paths)
    if primary is None:
        raise ValueError("No SVG paths found in source")

    raw_points = _sample_path(primary, 3000)
    closed = close_loop(raw_points)
    total_len = line_length(closed)
    step = max(total_len / 3000, 1.0)
    centerline = resample_closed(closed, step)

    canonical = TrackCanonical(
        track_id="",
        name=name,
        config=config,
        centerline=[[p[0], p[1]] for p in centerline],
        length_m=total_len,
        provenance=[
            Provenance(
                source_type="wikimedia_svg",
                url=resolved_source,
                license=None,
                attribution=None,
                retrieved_utc=now_utc_iso(),
                sha256=sha256,
                notes="scale unknown",
            )
        ],
    )
    return canonical
