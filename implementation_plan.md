# Implementation Plan: Open-Source Walkable Real-World Cities from Open Data

## Goal

An open-source pipeline and viewer that turns openly available geodata and street-level imagery into a walkable, chunk-streamed 3D replica of a real place at 1:1 scale. Users pick an area; the pipeline fetches open data with their own credentials, builds chunks locally, and a client streams them around the player. Where imagery is dense, a street gets photorealistic reconstruction. Elsewhere it degrades gracefully to procedural geometry.

## Principles

- **Ship code, not data.** The repository contains the pipeline and clients only. Each user downloads data for their own area with their own tokens and generates chunks locally. This sidesteps share-alike obligations on redistributed derived data.
- **Open formats everywhere.** glTF, 3D Tiles, `.ply` / `.spz`. Clients are thin and replaceable.
- **Graceful degradation.** Use the best data available per street; never fail because one source is missing.
- **No proprietary hard dependency.** Google tiles are an optional plugin with a user-supplied API key.
- **Adaptive and on-demand.** Never bulk-download. Fetch and bake only what the player can see or will reach soon, within user-set bandwidth, disk, RAM, and VRAM budgets.
- **Permissive dependencies only.** Avoid non-commercial or non-open licenses in the core.

## Licensing

Verify every item below against the current license text before publishing; this list is from working knowledge, not a legal review.

- **Project code:** Apache-2.0 (or MIT). AGPL only if hosted services must also open their code.
- **OpenStreetMap:** ODbL, attribution required, share-alike on derived databases.
- **Overture Maps:** license varies by theme; check per theme.
- **Mapillary imagery:** CC-BY-SA 4.0. Attribution required; share-alike applies to redistributed derivatives. Use a user-supplied API token.
- **Panoramax / KartaView:** open licenses; check per instance.
- **Copernicus DEM, national LiDAR and 3D city datasets (e.g. Tokyo PLATEAU, Helsinki, NYC, Berlin, Vienna, 3D BAG, Switzerland):** open, each with its own attribution terms.
- **Google Photorealistic 3D Tiles (optional plugin only):** streaming with the user's own key and required attribution. No bundling, caching, mesh extraction, or ML training. Never in the repo or in shipped chunks. Google Street View bulk extraction is prohibited and is not used.
- **Libraries:** prefer gsplat / Nerfstudio (Apache-2.0), COLMAP and OpenSfM (BSD). Do **not** use the original Inria Gaussian Splatting code in the core (non-commercial license).
- Remove people, faces, and license plates from any imagery used.
- Generate attribution credits automatically into each chunk's metadata and an `ATTRIBUTION` file.

## Architecture

```
repo/
  pipeline/      Python. Fetch -> build -> bake chunks. Engine-agnostic.
  clients/
    web/         CesiumJS (first client)
    godot/       later, community contribution
    unreal/      later, optional
  docs/
  ATTRIBUTION
```

```
Open sources                          Pipeline (local, per user)                 Client
------------                          --------------------------                 ------
Copernicus DEM / LiDAR  ------------> terrain
OSM / Overture / city 3D datasets --> buildings, roads  --+
OpenAerialMap / ortho  -------------> aerial textures  ---+--> chunk bake --> 3D Tiles / glTF --> streaming grid
Mapillary / Panoramax / KartaView --> coverage analysis ----+   (100 m,         + splats (.spz)    + async load
                                      -> SfM -> 3DGS         3 LODs)                              + near-only collision
                                      -> or facade texturing                                      + disk cache
                                                                                                  + optional Google plugin
```

- Chunk size: 100 m, georeferenced; chunk origin stored in global double coordinates (UTM or ECEF), positions inside a chunk in local float.
- LOD0 (near): detailed meshes, collision, splats where reconstructed.
- LOD1 (mid): simplified meshes, no collision.
- LOD2 (far): massing and terrain only.
- Splats are split per chunk, compressed, with point-count LOD.

## Adaptive streaming (Flight Simulator-style, without a cloud backend)

Microsoft Flight Simulator processes heavy data on its own cloud servers. This project has no such backend, so the work is split by cost:

- **At runtime (cheap, on demand):** terrain, procedural buildings (tiers B-D), facade texture projection, reading pre-built tiles. Runs in worker threads, time-sliced per frame.
- **Precomputed (expensive):** SfM and splat training (tier A), and conversion of large open meshes to 3D Tiles. Done offline, per area, then cached or published as static tiles.

Mechanisms:
1. **Cloud-optimized formats with HTTP range requests** (3D Tiles, PMTiles, COG): fetch only the bytes for visible tiles.
2. **Hierarchical LOD by screen-space error**: distant areas cost almost nothing.
3. **Prefetch along the movement vector**, with a priority queue by distance and visibility.
4. **Progressive refinement**: show a cheap tier immediately, upgrade in place when better data arrives.
5. **Hard budgets**: bandwidth cap, LRU disk cache with a size limit, RAM and VRAM caps, triangle and gaussian counts.
6. **Frame-time feedback**: if frame time exceeds the target, raise the screen-space error threshold and shrink the load radius automatically; relax when there is headroom.
7. **Persistent local cache**: a revisited area costs nothing. Offline mode uses the cache only.

Open question: large open meshes (for example Helsinki, about 243 GB as OBJ) cannot be range-requested. They must be converted to 3D Tiles and hosted as static files by someone. First check whether the city already publishes a ready tileset and consume it directly; otherwise decide on community-hosted static storage. Not yet verified.

## Per-street quality tiers (automatic degradation)

