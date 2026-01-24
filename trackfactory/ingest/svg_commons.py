from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
from lxml import etree
from svgpathtools import parse_path
from svgpathtools import Path as SvgPath

from trackfactory.geom import close_loop, length as line_length, resample_closed
from trackfactory.resolver.model import Provenance, TrackCanonical
from trackfactory.store.provenance import now_utc_iso


USER_AGENT = "TrackFactory/0.1 (https://www.trackfactor.com; mailto:info@trackfactory.com)"


def _read_svg_bytes(source: str) -> tuple[bytes, str]:
    path = Path(source)
    if path.exists():
        return path.read_bytes(), path.as_posix()
    with httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = client.get(source)
        resp.raise_for_status()
        return resp.content, source


def _parse_style_props(style_text: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for chunk in style_text.split(";"):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        key = key.strip().lower()
        value = value.strip().lower()
        if key:
            props[key] = value
    return props


def _parse_style_block(style_text: str) -> dict[str, dict[str, str]]:
    class_styles: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"\.([a-zA-Z0-9_-]+)\s*\{([^}]+)\}", style_text):
        class_name = match.group(1)
        props = _parse_style_props(match.group(2))
        if props:
            class_styles[class_name] = props
    return class_styles


def _parse_length(value: str | None) -> float:
    if not value:
        return 0.0
    match = re.match(r"([0-9.]+)", value.strip())
    if not match:
        return 0.0
    return float(match.group(1))


