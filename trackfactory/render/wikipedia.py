from __future__ import annotations

import math


def _bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def render_wikipedia_svg(points: list[tuple[float, float]]) -> str:
    if len(points) < 2:
        raise ValueError("Not enough points to render SVG")
    min_x, min_y, max_x, max_y = _bbox(points)
    width = max_x - min_x
    height = max_y - min_y
    max_dim = max(width, height)
    pad = max_dim * 0.08 + 1.0
    view_min_x = min_x - pad
    view_min_y = min_y - pad
    view_w = width + pad * 2
    view_h = height + pad * 2
    stroke_width = max_dim * 0.02

    d_parts = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    for x, y in points[1:]:
        d_parts.append(f"L {x:.3f} {y:.3f}")
    d_parts.append("Z")
    d = " ".join(d_parts)

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_min_x:.3f} {view_min_y:.3f} '
        f'{view_w:.3f} {view_h:.3f}" width="100%" height="100%">'
        f'<path d="{d}" fill="none" stroke="black" stroke-width="{stroke_width:.3f}" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        "</svg>"
    )
    return svg
