"""Extract racetrack centerlines from the "Racetracks of the World to Scale" poster.

This module processes a raster image of the poster, which contains ~100 track
outlines nested inside the Nurburgring Nordschleife (the largest contour).
Each track is rendered as a double-line outline (outer + inner boundary),
and tracks do not overlap.

Pipeline:
  1. Threshold the image to isolate dark strokes
  2. Extract contours with hierarchy (RETR_TREE)
  3. Identify the Nurburgring as the largest contour (universe)
  4. Extract child contours as individual tracks
  5. Compute centerlines from outer/inner contour pairs
  6. Calibrate pixel-to-meter scale via Nurburgring (20.832 km)
  7. Optionally OCR legend and on-image labels to name tracks
  8. Match contours to names using proximity + length + shape similarity
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from trackfactory.geom import close_loop, is_simple, length, resample_closed, run_qa, shape_similarity, smooth
from trackfactory.resolver.model import Provenance, TrackCanonical
from trackfactory.store.provenance import now_utc_iso

logger = logging.getLogger(__name__)

# Known Nurburgring Nordschleife length in meters (full combined circuit).
NURBURGRING_LENGTH_M = 20_832.0

# Minimum contour area in pixels to be considered a track (filters noise).
MIN_CONTOUR_AREA_PX = 1000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrackContour:
    """A detected track outline from the poster image."""
    outer: np.ndarray          # Nx1x2 OpenCV contour
    inner: np.ndarray | None   # Nx1x2 OpenCV contour or None for thin strokes
    centroid: tuple[float, float] = (0.0, 0.0)
    area: float = 0.0
    is_universe: bool = False  # True for the Nurburgring (largest contour)


@dataclass
class LegendEntry:
    """A parsed entry from the poster legend."""
    name: str
    length_km: float | None = None
    location: str | None = None
    bbox: tuple[int, int, int, int] | None = None  # x, y, w, h


@dataclass
class LabelDetection:
    """A text label detected on the image near a track contour."""
    text: str
    centroid: tuple[float, float] = (0.0, 0.0)
    confidence: float = 0.0


@dataclass
class MatchedTrack:
    """A contour matched to a name with confidence score."""
    contour: TrackContour
    centerline: list[tuple[float, float]]
    length_px: float = 0.0
    length_m: float = 0.0
    name: str = ""
    location: str | None = None
    confidence: float = 0.0
    match_source: str = ""  # "label", "length", "shape", "unknown"


# ---------------------------------------------------------------------------
# 3a. Image preprocessing
# ---------------------------------------------------------------------------

def load_and_threshold(image_path: str) -> tuple[np.ndarray, np.ndarray]:
    """Load a poster image and produce a clean binary mask of track strokes.

    Returns (grayscale, binary) where binary has white strokes on black.
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Gaussian blur to reduce JPEG compression noise
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    # Binary threshold — dark strokes (< ~80) become white on black
    _, binary = cv2.threshold(blurred, 80, 255, cv2.THRESH_BINARY_INV)
    return gray, binary


# ---------------------------------------------------------------------------
# 3b. Contour extraction with hierarchy
# ---------------------------------------------------------------------------

def find_track_contours(
    binary: np.ndarray,
    legend_y_cutoff: float | None = None,
) -> list[TrackContour]:
    """Find track contours using hierarchy-based analysis.

    The poster layout nests all track outlines inside the Nurburgring
    Nordschleife (the largest contour). This function:
      1. Finds the universe contour (largest by area)
      2. Walks the hierarchy to find its inner boundary
      3. Collects all children of that inner boundary as candidate tracks

    Args:
        binary: Binary image (white strokes on black).
        legend_y_cutoff: Y-coordinate below which contours are legend, not
            tracks. If None, defaults to 85% of image height.

    Returns:
        List of TrackContour objects (including the Nurburgring itself).
    """
    if legend_y_cutoff is None:
        legend_y_cutoff = binary.shape[0] * 0.85

    contours, hierarchy = cv2.findContours(
        binary, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE,
    )
    if hierarchy is None or len(contours) == 0:
        return []

    hierarchy = hierarchy[0]  # shape: (N, 4) — [next, prev, child, parent]

    # Find the universe contour — largest by area
    areas = [cv2.contourArea(c) for c in contours]
    universe_idx = int(np.argmax(areas))

    # The universe contour's first child is the Nurburgring inner boundary
    universe_child_idx = hierarchy[universe_idx][2]
    if universe_child_idx < 0:
        logger.warning("Universe contour has no children — image may not be a valid poster")
        return []

    # Build the Nurburgring as a TrackContour (universe outer + first child as inner)
    nurburgring = TrackContour(
        outer=contours[universe_idx],
        inner=contours[universe_child_idx],
        centroid=_contour_centroid(contours[universe_idx]),
        area=areas[universe_idx],
        is_universe=True,
    )

    results: list[TrackContour] = [nurburgring]

    # All children of the universe's inner boundary are candidate tracks.
    # Each track appears as an outer contour with an optional inner contour.
    track_outer_idx = hierarchy[universe_child_idx][2]
    while track_outer_idx >= 0:
        outer = contours[track_outer_idx]
        area = areas[track_outer_idx]

        # Skip noise and legend-region contours
        cx, cy = _contour_centroid(outer)
        if area < MIN_CONTOUR_AREA_PX or cy > legend_y_cutoff:
            track_outer_idx = hierarchy[track_outer_idx][0]  # next sibling
            continue

        # Look for an inner contour (first child)
        inner_idx = hierarchy[track_outer_idx][2]
        inner = contours[inner_idx] if inner_idx >= 0 else None

        results.append(TrackContour(
            outer=outer,
            inner=inner,
            centroid=(cx, cy),
            area=area,
        ))

        track_outer_idx = hierarchy[track_outer_idx][0]  # next sibling

    return results


