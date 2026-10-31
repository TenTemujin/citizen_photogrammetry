#!/usr/bin/env python3
"""Phase 3b: clean street surfaces from OSM on top of the reality mesh.

Usage:
    python pipeline/streets.py [--mesh meshes/L18] [--out meshes/streets]

Aerial photogrammetry makes streets lumpy and melts cars into the asphalt. This writes, in the web client's
local frame (x east, y north, metres from the SW corner of the bbox):
  dem.bin      float32 grid of ground height (m), CELL metres per cell, row 0 = south; lowest mesh surface per cell, smoothed
  overlay.png  RGBA texture of the road / sidewalk / cycleway areas (alpha 0 elsewhere and under OSM buildings)
  mask.png     L image, 255 where a street surface is drawn (used by mesh_to_glb.py --streets to cut the lumpy mesh there)
  streets.json origin, size, cell and texture size
The client drapes a plane over the DEM with the overlay as an alpha-cut texture.
Run it on a mesh that is NOT yet cut by mesh_to_glb.py --streets: the DEM comes from the mesh surface, and the cut streets leave
no surface (a re-run on a cut mesh filled 21% of the cells from neighbours instead of 6%). To change only the overlay, run with
--out to another directory and copy overlay.png over.
Data: OSM, ODbL 1.0; heights from the Helsinki 3D+ mesh (CC BY 4.0).
"""
import argparse
import glob
import json
import pathlib

import numpy as np
import trimesh
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt, gaussian_filter, median_filter, minimum_filter, zoom

ROOT = pathlib.Path(__file__).resolve().parent.parent
M_PER_DEG_LAT = 111_320.0
X0, Y0, SIZE = -26.0, -26.0, 1056.0  # covers the 1 km bbox plus margin, metres
CELL = 2.0
TEX = 4096
MARKED = {"primary", "secondary", "tertiary", "residential", "unclassified"}  # classes that get a dashed centre line
CARRIAGE = {"motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified", "living_street", "service", "track"}
# colour (linear-ish 0..1) per surface class
COLORS = {"road": (0.17, 0.17, 0.18), "walk": (0.52, 0.50, 0.47), "cycle": (0.50, 0.20, 0.18), "plaza": (0.46, 0.44, 0.40)}
ORDER = ["road", "plaza", "walk", "cycle"]  # later classes are drawn over earlier ones


