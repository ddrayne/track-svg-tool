## TrackFactory Agent Notes

This file captures working context and pitfalls for coding agents.

### What This Tool Does
- CLI that resolves tracks from Wikimedia SVGs and OSM, ingests to canonical geometry, and renders SVG outputs.
- Wikimedia SVGs are preferred; OSM is fallback.
- `build-variants` ingests multiple SVG variants into per-config folders.

### What Worked
- Using a policy-compliant User-Agent for Wikimedia downloads:
  - `TrackFactory/0.1 (https://www.trackfactor.com; mailto:info@trackfactory.com)`
- Treating Wikimedia SVGs as the primary output:
  - `track.svg` is the original Wikimedia SVG (high fidelity).
  - `centerline.svg` is a cleaned outline extracted from the source SVG.
- Filtering non-track SVGs by title before prompting the LLM.
- LLM-assisted variant ranking and naming via `TRACKFACTORY_LLM_CMD*`.

### What Did Not Work (and Why)
- Naive SVG path selection (largest/longest) often picked labels or non-track shapes.
- Medial-axis skeletonization from filled SVG outlines produced incorrect shapes.
- OSM-only selection often yielded ovals or partial loops (missing road course detail).
- Wikimedia downloads without a proper User-Agent were blocked (403).

### Key Files
- `trackfactory/cli.py`: CLI, verbose logging, LLM integration, variant builds.
- `trackfactory/ingest/svg_commons.py`: SVG parsing and outline extraction.
- `trackfactory/ingest/osm_geom.py`: OSM ingest and heuristics.
- `trackfactory/resolver/commons.py`: Wikimedia search and scoring.
- `trackfactory/resolver/osm.py`: Overpass + Nominatim bbox search.
- `trackfactory/llm.py`: LLM runner (env-driven).

### Output Layout (Per Track)
```
tracks/
  <slug>/
    variants.json        # optional, LLM output
    llm-raw.json         # optional, raw LLM prompt/response
    <config>/
      canonical.json
      track.svg          # Wikimedia SVG if source is wikimedia
      centerline.svg     # cleaned outline extracted from source SVG
      source_wiki.svg    # original Wikimedia SVG
      debug.svg
      sources.json
```

### LLM Setup
Use one of:
- `TRACKFACTORY_LLM_CMD`
- `TRACKFACTORY_LLM_CMD_CLAUDE`
- `TRACKFACTORY_LLM_CMD_CODEX`

Example:
```
setx TRACKFACTORY_LLM_CMD_CLAUDE "claude -p --model opus"
setx TRACKFACTORY_LLM_CMD_CODEX "codex exec -m gpt-5.2-codex"
```

The LLM must return JSON with:
- `ordered_titles` (string list)
- `variants` (list of objects: title, slug, variant_type, year, notes)

Raw prompts/responses (when verbose) are saved to:
- `tracks/<slug>/llm-raw.json`

### SVG Outline Selection
- Prefer a path with multiple closed subpaths (outer + inner).
- Require reasonable area ratio (outer/inner).
- Prefer black fill + white stroke when present.
- Fallbacks exist but may degrade shape accuracy.

### OSM Notes
- OSM is useful for data mapping and alternate styling.
- Name-only Overpass queries miss tracks; bbox search via Nominatim helps.
- Many tracks lack a full relation; segment stitching can be needed.

### Known Cleanup Task
If `build-variants` ran before filtering, remove junk folders like:
`tracks/<slug>/file-*`.
