# Graph Report - d:/my_projects/citizen_photgrammetry  (2026-10-26)

## Corpus Check
- Corpus is ~2,399 words - fits in a single context window. You may not need a graph.

## Summary
- 25 nodes · 32 edges · 4 communities
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 3 edges (avg confidence: 0.92)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Phase 0 Coverage Script
- Licensing and Open Source
- Imagery Reconstruction Tiers
- Adaptive Streaming and Formats

## God Nodes (most connected - your core abstractions)
1. `mapillary_tiles()` - 5 edges
2. `Per-street quality tiers A-D` - 5 edges
3. `osm_buildings()` - 4 edges
4. `main()` - 4 edges
5. `Implementation Plan` - 4 edges
6. `http_json()` - 3 edges
7. `Licensing constraints` - 3 edges
8. `Adaptive streaming` - 3 edges
9. `Gaussian Splatting hero street` - 3 edges
10. `Mapillary street imagery` - 3 edges

## Surprising Connections (you probably didn't know these)
- `osm_buildings()` --references--> `OSM / Overture data`  [INFERRED]
  pipeline/coverage.py → implementation_plan.md
- `mapillary_tiles()` --references--> `Mapillary street imagery`  [INFERRED]
  pipeline/coverage.py → implementation_plan.md
- `main()` --conceptually_related_to--> `Per-street quality tiers A-D`  [INFERRED]
  pipeline/coverage.py → implementation_plan.md
- `README` --references--> `Implementation Plan`  [EXTRACTED]
  README.md → implementation_plan.md
- `Citizen Photogrammetry project` --references--> `Ship code, not data`  [EXTRACTED]
  README.md → implementation_plan.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Open data sources feeding the pipeline** — implementation_plan_osm_overture_data, implementation_plan_mapillary_imagery, implementation_plan_helsinki_reality_mesh [INFERRED 0.75]

## Communities (4 total, 0 thin omitted)

### Community 0 - "Phase 0 Coverage Script"
Cohesion: 0.43
Nodes (7): Phase 0 coverage audit, frange(), http_json(), main(), mapillary_tiles(), osm_buildings(), Quick start: coverage.py

### Community 1 - "Licensing and Open Source"
Cohesion: 0.33
Nodes (6): Implementation Plan, Google 3D Tiles (optional plugin), Licensing constraints, Ship code, not data, README, Citizen Photogrammetry project

### Community 2 - "Imagery Reconstruction Tiers"
Cohesion: 0.40
Nodes (6): Gaussian Splatting hero street, Mapillary street imagery, OSM / Overture data, Procedural base layer, Per-street quality tiers A-D, SfM (COLMAP / OpenSfM)

### Community 3 - "Adaptive Streaming and Formats"
Cohesion: 0.40
Nodes (5): Adaptive streaming, Chunk streaming (100 m, 3 LODs), Helsinki reality mesh, Open formats (3D Tiles, glTF, spz), Web client (CesiumJS)

## Knowledge Gaps
- **8 isolated node(s):** `README`, `Citizen Photogrammetry project`, `Quick start: coverage.py`, `Google 3D Tiles (optional plugin)`, `SfM (COLMAP / OpenSfM)` (+3 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Implementation Plan` connect `Licensing and Open Source` to `Imagery Reconstruction Tiers`, `Adaptive Streaming and Formats`?**
  _High betweenness centrality (0.612) - this node is a cross-community bridge._
- **Why does `Per-street quality tiers A-D` connect `Imagery Reconstruction Tiers` to `Phase 0 Coverage Script`, `Licensing and Open Source`?**
  _High betweenness centrality (0.583) - this node is a cross-community bridge._
- **Why does `Adaptive streaming` connect `Adaptive Streaming and Formats` to `Licensing and Open Source`?**
  _High betweenness centrality (0.304) - this node is a cross-community bridge._
- **What connects `README`, `Citizen Photogrammetry project`, `Quick start: coverage.py` to the rest of the system?**
  _8 weakly-connected nodes found - possible documentation gaps or missing edges._