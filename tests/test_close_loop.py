from trackfactory.geom.polyline import close_loop


def test_close_loop():
    points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
    closed = close_loop(points)
    assert closed[0] == closed[-1]
