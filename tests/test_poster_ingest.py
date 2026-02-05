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
    NURBURGRING_LENGTH_M,
    TrackContour,
    calibrate_scale,
    extract_centerline,
    find_track_contours,
    load_and_threshold,
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
