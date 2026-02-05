from pathlib import Path

from trackfactory.ingest.svg_commons import ingest_svg


def test_svg_ingest_selects_longest_path(tmp_path: Path):
    svg_content = """<svg xmlns="http://www.w3.org/2000/svg">
    <path d="M 0 0 L 10 0 L 10 10 Z" />
    <path d="M 0 0 L 50 0 L 50 50 Z" />
    </svg>"""
    svg_path = tmp_path / "test.svg"
    svg_path.write_text(svg_content, encoding="utf-8")

    canonical, _svg_bytes = ingest_svg(str(svg_path), name="Test Track")
    assert canonical.centerline
    assert canonical.length_m is not None
    assert canonical.length_m > 100.0
