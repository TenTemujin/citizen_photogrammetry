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

Set `MAPILLARY_TOKEN` (free token at mapillary.com/developer) to also measure street-photo density. The Mapillary part is untested against the live API.

## Roadmap

See [implementation_plan.md](implementation_plan.md). Phases: 0 coverage audit, 1 web client, 2 procedural base layer, 3 chunk streaming, 4 imagery-based reconstruction, 5 integration and release.

## License

Not chosen yet. Planned: Apache-2.0 or MIT. See the licensing section of the plan.

## Attribution

See [ATTRIBUTION](ATTRIBUTION).
