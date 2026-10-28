# Citizen Photogrammetry

Open-source pipeline and viewer that turns openly available geodata and street-level imagery into a walkable, chunk-streamed 3D replica of a real place at 1:1 scale.

You pick an area. The pipeline fetches open data with your own credentials, builds 100 m chunks locally, and a client streams them around the player. Where imagery is dense, a street gets photorealistic reconstruction. Elsewhere it degrades gracefully to procedural geometry.

> Status: early planning. Only the Phase 0 coverage script exists.

## Principles

- **Ship code, not data.** Users download data for their own area with their own tokens.
- **Open formats.** glTF, 3D Tiles, `.ply` / `.spz`. Clients are thin and replaceable.
- **Graceful degradation.** Best data available per street; never fail because one source is missing.
- **No proprietary hard dependency.** Google tiles are an optional plugin with a user-supplied key.

## Layout

```
pipeline/    Python pipeline (engine-agnostic). Currently: coverage.py
clients/     Viewers (web first). Not created yet.
docs/        Documentation. Not created yet.
ATTRIBUTION  Data and library credits
implementation_plan.md   Full plan, phases, licensing, risks
```

## Quick start (Phase 0)

Measure open-data coverage for a bounding box (south, west, north, east in decimal degrees):

```
python pipeline/coverage.py 35.658 139.700 35.662 139.705
```

Split OSM buildings of the same bbox into 100 m GeoJSON chunks (Phase 2 skeleton, output in `chunks/`):

```
python pipeline/chunks.py 60.1690 24.9390 60.1730 24.9450
```

View the chunks in the browser (flat ground, extruded footprints, chunks load and unload around the camera; no tokens). From the repo root:

```
python -m http.server 8000
```

Then open http://localhost:8000/clients/web/ , click to look around, WASD to move. The page loads Three.js from unpkg, so it needs internet.

Run everything in one go (coverage, chunks, photo dry run) by double-clicking `run.bat`, or `python pipeline/run_all.py`. It asks for the Mapillary token with hidden input and can save it to `.env` (plain text, git-ignored). Logs, a `manifest.json` and the exported chunks go to `runs/<timestamp>/`. Pass `--download` to actually fetch the photos.

Set `MAPILLARY_TOKEN` (free token at mapillary.com/developer) to also measure street-photo density. The Mapillary part is untested against the live API.

## Roadmap

See [implementation_plan.md](implementation_plan.md). Phases: 0 coverage audit, 1 web client, 2 procedural base layer, 3 chunk streaming, 4 imagery-based reconstruction, 5 integration and release.

## License

Not chosen yet. Planned: Apache-2.0 or MIT. See the licensing section of the plan.

## Attribution

See [ATTRIBUTION](ATTRIBUTION).
