from __future__ import annotations

from .wikipedia import _bbox


def render_debug_svg(points: list[tuple[float, float]], label_every: int = 200) -> str:
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

    circles = []
    for idx, (x, y) in enumerate(points):
        if idx % label_every == 0:
            circles.append(
                f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{max_dim * 0.002:.3f}" fill="red"/>'
            )
            circles.append(
                f'<text x="{x:.3f}" y="{y:.3f}" font-size="{max_dim * 0.01:.3f}" '
                'fill="blue" dx="2" dy="2">'
                f"{idx}</text>"
            )
    start = points[0]
    circles.append(
        f'<circle cx="{start[0]:.3f}" cy="{start[1]:.3f}" r="{max_dim * 0.006:.3f}" fill="green"/>'
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_min_x:.3f} {view_min_y:.3f} '
        f'{view_w:.3f} {view_h:.3f}" width="100%" height="100%">'
        + "".join(circles)
        + "</svg>"
    )
    return svg
