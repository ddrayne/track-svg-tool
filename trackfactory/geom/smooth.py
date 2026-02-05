from __future__ import annotations


def smooth(
    points: list[tuple[float, float]],
    iterations: int = 3,
) -> list[tuple[float, float]]:
    """Smooth a polyline using Chaikin corner-cutting.

    Each iteration replaces every segment AB with two new points at 25% and 75%
    along the segment, producing a progressively smoother curve.

    If the polyline is closed (first point == last point), the loop property
    is preserved after smoothing.
    """
    if len(points) < 3 or iterations < 1:
        return points

    closed = (
        len(points) >= 2
        and abs(points[0][0] - points[-1][0]) < 1e-9
        and abs(points[0][1] - points[-1][1]) < 1e-9
    )

    pts = list(points)
    for _ in range(iterations):
        new: list[tuple[float, float]] = []
        n = len(pts)
        end = n if closed else n - 1
        for i in range(end):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % n]
            new.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
            new.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
        if closed and new:
            new.append(new[0])
        pts = new

    return pts
