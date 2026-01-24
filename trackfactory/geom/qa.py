from __future__ import annotations

import math

from .intersect import is_simple as line_is_simple


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def run_qa(points: list[tuple[float, float]], closure_tolerance: float = 5.0) -> tuple[dict, list[str]]:
    qa: dict = {}
    failures: list[str] = []
    if len(points) < 3:
        failures.append("too_few_points")
        qa["point_count"] = len(points)
        return qa, failures
    qa["point_count"] = len(points)
    qa["closure_error"] = _distance(points[0], points[-1])
    qa["is_simple"] = line_is_simple(points)
    if qa["closure_error"] > closure_tolerance:
        failures.append("closure_error")
    return qa, failures