def noise(shape, scales, rng):
    out = np.zeros(shape, np.float32)
    for s, a in scales:
        small = rng.standard_normal((max(shape[0] // s, 2), max(shape[1] // s, 2))).astype(np.float32)
        out += a * zoom(small, (shape[0] / small.shape[0], shape[1] / small.shape[1]), order=1)[: shape[0], : shape[1]]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mesh", default="meshes/L18", help="uncut tiles (see above)")
    p.add_argument("--chunks", default="chunks")
    p.add_argument("--out", default="meshes/streets")
    a = p.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    idx = json.loads((ROOT / a.chunks / "index.json").read_text(encoding="utf-8"))
    south, west = idx["origin"]["south"], idx["origin"]["west"]
    m_lon = M_PER_DEG_LAT * np.cos(np.radians(south))

    # --- ground height: lowest mesh surface per cell
    n = int(SIZE / CELL)
    low = np.full((n, n), np.inf, np.float32)
    for f in sorted((ROOT / a.mesh).glob("*.glb")):
        mesh = trimesh.load(f, force="mesh")
        # sample the surface, not the vertices: flat ground is a few big triangles with no vertex inside most cells
        v = np.vstack([mesh.vertices, trimesh.sample.sample_surface(mesh, int(mesh.area * 3))[0]])
        ix = ((v[:, 0] - X0) / CELL).astype(int)
        iy = ((-v[:, 2] - Y0) / CELL).astype(int)
        ok = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n)
        np.minimum.at(low, (iy[ok], ix[ok]), v[ok, 1])
    empty = ~np.isfinite(low)
    near = distance_transform_edt(empty, return_distances=False, return_indices=True)
    dem = low[tuple(near)]
    # under tree canopies the mesh has no ground: the lowest surface there is the canopy's lower edge, metres too high. A minimum filter
    # wider than a crown (5 cells = 10 m) pulls those bumps down to the street level around them; terrain slopes cost < 0.3 m.
    dem = gaussian_filter(minimum_filter(median_filter(dem, size=3), size=5), 1.5).astype(np.float32)
    (out / "dem.bin").write_bytes(dem.tobytes())
    print(f"dem {n}x{n}, {empty.mean():.1%} cells filled from neighbours, height {dem.min():.1f}..{dem.max():.1f} m")

    # --- rasterise OSM surfaces
    px = TEX / SIZE
    def to_px(ring):
        return [((lon - west) * m_lon - X0) * px for lon, lat in ring], [TEX - ((lat - south) * M_PER_DEG_LAT - Y0) * px for lon, lat in ring]
    layers = {k: Image.new("L", (TEX, TEX), 0) for k in ORDER}
    centre = []  # (x0, y0, x1, y1) pixel centre lines of marked roads
    bld = Image.new("L", (TEX, TEX), 0)
    for f in glob.glob(str(ROOT / a.chunks / "*_*.geojson")):
        for ft in json.loads(pathlib.Path(f).read_text(encoding="utf-8"))["features"]:
            pr, g = ft["properties"], ft["geometry"]
            if g["type"] != "Polygon":
                continue
            xs, ys = to_px(g["coordinates"][0])
            pts = list(zip(xs, ys))
            if pr.get("kind") == "building":
                ImageDraw.Draw(bld).polygon(pts, fill=255)
                continue
            hw = pr.get("highway")
            if hw is None or hw == "platform":
                continue
            if hw in MARKED and len(pts) >= 4:
                # the quad of one OSM segment: its two shortest sides are the ends, the centre line joins their midpoints
                e = sorted(range(4), key=lambda i: np.hypot(pts[i][0] - pts[(i + 1) % 4][0], pts[i][1] - pts[(i + 1) % 4][1]))[:2]
                mid = [((pts[i][0] + pts[(i + 1) % 4][0]) / 2, (pts[i][1] + pts[(i + 1) % 4][1]) / 2) for i in e]
                if np.hypot(mid[0][0] - mid[1][0], mid[0][1] - mid[1][1]) > 8 * px and np.hypot(pts[e[0]][0] - pts[(e[0] + 1) % 4][0], pts[e[0]][1] - pts[(e[0] + 1) % 4][1]) > 5.5 * px:
                    centre.append((*mid[0], *mid[1]))
            cls = "road" if hw in CARRIAGE else "cycle" if hw == "cycleway" else "plaza" if hw == "pedestrian" else "walk"
            ImageDraw.Draw(layers[cls]).polygon(pts, fill=255)

    rng = np.random.default_rng(1)
    base = noise((TEX, TEX), [(4, 0.04), (16, 0.05), (64, 0.05)], rng)
    rgb = np.zeros((TEX, TEX, 3), np.float32)
    alpha = np.zeros((TEX, TEX), bool)
    for k in ORDER:
        m = np.asarray(layers[k]) > 0
        rgb[m] = COLORS[k]
        alpha |= m
    lines = Image.new("L", (TEX, TEX), 0)
    ld = ImageDraw.Draw(lines)
    for x0_, y0_, x1_, y1_ in centre:  # 3 m dash, 6 m gap
        length = np.hypot(x1_ - x0_, y1_ - y0_) / px
        for t in np.arange(1.5, length - 3, 9.0):
            ld.line([(x0_ + (x1_ - x0_) * t / length, y0_ + (y1_ - y0_) * t / length),
                     (x0_ + (x1_ - x0_) * (t + 3) / length, y0_ + (y1_ - y0_) * (t + 3) / length)], fill=255, width=2)
    rgb[np.asarray(lines) > 0] = 0.75
    alpha &= np.asarray(bld) == 0
    rgb = np.clip(rgb * (1 + base[..., None]), 0, 1)
    rgba = np.dstack([(rgb ** (1 / 2.2) * 255).astype(np.uint8), (alpha * 255).astype(np.uint8)])  # sRGB for the texture
    Image.fromarray(rgba, "RGBA").save(out / "overlay.png")
    Image.fromarray((alpha * 255).astype(np.uint8), "L").save(out / "mask.png")
    (out / "streets.json").write_text(json.dumps({"x0": X0, "y0": Y0, "size": SIZE, "cell": CELL, "n": n, "tex": TEX,
                                                   "attribution": "Streets: (c) OpenStreetMap contributors, ODbL 1.0"}), encoding="utf-8")
    print(f"overlay {TEX}px, {alpha.mean():.1%} of the area is street surface -> {out}")


if __name__ == "__main__":
    main()
