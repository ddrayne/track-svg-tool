from __future__ import annotations

from shapely.geometry import LineString, Point


def _normalize_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Translate centroid to origin, scale so max bbox dimension = 1.0."""
    if len(points) < 2:
        return points
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    shifted = [(x - cx, y - cy) for x, y in points]
    xs2 = [p[0] for p in shifted]
    ys2 = [p[1] for p in shifted]
    span = max(max(xs2) - min(xs2), max(ys2) - min(ys2))
    if span <= 0:
        return shifted
    return [(x / span, y / span) for x, y in shifted]


def _rotate_90(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(-y, x) for x, y in points]


def _aspect_ratio(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 1.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return 1.0
    return max(w / h, h / w)


def _directed_coverage(
    line: LineString,
    reference_points: list[tuple[float, float]],
    threshold: float = 0.10,
) -> float:
    """Fraction of reference_points within *threshold* distance of *line*."""
    if not reference_points:
        return 1.0
    close = sum(1 for p in reference_points if line.distance(Point(p)) < threshold)
    return close / len(reference_points)


def shape_similarity(
    shape_a: list[tuple[float, float]],
    shape_b: list[tuple[float, float]],
) -> dict:
    """Compare two polylines for shape similarity.

    Returns dict with hausdorff distance, coverage fraction, aspect ratio diff,
    and is_similar flag.
    Both shapes are normalized to unit scale before comparison.
    Tries 4 rotations (0/90/180/270) to handle differently-oriented SVGs.

    Coverage measures what fraction of shape_b (reference) points lie within
    distance 0.10 of shape_a (candidate). This catches partial-track SVGs
    where hausdorff alone is too permissive.
    """
    norm_a = _normalize_points(shape_a)
    norm_b = _normalize_points(shape_b)
    ar_a = _aspect_ratio(norm_a)
    ar_b = _aspect_ratio(norm_b)
    ar_diff = abs(ar_a - ar_b) / max(ar_a, ar_b)

    line_b = LineString(norm_b)
    best_hausdorff = float("inf")
    best_coverage = 0.0
    rotated = norm_a
    for _ in range(4):
        line_a = LineString(rotated)
        hd = line_a.hausdorff_distance(line_b)
        cov = _directed_coverage(line_a, norm_b)
        if hd < best_hausdorff:
            best_hausdorff = hd
            best_coverage = cov
        rotated = _normalize_points(_rotate_90(rotated))

    return {
        "hausdorff": best_hausdorff,
        "coverage": best_coverage,
        "aspect_ratio_diff": ar_diff,
        "is_similar": best_hausdorff < 0.25 and ar_diff < 0.35 and best_coverage > 0.85,
    }