1. **Tier A, dense imagery:** cluster photos, run OpenSfM or COLMAP, train a splat (or reconstruct a mesh) for the segment.
2. **Tier B, medium imagery:** keep procedural buildings and project street photos onto facades as textures.
3. **Tier C, no imagery:** procedural buildings with generic textures per building type.
4. **Tier D, no building data:** extruded OSM footprints with default heights.

The pipeline measures photo density per street segment and assigns the tier automatically. The assignment is stored in chunk metadata.

## Phases

### Phase 0: Coverage and data audit (3-5 days)
- Pick a validation city with excellent open data and one target city of personal interest. Candidate: Helsinki, which publishes an open photogrammetry reality mesh (about 7.5 cm/pixel, CC BY 4.0, OBJ); confirm Mapillary density there first. Tokyo PLATEAU is a semantic building model, not a photogrammetry mesh, so it is a second candidate for buildings only.
- Check whether each candidate city already serves its mesh as 3D Tiles, which avoids hosting.
- Script: given a bbox, report OSM / Overture building completeness, LiDAR / DEM availability, and Mapillary photo density per street.
- **Exit criteria:** a coverage report for both cities and a chosen 500 x 500 m test area per city.

### Phase 1: Web client with streaming context (1 week)
- CesiumJS client with a first-person walking controller and collision against terrain.
- Optional Google 3D Tiles layer, enabled only when the user supplies a key.
- **Exit criteria:** walk through the test area in the browser; georeferencing and scale validated.

### Phase 2: Procedural base layer (1-2 weeks)
- Fetch DEM, OSM, Overture, and open city 3D data for a bbox.
- Generate buildings, roads, and sidewalks; export glTF / 3D Tiles in 100 m chunks.
- **Exit criteria:** the full test area exists as locally generated, open-format geometry with no proprietary data.

### Phase 3: Chunk streaming system (1 week)
- 3-level LOD grid, directional async preloading, per-frame load budget, near-only collision, disk cache.
- Test with a free camera and placeholder chunks first.
- Implement the adaptive mechanisms above: range-request fetching, priority queue, prefetch, LRU disk cache, and frame-time feedback on the LOD threshold.
- Do runtime baking in worker threads (TypeScript or Rust compiled to WASM), not in the Python pipeline.
- **Exit criteria:** constant 60 fps with no hitches crossing chunk borders; test at 5-10 km from the origin to confirm precision.

### Phase 4: Imagery-based reconstruction (2-4 weeks)
- Mapillary / Panoramax fetcher using the user's token, with per-image license and attribution recorded.
- Mask people and vehicles (SAM 2 or Mask2Former). Run OpenSfM or COLMAP with sequential matching; discard GPS poses and use SfM poses only.
- Verify the reconstruction visually before training. Train with gsplat or Nerfstudio at about 1600 px, per block. Clean floaters (for example with SuperSplat), crop, compress.
- Implement Tier B facade texture projection as the fallback.
- **Exit criteria:** one Tier A street segment and one Tier B segment, both walkable.

### Phase 5: Integration and release prep (1-2 weeks)
- Blend Tier A splats into procedural surroundings with a smooth transition. Fix time of day for splat areas; prefer overcast captures to avoid baked shadows.
- Proxy mesh for collision and shadow casting; contact-shadow decal under the player.
- Write the README, contributor guide, `ATTRIBUTION`, license headers, and a one-command example for the validation city.
- **Exit criteria:** a vertical-slice demo of about 5 minutes and a reproducible build from a clean checkout.

### Later
- Godot and Unreal clients as community contributions.
- Crowdsourced capture workflow: contributors upload to Panoramax or Mapillary to fill coverage gaps.
- Mesh reconstruction (2DGS, SuGaR, or COLMAP + OpenMVS) as an alternative to splats for dynamic lighting.

## Performance targets

- 60 fps on a mid-range GPU in the web client.
- 1-3 million visible gaussians and a few hundred thousand visible triangles.
- No main-thread chunk loads.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Uneven imagery coverage between cities | Quality varies a lot | Tiered degradation; measure density per street in Phase 0; validate on a data-rich city first |
| Mapillary coverage poor in the target area | Tier A unavailable | Fall back to Tier B / C; encourage contributions to Panoramax / Mapillary |
| Mixed-device, mixed-lighting photos | Irregular splats | Cluster by capture date and sequence; exclude outliers; prefer overcast sets |
| Bad SfM poses | Warped scene | Sequential matching, high overlap, ignore GPS, visual check before training |
| Ghosts from people and cars | Poor splat quality | Mask dynamic objects before SfM and training; clean floaters after |
| GPU limits for training | Slow or impossible | Cloud GPU by the hour; lower resolution; train per block; document minimum specs |
| Splats lack dynamic lighting and shadows | Visual mismatch | Fixed time of day for splat areas; proxy mesh for shadows; evaluate mesh reconstruction later |
| Float precision at city scale | Jitter far from origin | Global double origin per chunk, local float inside; Cesium handles georeferencing |
| Share-alike / attribution mistakes | Legal exposure | Ship no data; auto-generate attribution; record per-image licenses; review before any data release |
| Non-open dependency creeps in | Blocks open-source goal | License check in CI; permissive libraries only in the core |
| Google terms change | Plugin breaks | Plugin is optional and isolated; core never depends on it |

## Immediate next steps

1. Pick the validation city and the personal target city.
2. Set up the repo skeleton (`pipeline/`, `clients/web/`, `docs/`, `ATTRIBUTION`) with the chosen license and a license-check CI step.
3. Write the Phase 0 coverage script (bbox in, coverage report out).
