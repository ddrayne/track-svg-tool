# Track SVG Tool (TrackFactory)

TrackFactory is a Python CLI that discovers, ingests, normalizes, QA checks, and renders accurate track maps as Wikipedia-style SVGs with a canonical geometry JSON for downstream use.

Primary sources (ranked):
- Wikimedia Commons SVGs
- OpenStreetMap raceway geometry
- PDFs (stubbed in v1)

## Features (v1)
- Search and rank track candidates from Commons and OSM
- Ingest to a canonical centerline model
- QA checks (closure error, self-intersection, point count)
- Render Wikipedia-style SVG plus a debug SVG
- Write a canonical JSON + provenance + sources list

## Requirements
- Python 3.13
- Windows supported (no OS-specific paths)

## Install
```powershell
pip install -e .[dev]
```

## Usage
Search candidates:
```powershell
python -m trackfactory.cli search "Nurburgring Nordschleife"
```

Build a track (resolve + ingest + QA + render + write outputs):
```powershell
python -m trackfactory.cli build "Nurburgring Nordschleife"
```

Direct ingest from a local SVG (fixture example):
```powershell
python -m trackfactory.cli ingest --type wikimedia_svg --source "<path-to-provided-svg>" --name "Nurburgring Nordschleife"
```

Re-run QA:
```powershell
python -m trackfactory.cli qa nurburgring-nordschleife
```

Render from canonical:
```powershell
python -m trackfactory.cli render nurburgring-nordschleife
```

## Output Layout
```
tracks/
  <slug>/
    sources.json
    <config>/
      canonical.json
      track.svg
      debug.svg
```

## Tests
```powershell
pytest
```

## Notes
- Wikimedia Commons can return 403 without a proper User-Agent; this tool sets a User-Agent.
- OSM ingestion uses Overpass and projects to UTM for local planar units.
- PDFs are stubbed in v1.
