import math

from trackfactory.geom.polyline import resample_closed


def test_resample_closed_spacing():
    square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
    resampled = resample_closed(square, step=1.0)
    assert resampled[0] == resampled[-1]
    seg_lengths = [
        math.hypot(resampled[i + 1][0] - resampled[i][0], resampled[i + 1][1] - resampled[i][1])
        for i in range(len(resampled) - 1)
    ]
    avg = sum(seg_lengths) / len(seg_lengths)
    assert 0.7 <= avg <= 1.3
