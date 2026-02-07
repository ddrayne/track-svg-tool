"""Tests for trackfactory.ingest.poster_raster.

Tests that don't require the actual poster image use synthetic data.
Tests that need the real poster are skipped if the image is not present.
Tests that need easyocr are skipped if the package is not installed.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from trackfactory.ingest.poster_raster import (
    MAX_CONTOUR_AREA_PX,
    MAX_TRACK_LENGTH_M,
    NURBURGRING_LENGTH_M,
    MatchedTrack,
    TrackContour,
    calibrate_scale,
    extract_centerline,
    find_track_contours,
    load_and_threshold,
    match_contours_to_names,
    matched_to_canonical,
)

POSTER_PATH = Path(__file__).resolve().parents[1] / "racetracks-of-the-world-to-scale-01.jpg"
POSTER_AVAILABLE = POSTER_PATH.exists()


def _make_circle_contour(cx: float, cy: float, r: float, n: int = 200) -> np.ndarray:
    """Create a synthetic OpenCV contour (Nx1x2 int32) in the shape of a circle."""
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pts = np.stack([cx + r * np.cos(angles), cy + r * np.sin(angles)], axis=-1)
    return pts.reshape(-1, 1, 2).astype(np.int32)


def _make_binary_with_nested_tracks(
    width: int = 800,
    height: int = 800,
    num_inner: int = 5,
) -> np.ndarray:
    """Create a synthetic binary image mimicking the poster structure.

    Draws a large outer ring (universe/Nurburgring) containing several
    smaller track-like ring pairs.
    """
    binary = np.zeros((height, width), dtype=np.uint8)

    # Universe contour: thick ring centered in the image
    center = (width // 2, height // 2)
    cv2.circle(binary, center, 300, 255, thickness=20)

    # Inner tracks: smaller rings inside the universe
    rng = np.random.default_rng(42)
    for i in range(num_inner):
        angle = 2 * math.pi * i / num_inner
        cx = int(center[0] + 150 * math.cos(angle))
        cy = int(center[1] + 150 * math.sin(angle))
        r = rng.integers(30, 60)
        cv2.circle(binary, (cx, cy), int(r), 255, thickness=8)

    return binary


# ---------------------------------------------------------------------------
# Tests using synthetic data (always runnable)
# ---------------------------------------------------------------------------


def test_centerline_from_synthetic_pair():
    """Midpoint averaging on concentric circles produces a circle at the mean radius."""
    outer = _make_circle_contour(400, 400, 100, n=300)
    inner = _make_circle_contour(400, 400, 80, n=300)

    cl = extract_centerline(outer, inner, num_points=200)

    # Centerline should be roughly circular at radius ~90
    distances = [math.hypot(x - 400, y - 400) for x, y in cl]
    mean_r = sum(distances) / len(distances)
    assert 85 < mean_r < 95, f"Expected radius ~90, got {mean_r:.1f}"


def test_centerline_from_single_contour():
    """When no inner contour is provided, the outer is used directly."""
    outer = _make_circle_contour(200, 200, 50, n=100)
    cl = extract_centerline(outer, None, num_points=100)

    assert len(cl) >= 50
    # Should be roughly circular at radius 50
    distances = [math.hypot(x - 200, y - 200) for x, y in cl]
    mean_r = sum(distances) / len(distances)
    assert 45 < mean_r < 55, f"Expected radius ~50, got {mean_r:.1f}"


def test_scale_calibration_from_synthetic():
    """Scale calibration with a known contour produces a reasonable meters_per_pixel."""
    # Create a "Nurburgring" contour with circumference = 2*pi*R pixels
    R = 500  # radius in pixels
    circumference_px = 2 * math.pi * R  # ~3141.6 px
    expected_mpp = NURBURGRING_LENGTH_M / circumference_px

    outer = _make_circle_contour(600, 600, R, n=1000)
    inner = _make_circle_contour(600, 600, R - 20, n=1000)

    tc = TrackContour(
        outer=outer,
        inner=inner,
        centroid=(600.0, 600.0),
        area=math.pi * R * R,
        is_universe=True,
    )

    mpp = calibrate_scale([tc])
    # Should be close to the expected value (within 20% due to resampling)
    assert abs(mpp - expected_mpp) / expected_mpp < 0.20, (
        f"Expected ~{expected_mpp:.3f} m/px, got {mpp:.3f}"
    )


def test_find_contours_on_synthetic_image():
    """Contour detection finds the universe and inner tracks from a synthetic image."""
    binary = _make_binary_with_nested_tracks(num_inner=5)
    contours = find_track_contours(binary)

    # Should find at least the universe + some inner tracks
    assert len(contours) >= 2, f"Expected >=2 contours, found {len(contours)}"

    # Exactly one should be the universe
    universes = [tc for tc in contours if tc.is_universe]
    assert len(universes) == 1, f"Expected 1 universe, found {len(universes)}"


# ---------------------------------------------------------------------------
# Tests using the actual poster image (skipped if not available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not POSTER_AVAILABLE, reason="Poster image not present")
def test_threshold_produces_clean_binary():
    """Thresholding the poster produces a non-trivial binary image."""
    gray, binary = load_and_threshold(str(POSTER_PATH))

    assert gray.shape == binary.shape
    assert binary.dtype == np.uint8

    # Binary image should have both black and white pixels
    white_fraction = np.count_nonzero(binary) / binary.size
    assert 0.01 < white_fraction < 0.50, (
        f"White pixel fraction {white_fraction:.3f} outside expected range"
    )


@pytest.mark.skipif(not POSTER_AVAILABLE, reason="Poster image not present")
def test_find_contours_count():
    """The poster should contain at least 60 track contours."""
    _, binary = load_and_threshold(str(POSTER_PATH))
    contours = find_track_contours(binary)
    assert len(contours) >= 60, f"Expected >=60 contours, found {len(contours)}"


@pytest.mark.skipif(not POSTER_AVAILABLE, reason="Poster image not present")
def test_nurburgring_is_largest():
    """The Nurburgring (universe contour) should be the largest by area."""
    _, binary = load_and_threshold(str(POSTER_PATH))
    contours = find_track_contours(binary)
    assert len(contours) > 0

    universe = [tc for tc in contours if tc.is_universe]
    assert len(universe) == 1
    nurburgring = universe[0]

    # Universe should have the largest area
    max_area = max(tc.area for tc in contours)
    assert nurburgring.area == max_area


@pytest.mark.skipif(not POSTER_AVAILABLE, reason="Poster image not present")
def test_scale_calibration_reasonable():
    """Meters-per-pixel from the poster should be in a plausible range.

    The poster is roughly 3000px wide. The Nurburgring centerline is ~20.8km.
    At poster scale, the Nurburgring centerline might span ~1500-3000px,
    giving roughly 7-14 meters/pixel.
    """
    _, binary = load_and_threshold(str(POSTER_PATH))
    contours = find_track_contours(binary)
    mpp = calibrate_scale(contours)

    # Plausible range for this poster
    assert 1.0 < mpp < 50.0, f"meters_per_pixel={mpp:.3f} outside plausible range"


@pytest.mark.skipif(not POSTER_AVAILABLE, reason="Poster image not present")
def test_legend_ocr_finds_entries():
    """OCR on the legend should find at least some entries (if easyocr is installed)."""
    try:
        import easyocr  # noqa: F401
    except ImportError:
        pytest.skip("easyocr not installed")

    from trackfactory.ingest.poster_raster import ocr_legend

    image = cv2.imread(str(POSTER_PATH))
    entries = ocr_legend(image)
    assert len(entries) >= 20, f"Expected >=20 legend entries, found {len(entries)}"


# ---------------------------------------------------------------------------
# Tests for spurious contour filtering, dedup, fallback, normalization
# ---------------------------------------------------------------------------


def test_spurious_contours_filtered():
    """Oversized blobs inside the universe are filtered by MAX_CONTOUR_AREA_PX."""
    binary = np.zeros((800, 800), dtype=np.uint8)

    # Universe: large outer ring
    cv2.circle(binary, (400, 400), 350, 255, thickness=15)

    # Normal track: small ring inside universe
    cv2.circle(binary, (300, 300), 40, 255, thickness=6)

    # Oversized blob: filled region that exceeds MAX_CONTOUR_AREA_PX
    cv2.circle(binary, (500, 400), 200, 255, thickness=-1)  # filled, area ~125K px

    contours = find_track_contours(binary)

    # Universe should be found
    universes = [tc for tc in contours if tc.is_universe]
    assert len(universes) == 1

    # Non-universe contours should NOT include the oversized blob
    non_universe = [tc for tc in contours if not tc.is_universe]
    for tc in non_universe:
        assert tc.area <= MAX_CONTOUR_AREA_PX, (
            f"Oversized contour (area={tc.area:.0f}) was not filtered"
        )


def test_name_deduplication():
    """When two contours match the same name, only the higher-confidence one keeps it."""
    # Create two simple track contours
    outer1 = _make_circle_contour(200, 200, 50, n=100)
    outer2 = _make_circle_contour(400, 400, 60, n=100)

    tc1 = TrackContour(outer=outer1, inner=None, centroid=(200, 200), area=7854)
    tc2 = TrackContour(outer=outer2, inner=None, centroid=(400, 400), area=11310)

    cl1 = [(float(p[0][0]), float(p[0][1])) for p in outer1]
    cl2 = [(float(p[0][0]), float(p[0][1])) for p in outer2]

    # Both will shape-match the same reference (a circle at same scale)
    ref_circle = [(200 + 55 * math.cos(a), 200 + 55 * math.sin(a))
                  for a in [i * 2 * math.pi / 100 for i in range(100)]]

    existing = {"test-circuit": ref_circle}

    results = match_contours_to_names(
        [tc1, tc2], [cl1, cl2],
        meters_per_pixel=1.0,
        existing_tracks=existing,
    )

    # At most one contour should have the name "test-circuit"
    named = [mt for mt in results if mt.name == "test-circuit"]
    assert len(named) <= 1, f"Duplicate name: {len(named)} contours claimed 'test-circuit'"


def test_fallback_naming():
    """Contours with no matching signals get 'poster-track-NN' fallback names."""
    outer = _make_circle_contour(200, 200, 50, n=100)
    tc = TrackContour(outer=outer, inner=None, centroid=(200, 200), area=7854)
    cl = [(float(p[0][0]), float(p[0][1])) for p in outer]

    # No labels, no legend, no existing tracks
    results = match_contours_to_names(
        [tc], [cl],
        meters_per_pixel=1.0,
    )

    assert len(results) == 1
    mt = results[0]
    assert mt.name.startswith("poster-track-"), f"Expected fallback name, got '{mt.name}'"
    assert mt.match_source == "fallback"
    assert mt.confidence == 0.0


def test_coordinate_normalization():
    """matched_to_canonical translates coords to origin and flips Y."""
    outer = _make_circle_contour(500, 600, 40, n=100)
    tc = TrackContour(outer=outer, inner=None, centroid=(500, 600), area=5027)
    cl = [(float(p[0][0]), float(p[0][1])) for p in outer]

    mt = MatchedTrack(
        contour=tc,
        centerline=cl,
        length_px=251.0,
        length_m=251.0,
        name="test-track",
        confidence=0.5,
        match_source="shape",
    )

    canonical = matched_to_canonical(mt, meters_per_pixel=1.0, image_path="test.jpg")

    xs = [p[0] for p in canonical.centerline]
    ys = [p[1] for p in canonical.centerline]

    # Minimum x and y should be near 0 (translated to origin)
    assert min(xs) >= -1.0, f"min x = {min(xs):.1f}, expected near 0"
    assert min(ys) >= -1.0, f"min y = {min(ys):.1f}, expected near 0"

    # Y should be flipped — originally centered at y=600, now should be near 0..~80
    assert max(ys) < 200, f"max y = {max(ys):.1f}, expected Y-flipped range"