def _contour_centroid(contour: np.ndarray) -> tuple[float, float]:
    """Compute the centroid of an OpenCV contour."""
    M = cv2.moments(contour)
    if M["m00"] == 0:
        pts = contour.reshape(-1, 2)
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())
    return float(M["m10"] / M["m00"]), float(M["m01"] / M["m00"])


# ---------------------------------------------------------------------------
# 3c. Centerline extraction from contour pairs
# ---------------------------------------------------------------------------

def extract_centerline(
    outer: np.ndarray,
    inner: np.ndarray | None,
    num_points: int = 500,
) -> list[tuple[float, float]]:
    """Extract a centerline from an outer/inner contour pair.

    If *inner* is provided, both contours are resampled to *num_points*
    evenly-spaced points, aligned, and midpoints are computed.
    If *inner* is None (thin stroke), the outer contour itself is used
    as the centerline.

    Args:
        outer: Nx1x2 OpenCV contour (outer boundary).
        inner: Nx1x2 OpenCV contour (inner boundary) or None.
        num_points: Number of evenly-spaced sample points.

    Returns:
        List of (x, y) centerline points.
    """
    outer_pts = _resample_contour(outer, num_points)

    if inner is None:
        # Thin stroke — use the outer contour directly
        return close_loop(outer_pts)

    inner_pts = _resample_contour(inner, num_points)
    inner_pts = _align_ring(inner_pts, outer_pts)

    # Compute midpoints
    center = [
        ((ox + ix) / 2, (oy + iy) / 2)
        for (ox, oy), (ix, iy) in zip(outer_pts, inner_pts)
    ]
    return close_loop(center)


def _resample_contour(contour: np.ndarray, num_points: int) -> list[tuple[float, float]]:
    """Resample an OpenCV contour to *num_points* evenly-spaced points."""
    pts = contour.reshape(-1, 2).astype(float)
    points = [(float(p[0]), float(p[1])) for p in pts]
    if len(points) < 3:
        return points

    # Close the loop for length calculation
    loop = close_loop(points)
    total_len = length(loop)
    if total_len == 0:
        return points[:num_points]

    step = total_len / num_points
    return resample_closed(loop, step)


