# Track SVG Tool (TrackFactory)

TrackFactory is a Python CLI that discovers, ingests, normalizes, QA checks, and renders track maps as SVGs with a canonical geometry JSON for downstream use. It prefers Wikimedia SVGs when available and falls back to OSM geometry.

Primary sources (ranked, when available):
- Wikimedia Commons SVGs (preferred)
- OpenStreetMap raceway geometry
- PDFs (stubbed in v1)

## Features (v1)
- Search and rank track candidates from Commons and OSM
- Ingest to a canonical centerline model
- QA checks (closure error, self-intersection, point count)
- Render a stylable SVG plus a debug SVG
- Write a canonical JSON + provenance + sources list
- Optional LLM-assisted variant selection and naming

## Requirements
- Python 3.13
- Node.js 18+ (for the web viewer in `web/`)
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

Build all SVG variants (per candidate) into per-variant configs:
```powershell
python -m trackfactory.cli build-variants "Nurburgring"
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

Batch from a list file (one query per line):
```powershell
python -m trackfactory.cli batch --list top10.txt
```

Enable verbose logging:
```powershell
python -m trackfactory.cli build "Nurburgring Nordschleife" --verbose
```

## Output Layout
```
tracks/
  <slug>/
    variants.json        # optional, LLM output
    llm-raw.json         # optional, raw LLM prompt/response
    <config>/
      canonical.json
      track.svg
      centerline.svg
      source_wiki.svg
      debug.svg
      sources.json
```

Notes on outputs:
- For Wikimedia SVG sources, `track.svg` is the original SVG (high fidelity) and `source_wiki.svg` is the raw download.
- For non-Wikimedia sources, `track.svg` is rendered from the canonical centerline.
- `centerline.svg` is always rendered from canonical geometry.
- `sources.json` is written per config to avoid overwriting variants.

## LLM-assisted variants (optional)
Set one of these environment variables to enable variant ranking and naming:
- `TRACKFACTORY_LLM_CMD`
- `TRACKFACTORY_LLM_CMD_CLAUDE`
- `TRACKFACTORY_LLM_CMD_CODEX`

Examples:
```powershell
setx TRACKFACTORY_LLM_CMD_CLAUDE "claude -p --model opus"
setx TRACKFACTORY_LLM_CMD_CODEX "codex exec -m gpt-5.2-codex"
```

The LLM should return JSON:
```json
{
  "ordered_titles": ["File:...Road Course 2024.svg"],
  "variants": [
    {
      "title": "File:...Road Course 2024.svg",
      "slug": "road-course-2024",
      "variant_type": "road_course",
      "year": 2024,
      "notes": "24-hour layout"
    }
  ]
}
```

## Tests
```powershell
pytest
```

## Web viewer
The React app in `web/` loads tracks from `tracks/` and can render/inspect canonical geometry and SVGs.

Dev:
```powershell
cd web
npm install
npm run dev
```

## Notes
- Wikimedia Commons can return 403 without a proper User-Agent; this tool sets a policy-compliant User-Agent.
- OSM ingestion uses Overpass and projects to UTM for local planar units.
- PDFs are stubbed in v1.
