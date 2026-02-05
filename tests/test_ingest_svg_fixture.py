from pathlib import Path

from trackfactory.ingest.svg_commons import ingest_svg


def test_ingest_svg_fixture():
    repo_root = Path(__file__).resolve().parents[1]
    fixture = repo_root / "Circuit_Nürburgring-2013-Nordschleife.svg"
    assert fixture.exists()
    canonical, _svg_bytes = ingest_svg(str(fixture), name="Nurburgring Nordschleife")
    assert len(canonical.centerline) > 1000
