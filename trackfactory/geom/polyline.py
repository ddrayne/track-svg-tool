from __future__ import annotations

import math


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def close_loop(points: list[tuple[float, float]], tol: float = 1e-6) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    if _dist(points[0], points[-1]) <= tol:
        return points
    return points + [points[0]]


def length(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    return sum(_dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def resample_closed(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    loop = close_loop(points)
    seg_lengths = [_dist(loop[i], loop[i + 1]) for i in range(len(loop) - 1)]
    total = sum(seg_lengths)
    if total == 0:
        return loop
    sample_count = max(3, int(round(total / step)))
    samples: list[tuple[float, float]] = []
    cumulative = 0.0
    seg_index = 0
    for i in range(sample_count):
        target = total * (i / sample_count)
        while seg_index < len(seg_lengths) and cumulative + seg_lengths[seg_index] < target:
            cumulative += seg_lengths[seg_index]
            seg_index += 1
        if seg_index >= len(seg_lengths):
            samples.append(loop[-1])
            continue
        start = loop[seg_index]
        end = loop[seg_index + 1]
        seg_len = seg_lengths[seg_index]
        if seg_len == 0:
            samples.append(start)
            continue
        t = (target - cumulative) / seg_len
        samples.append((start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1])))
    return close_loop(samples)