def _align_ring(
    inner: list[tuple[float, float]],
    outer: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Align inner ring to outer ring by finding best rotation + flip.

    Adapted from svg_commons._align_ring — tries rotations and reversals
    to minimize mean distance between corresponding points.
    """
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
            shifted = candidate[offset:] + candidate[:offset]
            score = _mean_distance(outer, shifted)
            if score < best_score:
                best_score = score
                best = shifted
    return best


def _mean_distance(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> float:
    """Average Euclidean distance between corresponding points."""
    n = min(len(a), len(b))
    if n == 0:
        return float("inf")
    return sum(
        math.hypot(a[i][0] - b[i][0], a[i][1] - b[i][1])
        for i in range(n)
    ) / n


# ---------------------------------------------------------------------------
# 3d. Scale calibration
# ---------------------------------------------------------------------------

def calibrate_scale(contours: list[TrackContour]) -> float:
    """Compute meters-per-pixel scale using the Nurburgring.

    The Nurburgring Nordschleife is always the largest contour (the
    "universe" contour). Its known length is 20.832 km.

    Returns:
        meters_per_pixel scale factor.
    """
    nurburgring = None
    for tc in contours:
        if tc.is_universe:
            nurburgring = tc
            break

    if nurburgring is None:
        raise ValueError("No universe contour found — cannot calibrate scale")

    centerline = extract_centerline(nurburgring.outer, nurburgring.inner)
    length_px = length(centerline)

    if length_px == 0:
        raise ValueError("Nurburgring centerline has zero length")

    return NURBURGRING_LENGTH_M / length_px


# ---------------------------------------------------------------------------
# 3e. Legend OCR
# ---------------------------------------------------------------------------

def ocr_legend(image: np.ndarray) -> list[LegendEntry]:
    """OCR the poster legend to extract track names and lengths.

    Requires the ``easyocr`` package (install via ``pip install easyocr``).
    The legend occupies approximately the bottom 15-30% of the image
    (rows ~1560 to ~2280 in the poster).

    Args:
        image: Full poster image (BGR).

    Returns:
        List of parsed LegendEntry objects.
    """
    try:
        import easyocr
    except ImportError:
        logger.warning("easyocr not installed — skipping legend OCR")
        return []

    h, w = image.shape[:2]
    # Legend region: bottom ~30% of the image
    y_start = int(h * 0.70)
    y_end = h
    legend_crop = image[y_start:y_end, :]

    # Preprocess: upscale 2x and sharpen for better OCR
    legend_up = cv2.resize(legend_crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    # Increase contrast
    lab = cv2.cvtColor(legend_up, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_chan = clahe.apply(l_chan)
    legend_up = cv2.cvtColor(cv2.merge([l_chan, a_chan, b_chan]), cv2.COLOR_LAB2BGR)

    reader = easyocr.Reader(["en"], gpu=True)
    results = reader.readtext(legend_up)

    entries: list[LegendEntry] = []
    length_re = re.compile(r"(\d+[.,]\d+)\s*(?:km|mi)", re.IGNORECASE)

    # Group OCR results by spatial proximity (x-coordinate clustering)
    # Each legend entry typically spans 2-4 OCR text boxes vertically
    raw_items: list[dict] = []
    for bbox, text, conf in results:
        if conf < 0.2 or len(text.strip()) < 2:
            continue
        xs = [pt[0] for pt in bbox]
        ys = [pt[1] for pt in bbox]
        raw_items.append({
            "text": text.strip(),
            "x": min(xs),
            "y": min(ys),
            "w": max(xs) - min(xs),
            "h": max(ys) - min(ys),
            "conf": conf,
        })

    # Sort by x (column) then y (row) — the legend is multi-column
    raw_items.sort(key=lambda it: (it["x"] // 200, it["y"]))

    # Parse entries: each entry is a track name followed by metadata lines
    # (location, length). A length pattern anchors the end of an entry.
    # To avoid cross-column concatenation, reset when there's a large
    # x-gap between consecutive items.
    current_name_parts: list[str] = []
    prev_x_bin = -1
    for item in raw_items:
        text = item["text"]
        x_bin = int(item["x"]) // 200

        # Column change — flush accumulated name parts
        if x_bin != prev_x_bin and current_name_parts:
            name = " ".join(current_name_parts).strip()
            if name and len(name) > 3:
                entries.append(LegendEntry(name=name))
            current_name_parts = []
        prev_x_bin = x_bin

        m = length_re.search(text)
        if m:
            # This line contains a length — finalize current entry
            length_str = m.group(1).replace(",", ".")
            try:
                length_km = float(length_str)
            except ValueError:
                length_km = None
            if "mi" in text.lower() and length_km is not None:
                length_km *= 1.60934  # Convert miles to km
            name = " ".join(current_name_parts).strip()
            if not name:
                # The name might be in the same line before the length
                prefix = text[: m.start()].strip()
                if prefix:
                    name = prefix
            if name:
                entries.append(LegendEntry(name=name, length_km=length_km))
            current_name_parts = []
        else:
            # Cap individual name accumulation to prevent blob names
            if len(current_name_parts) >= 3:
                name = " ".join(current_name_parts).strip()
                if name and len(name) > 3:
                    entries.append(LegendEntry(name=name))
                current_name_parts = []
            current_name_parts.append(text)

    # Handle trailing name parts without a length
    if current_name_parts:
        name = " ".join(current_name_parts).strip()
        if name and len(name) > 3:
            entries.append(LegendEntry(name=name))

    return entries


# ---------------------------------------------------------------------------
# 3f. Track label OCR (on-image labels)
# ---------------------------------------------------------------------------

def ocr_track_labels(image: np.ndarray) -> list[LabelDetection]:
    """OCR text labels placed near track contours in the image.

    Args:
        image: Full poster image (BGR).

    Returns:
        List of detected text labels with centroids.
    """
    try:
        import easyocr
    except ImportError:
        logger.warning("easyocr not installed — skipping track label OCR")
        return []

    h = image.shape[0]
    # Track region: top ~70% of the image (below is legend)
    track_crop = image[0 : int(h * 0.70), :]

    reader = easyocr.Reader(["en"], gpu=True)
    results = reader.readtext(track_crop)

    labels: list[LabelDetection] = []
    for bbox, text, conf in results:
        if conf < 0.3 or len(text.strip()) < 2:
            continue
        xs = [pt[0] for pt in bbox]
        ys = [pt[1] for pt in bbox]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        labels.append(LabelDetection(
            text=text.strip(),
            centroid=(cx, cy),
            confidence=conf,
        ))

    return labels


# ---------------------------------------------------------------------------
# 3g. Track identification — match contours to names
# ---------------------------------------------------------------------------

def match_contours_to_names(
    contours: list[TrackContour],
    centerlines: list[list[tuple[float, float]]],
    meters_per_pixel: float,
    legend: list[LegendEntry] | None = None,
    labels: list[LabelDetection] | None = None,
    existing_tracks: dict[str, list[tuple[float, float]]] | None = None,
) -> list[MatchedTrack]:
    """Match extracted contours to track names using multiple signals.

    Signals (in priority order):
      1. **Label proximity**: nearest OCR label to contour centroid
      2. **Length matching**: compare contour length (in meters) to legend lengths
      3. **Shape matching**: compare against existing OSM/SVG tracks

    Args:
        contours: Extracted track contours.
        centerlines: Corresponding centerlines (same order as contours).
        meters_per_pixel: Scale factor from calibrate_scale().
        legend: Parsed legend entries (from ocr_legend).
        labels: Detected track labels (from ocr_track_labels).
        existing_tracks: Dict of track_id -> centerline points for shape matching.

    Returns:
        List of MatchedTrack objects, one per contour.
    """
    legend = legend or []
    labels = labels or []
    existing_tracks = existing_tracks or {}

    results: list[MatchedTrack] = []
    used_legend: set[int] = set()

    for tc, cl in zip(contours, centerlines):
        length_px = length(cl)
        length_m = length_px * meters_per_pixel

        mt = MatchedTrack(
            contour=tc,
            centerline=cl,
            length_px=length_px,
            length_m=length_m,
        )

        # Special case: the Nurburgring universe contour
        if tc.is_universe:
            mt.name = "Nurburgring Nordschleife"
            mt.location = "Nurburg, Germany"
            mt.confidence = 1.0
            mt.match_source = "universe"
            results.append(mt)
            continue

        best_name = ""
        best_confidence = 0.0
        best_source = "unknown"

        # Signal 1: label proximity
        if labels:
            best_label = _find_nearest_label(tc.centroid, labels)
            if best_label is not None:
                dist = math.hypot(
                    tc.centroid[0] - best_label.centroid[0],
                    tc.centroid[1] - best_label.centroid[1],
                )
                # Labels within ~200px are likely associated
                if dist < 200:
                    label_conf = max(0.0, 1.0 - dist / 200) * best_label.confidence
                    if label_conf > best_confidence:
                        best_name = best_label.text
                        best_confidence = label_conf
                        best_source = "label"

        # Signal 2: length matching against legend
        if legend and length_m > 0:
            for li, entry in enumerate(legend):
                if li in used_legend or entry.length_km is None:
                    continue
                entry_m = entry.length_km * 1000
                rel_error = abs(length_m - entry_m) / entry_m
                if rel_error < 0.20:  # Within 20%
                    length_conf = 1.0 - rel_error
                    if length_conf > best_confidence:
                        best_name = entry.name
                        best_confidence = length_conf
                        best_source = "length"

        # Signal 3: shape matching against existing tracks
        if existing_tracks and len(cl) >= 10:
            for track_id, ref_cl in existing_tracks.items():
                if len(ref_cl) < 10:
                    continue
                sim = shape_similarity(cl, ref_cl)
                if sim["is_similar"]:
                    shape_conf = sim["coverage"] * (1.0 - sim["hausdorff"])
                    if shape_conf > best_confidence:
                        best_name = track_id
                        best_confidence = shape_conf
                        best_source = "shape"

        mt.name = best_name
        mt.confidence = best_confidence
        mt.match_source = best_source

        # Mark used legend entry to prevent duplicate matches
        if best_source == "length" and legend:
            for li, entry in enumerate(legend):
                if entry.name == best_name:
                    used_legend.add(li)
                    mt.location = entry.location
                    break

        results.append(mt)

    return results


def _find_nearest_label(
    centroid: tuple[float, float],
    labels: list[LabelDetection],
) -> LabelDetection | None:
    """Find the nearest label to a given centroid."""
    if not labels:
        return None
    return min(
        labels,
        key=lambda lb: math.hypot(
            centroid[0] - lb.centroid[0],
            centroid[1] - lb.centroid[1],
        ),
    )


# ---------------------------------------------------------------------------
# 3h. Top-level orchestration
# ---------------------------------------------------------------------------

def ingest_poster(
    image_path: str,
    verbose: bool = False,
    skip_ocr: bool = False,
    existing_tracks: dict[str, list[tuple[float, float]]] | None = None,
) -> list[MatchedTrack]:
    """Extract all track centerlines from the poster image.

    This is the main entry point for poster ingestion. It runs the full
    pipeline: threshold, contour detection, centerline extraction, scale
    calibration, optional OCR, and track identification.

    Args:
        image_path: Path to the poster image file.
        verbose: If True, log progress details.
        skip_ocr: If True, skip OCR steps (useful for testing without easyocr).
        existing_tracks: Optional dict of track_id -> centerline for shape matching.

    Returns:
        List of MatchedTrack objects with centerlines scaled to meters.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    if verbose:
        logger.info("Loading and thresholding image: %s", image_path)

    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")

    gray, binary = load_and_threshold(str(path))

    if verbose:
        logger.info("Image size: %dx%d", binary.shape[1], binary.shape[0])

    # Step 1: Find track contours
    contours = find_track_contours(binary)
    if verbose:
        logger.info("Found %d track contours", len(contours))

    if not contours:
        return []

    # Step 2: Extract centerlines
    centerlines: list[list[tuple[float, float]]] = []
    for tc in contours:
        cl = extract_centerline(tc.outer, tc.inner)
        centerlines.append(cl)

    # Step 3: Calibrate scale
    meters_per_pixel = calibrate_scale(contours)
    if verbose:
        logger.info("Scale: %.4f meters/pixel", meters_per_pixel)

    # Step 4: OCR (optional)
    legend: list[LegendEntry] = []
    labels: list[LabelDetection] = []
    if not skip_ocr:
        if verbose:
            logger.info("Running legend OCR...")
        legend = ocr_legend(image)
        if verbose:
            logger.info("Found %d legend entries", len(legend))
            logger.info("Running track label OCR...")
        labels = ocr_track_labels(image)
        if verbose:
            logger.info("Found %d track labels", len(labels))

    # Step 5: Match contours to names
    matched = match_contours_to_names(
        contours,
        centerlines,
        meters_per_pixel,
        legend=legend,
        labels=labels,
        existing_tracks=existing_tracks,
    )

    return matched


def matched_to_canonical(
    mt: MatchedTrack,
    meters_per_pixel: float,
    image_path: str,
    config: str = "poster",
) -> TrackCanonical:
    """Convert a MatchedTrack to a TrackCanonical model.

    Scales the pixel-space centerline to meters and runs QA.
    """
    from trackfactory.store.tracks import slugify

    # Scale centerline from pixels to meters
    scaled = [
        (x * meters_per_pixel, y * meters_per_pixel)
        for x, y in mt.centerline
    ]

    # Smooth pixel-level jaggedness from contour detection
    scaled = smooth(scaled, iterations=3)

    # Resample at 2m spacing for consistent density
    total_len = length(scaled)
    if total_len > 0:
        step = max(2.0, total_len / 3000)
        scaled = resample_closed(scaled, step)

    raw_slug = slugify(mt.name) if mt.name else ""
    # Truncate to avoid Windows MAX_PATH issues (260 chars total)
    track_id = raw_slug[:60].rstrip("-")
    qa_data, _ = run_qa(scaled)

    return TrackCanonical(
        track_id=track_id,
        name=mt.name or "unknown",
        config=config,
        centerline=[[p[0], p[1]] for p in scaled],
        length_m=length(scaled),
        qa=qa_data,
        provenance=[
            Provenance(
                source_type="poster",
                url=str(Path(image_path).resolve()),
                license="Fair use / personal reference",
                attribution="Racetracks of the World to Scale poster",
                retrieved_utc=now_utc_iso(),
                notes=f"Extracted from raster; confidence={mt.confidence:.2f}, source={mt.match_source}",
            )
        ],
    )
