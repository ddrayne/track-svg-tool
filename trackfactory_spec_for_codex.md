# TrackFactory Spec (for Codex)

## Goal
Build a Python system that can **discover**, **ingest**, **normalize**, **QA**, and **render** accurate track maps as **Wikipedia-style SVGs**, plus a canonical geometry JSON for downstream use.

Primary sources, in rank order:
1. **Wikimedia Commons SVGs** (fast win, shape-first, scale often unknown)
2. **OpenStreetMap (OSM) raceway geometry** (best scale, broad coverage)
3. **Event/series circuit-map PDFs** (authoritative drawings, extraction-heavy; licensing risk flagged)

## Deliverables
For each track + configuration:
- `tracks/<slug>/<config>/canonical.json`
- `tracks/<slug>/<config>/track.svg` (Wikipedia-style)
- `tracks/<slug>/<config>/debug.svg` (optional, for QA visualization)
- `tracks/<slug>/sources.json` (ranked candidates + provenance, license, attribution)

## Non-Goals (v1)
- Perfect pitlane modeling
- Automatic corner naming
- Full width-varying boundaries
- Guaranteed real-world scaling for all Wikimedia SVGs

---

## Repository Layout
```
trackfactory/
  pyproject.toml
  trackfactory/
    __init__.py
    cli.py

    resolver/
      __init__.py
      commons.py
      osm.py
      pdf.py              # stub in v1
      rank.py
      model.py

    ingest/
      __init__.py
      svg_commons.py
      osm_geom.py
      pdf_map.py          # stub in v1

    geom/
      __init__.py
      polyline.py
      project.py
      smooth.py
      qa.py
      intersect.py

    render/
      __init__.py
      wikipedia.py
      debug.py

    store/
      __init__.py
      tracks.py
      provenance.py

  tracks/                 # generated artifacts
  tests/
```

---

## Dependencies (Python)
- CLI: `typer`, `rich`
- HTTP: `httpx`
- Validation/models: `pydantic`
- Geometry: `numpy`, `shapely`
- Projection: `pyproj`
- SVG: `svgpathtools`, `lxml`
- PDFs (later): `pymupdf` (`fitz`), optional `potrace`

Pin versions after first vertical slice passes.

---

## Canonical Data Model (Pydantic)
File: `trackfactory/resolver/model.py`

### `Provenance`
- `source_type`: `"wikimedia_svg" | "osm" | "pdf" | "other"`
- `url`: source URL
- `license`: string or null
- `attribution`: string or null
- `retrieved_utc`: ISO-8601 `...Z`
- `sha256`: optional content hash
- `notes`: optional

### `TrackCanonical`
Required:
- `track_id`: slug
- `name`: string
- `config`: default `"default"`
- `direction`: `"CW" | "CCW" | "BOTH"` default `"BOTH"`
- `centerline`: list of `[x, y]` float pairs in **local planar units (meters if known)**
Optional:
- `left_boundary`, `right_boundary`: list of `[x, y]` (v2)
- `pitlane`: list of polylines (v2)
- `start_finish`: `{x, y, heading_rad}` (v2, can be null in v1)
- `length_m`: float (computed from centerline units)
- `qa`: dict (computed)
- `provenance`: list[Provenance]

---

## CLI Spec (Typer)
File: `trackfactory/cli.py`

Commands:

### `tf search "<query>" [--limit N]`
- Output: ranked candidate list with:
  - index, score, source_type, title, url

### `tf build "<query>" [--out-slug SLUG] [--config NAME]`
Pipeline:
1. resolve candidates
2. pick best
3. ingest to canonical
4. normalize
5. QA
6. render Wikipedia SVG + debug SVG
7. write artifacts to store

Exit codes:
- 2: no candidates
- 3: candidate type unsupported in current build

### `tf ingest --source <url|id> --type <wikimedia_svg|osm|pdf>`
Direct ingest without resolving.

### `tf qa <track_id> [--config NAME]`
Runs QA and overwrites canonical QA section.

### `tf render <track_id> [--config NAME] [--style wikipedia]`
Renders SVGs from canonical.

### `tf batch --list tracks.txt`
Build list of tracks; outputs a summary table and fails if any hard QA checks fail.

---

## Resolver Spec

### Common Resolver Output Type
`Candidate` fields:
- `source_type`
- `title`
- `url` (download URL when applicable)
- `score` float 0..1
- `payload` (source-specific metadata)

### Wikimedia Commons Resolver (`resolver/commons.py`)
Use MediaWiki API:
- search in File namespace for SVGs using `list=search`
- then fetch direct file URL via `prop=imageinfo&iiprop=url`

Heuristics scoring:
- + for titles containing `Circuit`, `track map`, `layout`
- + for categories matching racing circuits (optional in v1)
- base score high (Commons SVG is preferred)

