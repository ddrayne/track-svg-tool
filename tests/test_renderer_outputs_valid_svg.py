from trackfactory.render.wikipedia import render_wikipedia_svg


def test_renderer_outputs_valid_svg():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    svg = render_wikipedia_svg(points)
    assert "<svg" in svg
    assert "<path" in svg
