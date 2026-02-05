import math

from trackfactory.geom.shape_match import shape_similarity


def _circle(cx, cy, r, n=100):
    return [
        (cx + r * math.cos(2 * math.pi * i / n),
         cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def test_identical_shapes_are_similar():
    c = _circle(0, 0, 100)
    result = shape_similarity(c, c)
    assert result["is_similar"] is True
    assert result["coverage"] > 0.95


def test_same_shape_different_scale():
    result = shape_similarity(_circle(0, 0, 10), _circle(500, 500, 5000))
    assert result["is_similar"] is True
    assert result["coverage"] > 0.95


def test_rotated_90_still_similar():
    shape = [(0, 0), (10, 0), (12, 5), (10, 10), (0, 10), (-2, 5), (0, 0)]
    rotated = [(-y, x) for x, y in shape]
    assert shape_similarity(shape, rotated)["is_similar"] is True


def test_partial_section_rejected():
    full = _circle(0, 0, 100)
    partial = full[:25]  # quarter circle
    assert shape_similarity(partial, full)["is_similar"] is False


def test_straight_line_vs_circle_rejected():
    assert shape_similarity(
        [(0, i) for i in range(100)],
        _circle(0, 0, 100),
    )["is_similar"] is False


def test_simple_oval_vs_complex_loop_rejected():
    """A narrow oval should not match a complex track shape even with similar bbox."""
    # Simple narrow oval
    oval = [(50 * math.cos(t), 130 * math.sin(t))
            for t in [2 * math.pi * i / 100 for i in range(100)]]
    # Complex track with switchbacks (same overall bbox)
    complex_track = [
        (0, 0), (30, 10), (50, 0), (40, -30), (20, -50),
        (-10, -80), (-30, -100), (-50, -120), (-40, -100),
        (-20, -80), (10, -60), (30, -40), (50, -20),
        (40, 10), (20, 30), (-10, 50), (-30, 80),
        (-50, 100), (-40, 120), (-20, 130), (0, 100),
        (20, 70), (10, 40), (-10, 20), (0, 0),
    ]
    result = shape_similarity(oval, complex_track)
    assert result["coverage"] < 0.80