### OSM Resolver (`resolver/osm.py`)
Use Overpass API:
- query `highway=raceway` ways/relations with `name~query`
- in v1, return raw Overpass JSON; in v2, do area-based querying for precision

Heuristics scoring:
- base score medium
- + for closed loops found
- + for longer total length (reject tiny kart tracks when a full circuit expected)

### PDF Resolver (`resolver/pdf.py`)
Stub in v1:
- allow user to provide a PDF URL explicitly to `tf ingest --type pdf`
- discovery via curated hosts deferred

---

## Ingest Spec

### Wikimedia SVG Ingest (`ingest/svg_commons.py`) v1
- Download SVG bytes
- Parse paths via `svgpathtools.svg2paths`
- Pick the primary loop:
  - choose longest closed path by geometric length (fallback: longest path)
- Sample the path into points (`N ~ 2000-5000`)
- Close loop, resample to uniform spacing
- Store as `centerline` (shape-first; units are SVG units)
- Add provenance with sha256 + note "scale unknown"

### OSM Ingest (`ingest/osm_geom.py`) v1
- Take Overpass JSON
- Extract node lat/lon and way geometry
- Build connected components; choose best closed loop
- Project lat/lon to local planar meters via `pyproj`:
  - Use UTM zone inferred from centroid (v1) or Azimuthal Equidistant (v2)
- Resample to uniform spacing (e.g., 1–3 m)
- Store `centerline` in meters
- Add provenance including Overpass query used (store in notes or separate file)

### PDF Ingest (`ingest/pdf_map.py`) v1 stub
- Not implemented; leave scaffolding.

---

## Geometry Core Spec

### `geom/polyline.py`
- `close_loop(points)`
- `length(points)`
- `resample_closed(points, step)`
- `simplify_rdp(points, epsilon)` (optional v1)
- `compute_headings(points)` (optional v2)

### `geom/intersect.py`
- self-intersection detection:
  - v1: shapely `LineString.is_simple`
  - v2: report intersection points (segment sweep or shapely overlay)

### `geom/smooth.py`
- smoothing filter with max-deviation guard:
  - v1: optional Chaikin with limited iterations and a maximum lateral error
  - v2: Savitzky–Golay on arc-length parameterization

### `geom/qa.py`
Compute:
- `closure_error` (distance start/end)
- `is_simple` (no self-intersections)
- `point_count`
- `spacing_stats` (mean/std of segment lengths) (v2)
- `curvature_noise` (v2)
Hard fails (v1):
- closure_error > tolerance (default 5 units) => fail
- empty/too few points => fail

Confidence score (v2):
- weighted sum of: closure, simplicity, spacing variance, curvature noise, length sanity (if known)

---

## Rendering Spec

### Wikipedia-style renderer (`render/wikipedia.py`) v1
Input: centerline polyline
Output: SVG with:
- transparent background
- one path representing loop
- black stroke, round joins/caps
- stroke-width proportional to bbox (e.g., 2% of max dimension)
- viewBox tightly fit with padding (~8% + constant)

### Debug renderer (`render/debug.py`) v1 optional
- plot points + indices every N points
- highlight self-intersection warnings if any
- show start point marker

No colors are mandated except basic legibility.

---

## Store Spec (`store/tracks.py`)
Write:
- `canonical.json` (pydantic dump, indented)
- `track.svg`
- `debug.svg` (optional)
Write track slug directory:
- `tracks/<slug>/sources.json` for candidates + chosen source + hashes

Track ID slug rules:
- lowercase
- non-alphanumerics -> `-`
- collapse multiple `-`
- trim edges

---

## Tests (minimum)
- `test_slugify`
- `test_close_loop`
- `test_resample_closed_spacing`
- `test_commons_search_parsing` (mock HTTP)
- `test_svg_ingest_selects_longest_path` (fixture svg)
- `test_renderer_outputs_valid_svg` (contains `<svg` and `<path`)

---

## First Vertical Slice (Definition of Done)
Command:
- `tf build "Nürburgring Nordschleife"`

Expected:
- successful Commons resolution (or OSM fallback)
- artifacts written:
  - `tracks/nurburgring-nordschleife/default/canonical.json`
  - `tracks/nurburgring-nordschleife/default/track.svg`
- QA fields populated:
  - closure_error
  - is_simple
  - point_count
- SVG renders a single thick black loop.

---

## v2 Roadmap Hooks (leave interfaces ready)
- Start/finish detection and manual overrides
- Boundaries from width model (constant width default)
- Configurations (GP vs National) as separate `config` entries
- PDF vector extraction via PyMuPDF + potrace raster tracing fallback
- Resolver curation file allowing per-track overrides (selected source, bbox, config)