def _parse_paint(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered in {"none", "transparent"}:
        return None
    return lowered


def _merge_style_props(
    element: etree._Element, class_styles: dict[str, dict[str, str]]
) -> dict[str, str]:
    props: dict[str, str] = {}
    classes = (element.get("class") or "").split()
    for class_name in classes:
        props.update(class_styles.get(class_name, {}))
    inline_style = element.get("style")
    if inline_style:
        props.update(_parse_style_props(inline_style))
    for attr in ("stroke", "stroke-width", "fill"):
        value = element.get(attr)
        if value:
            props[attr] = value.strip().lower()
    return props


def _parse_viewbox_area(root: etree._Element) -> float:
    viewbox = root.get("viewBox") or root.get("viewbox")
    if viewbox:
        parts = [p for p in re.split(r"[ ,]+", viewbox.strip()) if p]
        if len(parts) == 4:
            try:
                width = float(parts[2])
                height = float(parts[3])
                return abs(width * height)
            except ValueError:
                pass
    width = _parse_length(root.get("width"))
    height = _parse_length(root.get("height"))
    if width and height:
        return abs(width * height)
    return 0.0


def _read_viewbox(root: etree._Element) -> str | None:
    viewbox = root.get("viewBox") or root.get("viewbox")
    if viewbox:
        return viewbox.strip()
    width = root.get("width")
    height = root.get("height")
    if width and height:
        w = _parse_length(width)
        h = _parse_length(height)
        if w and h:
            return f"0 0 {w} {h}"
    return None


def _extract_paths(svg_bytes: bytes) -> tuple[list[dict], float]:
    root = etree.fromstring(svg_bytes)
    class_styles: dict[str, dict[str, str]] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.endswith("style") and element.text:
            class_styles.update(_parse_style_block(element.text))
    viewbox_area = _parse_viewbox_area(root)
    paths: list[dict] = []
    path_by_id: dict[str, dict] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.endswith("path"):
            d = element.get("d")
            if not d:
                continue
            props = _merge_style_props(element, class_styles)
            info = {"path": parse_path(d), "props": props, "from_use": False}
            paths.append(info)
            element_id = element.get("id")
            if element_id:
                path_by_id[element_id] = info
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.endswith("use"):
            href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
            if not href or not href.startswith("#"):
                continue
            ref_id = href[1:]
            ref = path_by_id.get(ref_id)
            if not ref:
                continue
            props = ref["props"].copy()
            props.update(_merge_style_props(element, class_styles))
            paths.append({"path": ref["path"], "props": props, "from_use": True})
    return paths, viewbox_area


def _score_path_info(path_info: dict, viewbox_area: float) -> float:
    path = path_info["path"]
    props = path_info["props"]
    try:
        length = path.length()
    except Exception:
        return 0.0
    if length <= 0:
        return 0.0
    try:
        xmin, xmax, ymin, ymax = path.bbox()
        area = abs((xmax - xmin) * (ymax - ymin))
    except Exception:
        area = 0.0
    area_ratio = min(area / viewbox_area, 1.0) if viewbox_area else 0.0
    stroke_width = _parse_length(props.get("stroke-width"))
    has_stroke = _parse_paint(props.get("stroke")) is not None
    has_fill = _parse_paint(props.get("fill")) is not None
    score = length * (0.4 + area_ratio)
    if has_stroke:
        score *= 1.1
    if stroke_width >= 2.0:
        score *= 1.1
    if stroke_width >= 4.0:
        score *= 1.1
    if not has_stroke and has_fill:
        score *= 0.2
    if area_ratio < 0.02 and viewbox_area:
        score *= 0.1
    elif area_ratio < 0.01:
        score *= 0.2
    if path_info.get("from_use"):
        score *= 1.15
    return score


def _select_primary_path(paths: list[dict], viewbox_area: float) -> object | None:
    if not paths:
        return None
    closed = []
    for info in paths:
        path = info["path"]
        isclosed = getattr(path, "isclosed", None)
        if not isclosed:
            continue
        try:
            if isclosed():
                closed.append(info)
        except AssertionError:
            continue
    candidates = closed or paths
    return max(candidates, key=lambda info: _score_path_info(info, viewbox_area))["path"]


def _is_black(value: str | None) -> bool:
    if not value:
        return False
    value = value.lower()
    return value in {"#000", "#000000", "#010101", "black"}


def _is_white(value: str | None) -> bool:
    if not value:
        return False
    value = value.lower()
    return value in {"#fff", "#ffffff", "white"}


def _select_track_outline_path(paths: list[dict], viewbox_area: float) -> object | None:
    best = None
    best_score = -1.0
    for info in paths:
        path = info["path"]
        props = info["props"]
        if not hasattr(path, "continuous_subpaths"):
            continue
        try:
            subpaths = list(path.continuous_subpaths())
        except Exception:
            continue
        closed = []
        for sub in subpaths:
            try:
                if sub.isclosed():
                    closed.append(sub)
            except Exception:
                continue
        if len(closed) < 2:
            continue
        closed.sort(key=lambda p: _bbox_area(p), reverse=True)
        outer = closed[0]
        inner = closed[1]
        area_outer = _bbox_area(outer)
        area_inner = _bbox_area(inner)
        if area_inner <= 0:
            continue
        area_ratio = area_outer / area_inner
        if area_ratio < 1.05 or area_ratio > 3.0:
            continue
        if viewbox_area:
            outer_ratio = area_outer / viewbox_area
            if outer_ratio < 0.02:
                continue
        score = area_outer
        if viewbox_area:
            score *= min(area_outer / viewbox_area, 1.0) + 0.5
        if _is_black(_parse_paint(props.get("fill"))):
            score *= 1.2
        if _is_white(_parse_paint(props.get("stroke"))):
            score *= 1.1
        if info.get("from_use"):
            score *= 1.15
        if score > best_score:
            best_score = score
            best = path
    return best


def _sample_path(path, count: int) -> list[tuple[float, float]]:
    total = path.length()
    points: list[tuple[float, float]] = []
    if count <= 1 or total <= 0:
        return points
    for i in range(count):
        s = total * (i / (count - 1))
        try:
            t = path.ilength(s)
        except Exception:
            t = i / count
        pt = path.point(t)
        points.append((float(pt.real), float(pt.imag)))
    return points


def _mean_distance(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    if not a or not b:
        return float("inf")
    count = min(len(a), len(b))
    total = 0.0
    for i in range(count):
        dx = a[i][0] - b[i][0]
        dy = a[i][1] - b[i][1]
        total += (dx * dx + dy * dy) ** 0.5
    return total / count


def _shift_points(points: list[tuple[float, float]], offset: int) -> list[tuple[float, float]]:
    if not points:
        return points
    offset = offset % len(points)
    return points[offset:] + points[:offset]


def _align_ring(inner: list[tuple[float, float]], outer: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not inner or not outer:
        return inner
    count = min(len(inner), len(outer))
    inner = inner[:count]
    outer = outer[:count]
    best = inner
    best_score = _mean_distance(outer, inner)
    step = max(count // 120, 1)
    for candidate in (inner, list(reversed(inner))):
        for offset in range(0, count, step):
            shifted = _shift_points(candidate, offset)
            score = _mean_distance(outer, shifted)
            if score < best_score:
                best_score = score
                best = shifted
    return best


def _bbox_area(path) -> float:
    try:
        xmin, xmax, ymin, ymax = path.bbox()
        return abs((xmax - xmin) * (ymax - ymin))
    except Exception:
        return 0.0


def _centerline_from_outline(path, count: int) -> list[tuple[float, float]] | None:
    if not hasattr(path, "continuous_subpaths"):
        return None
    try:
        subpaths = list(path.continuous_subpaths())
    except Exception:
        return None
    closed = []
    for sub in subpaths:
        try:
            if sub.isclosed():
                closed.append(sub)
        except Exception:
            continue
    if len(closed) < 2:
        return None
    closed.sort(key=lambda p: p.length(), reverse=True)
    candidates = closed[:6]
    best_center = None
    best_score = float("inf")
    area_pairs = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a = candidates[i]
            b = candidates[j]
            outer, inner = (a, b) if a.length() >= b.length() else (b, a)
            area_outer = _bbox_area(outer)
            area_inner = _bbox_area(inner)
            area_ratio = (area_outer / area_inner) if area_inner else 0.0
            area_pairs.append((abs(area_ratio - 1.0), area_ratio, outer, inner))
    area_pairs.sort(key=lambda item: item[0])
    for _, area_ratio, outer, inner in area_pairs:
        if area_ratio < 1.02 or area_ratio > 2.0:
            continue
        outer_pts = _sample_path(outer, count)
        inner_pts = _sample_path(inner, count)
        if not outer_pts or not inner_pts:
            continue
        inner_pts = _align_ring(inner_pts, outer_pts)
        try:
            inner_line = LineString(inner_pts)
        except Exception:
            continue
        center = []
        for pt in outer_pts:
            projected = inner_line.project(Point(pt))
            nearest = inner_line.interpolate(projected)
            center.append(((pt[0] + nearest.x) / 2, (pt[1] + nearest.y) / 2))
        score = _mean_distance(center, outer_pts)
        if score < best_score:
            best_score = score
            best_center = center
    if best_center is None:
        return None
    return best_center


def _outline_from_path(path) -> SvgPath | None:
    if not hasattr(path, "continuous_subpaths"):
        return None
    try:
        subpaths = list(path.continuous_subpaths())
    except Exception:
        return None
    closed = []
    for sub in subpaths:
        try:
            if sub.isclosed():
                closed.append(sub)
        except Exception:
            continue
    if not closed:
        return None
    closed.sort(key=lambda p: _bbox_area(p), reverse=True)
    return closed[0]


def extract_outline_svg(svg_bytes: bytes) -> str | None:
    root = etree.fromstring(svg_bytes)
    viewbox = None
    paths, viewbox_area = _extract_paths(svg_bytes)
    if not paths:
        return None
    primary = _select_track_outline_path(paths, viewbox_area)
    if primary is None:
        primary = _select_primary_path(paths, viewbox_area)
    if primary is None:
        return None
    outline = _outline_from_path(primary)
    if outline is None:
        outline = primary
    d = outline.d()
    try:
        xmin, xmax, ymin, ymax = outline.bbox()
        viewbox = f"{xmin:.3f} {ymin:.3f} {xmax - xmin:.3f} {ymax - ymin:.3f}"
    except Exception:
        viewbox = _read_viewbox(root) or "0 0 100 100"
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
        'width="100%" height="100%">'
        f'<path d="{d}" fill="none" stroke="black" stroke-width="5" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
        "</svg>"
    )
    return svg


def ingest_svg(
    source: str, name: str, config: str = "default"
) -> tuple[TrackCanonical, bytes]:
    svg_bytes, resolved_source = _read_svg_bytes(source)
    sha256 = hashlib.sha256(svg_bytes).hexdigest()

    paths, viewbox_area = _extract_paths(svg_bytes)
    primary = _select_primary_path(paths, viewbox_area)
    if primary is None:
        raise ValueError("No SVG paths found in source")

    outline = _outline_from_path(primary)
    if outline is not None:
        raw_points = _sample_path(outline, 3000)
    else:
        raw_points = _centerline_from_outline(primary, 3000)
        if not raw_points:
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
    return canonical, svg_bytes
