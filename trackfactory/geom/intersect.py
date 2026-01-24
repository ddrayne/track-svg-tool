from __future__ import annotations

from shapely.geometry import LineString


def is_simple(points: list[tuple[float, float]]) -> bool:
    if len(points) < 4:
        return True
    line = LineString(points)
    return line.is_simple
