#!/usr/bin/env python3
"""Phase 3: convert a Helsinki reality-mesh block (OBJ + JPG) to GLB tiles in the web client's local frame.

Usage:
    python pipeline/mesh_to_glb.py data/mesh/672496 --level 18 [--bbox S W N E] [--out meshes]

The block's metadata.xml gives the origin in EPSG:3879 (ETRS-GK25); vertices are metres from it.
Output frame matches clients/web (x = east, y = up, z = -north, origin = SW corner of --bbox, which
should be the same bbox given to chunks.py). Each OBJ at the chosen quadtree level becomes
<out>/L<level>/<name>.glb; <out>/L<level>/index.json lists them with their bounds.
Only tiles that touch the bbox are converted. Heights stay in the source vertical datum (EPSG:5773).
Data licence: Helsinki 3D, CC BY 4.0 (city of Helsinki); keep the attribution with any output.
"""
import argparse
import json
import pathlib
import re
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
from pyproj import Transformer
from scipy.ndimage import gaussian_filter, maximum_filter
from scipy.spatial import cKDTree

ATTRIBUTION = "3D mesh: City of Helsinki, Helsinki 3D+ reality mesh, CC BY 4.0"


def srs_origin(block):
    root = ET.parse(block / "metadata.xml").getroot()
    return [float(v) for v in root.findtext("SRSOrigin").split(",")]


MIN_CANOPY_M = 3.0  # a green face counts as tree only this far above the lowest surface nearby (not lawn)
GROUND_RADIUS_M = 15.0
DARK_VALUE = 0.4  # brightest channel below this = shadowed (0..1)
GROW_RINGS = 3


def vegetation_faces(mesh):
    """Boolean per face: tree canopy = texture is green AND the face is well above the ground around it.
    Works in the output frame (x east, y up, z -north)."""
    img = np.asarray(mesh.visual.material.image.convert("RGB"), dtype=np.float32) / 255
    h, w, _ = img.shape
    uv = mesh.visual.uv[mesh.faces].mean(1)
    px = img[np.clip(((1 - uv[:, 1]) * (h - 1)).astype(int), 0, h - 1), np.clip((uv[:, 0] * (w - 1)).astype(int), 0, w - 1)]
    r, g, b = px.T
    sat = (px.max(1) - px.min(1)) / np.maximum(px.max(1), 1e-6)
    green = (g >= r) & (g >= b) & (sat > 0.12)
    v = np.asarray(mesh.vertices)
    fc = v[mesh.faces].mean(1)
    near = cKDTree(v[:, [0, 2]]).query_ball_point(fc[:, [0, 2]], GROUND_RADIUS_M)
    # coarse tiles (L16) have faces larger than the radius: fall back to the face's own lowest vertex
    ground = np.array([v[i, 1].min() if i else v[f, 1].min() for i, f in zip(near, mesh.faces)])
    veg = green & (fc[:, 1] - ground > MIN_CANOPY_M)
    # shaded leaves are dark and nearly grey, so the colour test misses them: grow the canopy over dark faces next to it
    dark = (px.max(1) < DARK_VALUE) & (fc[:, 1] - ground > 1.0)
    for _ in range(GROW_RINGS):
        on = np.zeros(len(v), bool)
        on[mesh.faces[veg].ravel()] = True
        veg = veg | (dark & on[mesh.faces].any(1))
    return veg, ground


def split_trees(mesh, veg, ground):
    """One record per crown: local height maxima on a 1 m raster (min 5 m apart), canopy faces go to the nearest one."""
    v = np.asarray(mesh.vertices)
    c = v[mesh.faces[veg]].mean(1)
    top = v[mesh.faces[veg]].max(1)[:, 1]
    if len(c) < 8:
        return []
    x0, z0 = c[:, 0].min() - 2, c[:, 2].min() - 2
    shape = (int(np.ptp(c[:, 2]) + 5), int(np.ptp(c[:, 0]) + 5))
    height = np.full(shape, -np.inf)
    np.maximum.at(height, (((c[:, 2] - z0)).astype(int), ((c[:, 0] - x0)).astype(int)), top)
    filled = np.where(np.isfinite(height), height, 0)
    smooth = gaussian_filter(filled, 1.5)
    peaks = np.argwhere((smooth == maximum_filter(smooth, size=5)) & (smooth > 1.0))
    if not len(peaks):
        return []
    centres = np.column_stack([peaks[:, 1] + x0, peaks[:, 0] + z0])
    owner = cKDTree(centres).query(c[:, [0, 2]])[1]
    trees = []
    gnd = ground[veg]
    for i in range(len(centres)):
        m = owner == i
        if m.sum() < 6:
            continue
        cx, cz = c[m, 0].mean(), c[m, 2].mean()
        d = np.hypot(c[m, 0] - cx, c[m, 2] - cz)
        base, ground_y, tip = np.percentile(c[m, 1], 10), np.median(gnd[m]), top[m].max()
        trees.append({"x": float(cx), "z": float(cz), "ground": float(ground_y), "base": float(base),
                      "top": float(tip), "radius": float(max(np.percentile(d, 90), 1.5))})
    return trees


def merge_trees(trees, dist=3.0):
    """Tiles overlap at their borders: keep the taller of any two crowns closer than `dist` metres."""
    keep = []
    for t in sorted(trees, key=lambda t: -t["top"]):
        if all(np.hypot(t["x"] - k["x"], t["z"] - k["z"]) > dist for k in keep):
            keep.append(t)
    return keep


STREET_CUT_M = 2.2  # street faces up to this far above the DEM go: ground lumps, parked cars, buses


def load_streets(d):
    from PIL import Image
    meta = json.loads((pathlib.Path(d) / "streets.json").read_text(encoding="utf-8"))
    n = meta["n"]
    meta["dem"] = np.fromfile(pathlib.Path(d) / "dem.bin", dtype=np.float32).reshape(n, n)
    meta["mask"] = np.asarray(Image.open(pathlib.Path(d) / "mask.png")) > 0
    return meta


def street_faces(mesh, st):
    """Boolean per face: all three corners on a street surface (OSM overlay) and the centroid low enough above the ground
    model to be street clutter. Faces only partly on the street stay, otherwise big triangles leave holes the overlay cannot fill."""
    v = np.asarray(mesh.vertices)
    tex, size, n = st["tex"], st["size"], st["n"]

    def on_street(p):
        east, north = p[..., 0], -p[..., 2]
        col = np.clip(((east - st["x0"]) / size * tex).astype(int), 0, tex - 1)
        row = np.clip((tex - (north - st["y0"]) / size * tex).astype(int), 0, tex - 1)
        return st["mask"][row, col]

    corners = v[mesh.faces]
    c = corners.mean(1)
    ix = np.clip(((c[:, 0] - st["x0"]) / st["cell"]).astype(int), 0, n - 1)
    iy = np.clip(((-c[:, 2] - st["y0"]) / st["cell"]).astype(int), 0, n - 1)
    return on_street(corners).all(1) & (c[:, 1] - st["dem"][iy, ix] < STREET_CUT_M)


RAMP_Y = (0.35, 0.9)  # |normal.y| of a slope that is neither ground (> 0.9) nor wall (< 0.35)
RAMP_MAX_M = 9.0  # only this low above the ground model


def ramp_faces(mesh, st):
    """Slanted faces close to the ground: what is left of canopy curtains and of ground lifted to canopy height once the trees
    are cut out. Walls and roofs are steeper or higher, flat ground is flatter."""
    c = np.asarray(mesh.vertices)[mesh.faces].mean(1)
    n = st["n"]
    ix = np.clip(((c[:, 0] - st["x0"]) / st["cell"]).astype(int), 0, n - 1)
    iy = np.clip(((-c[:, 2] - st["y0"]) / st["cell"]).astype(int), 0, n - 1)
    ny = np.abs(mesh.face_normals[:, 1])
    return (ny > RAMP_Y[0]) & (ny < RAMP_Y[1]) & (c[:, 1] - st["dem"][iy, ix] < RAMP_MAX_M)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("block")
    p.add_argument("--strip-veg", action="store_true", help="drop green canopy faces from the tiles (the client plants proper trees instead)")
    p.add_argument("--streets", help="dir written by pipeline/streets.py: cut mesh faces lying on OSM street surfaces (the client drapes clean streets)")
    p.add_argument("--trees-out", help="with --strip-veg: write one record per crown to this JSON file")
    p.add_argument("--level", type=int, default=18, help="quadtree level to convert (13 coarse .. 21 fine)")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                   default=[60.165, 24.930, 60.174, 24.948])
    p.add_argument("--out", default="meshes")
    a = p.parse_args()

    block = pathlib.Path(a.block)
    ox, oy, _ = srs_origin(block)
    s, w, n, e = a.bbox
    to_gk = Transformer.from_crs(4326, 3879, always_xy=True)
    e0, n0 = to_gk.transform(w, s)  # local origin: SW corner
    e1, n1 = to_gk.transform(e, n)
    # local extent in GK metres (x east, y north) from the local origin
    lo = np.array([0.0, 0.0])
    hi = np.array([e1 - e0, n1 - n0])

    out = pathlib.Path(a.out) / f"L{a.level}"
    out.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"_L{a.level}(_|\.)")
    st = load_streets(a.streets) if a.streets else None
    entries, trees = [], []
    for obj in sorted(block.rglob("*.obj")):
        if not pattern.search(obj.name):
            continue
        scene = trimesh.load(obj, process=False, force="mesh")
        v = np.asarray(scene.vertices, dtype=np.float64)
        east, north = ox + v[:, 0] - e0, oy + v[:, 1] - n0
        mn = np.array([east.min(), north.min()])
        mx = np.array([east.max(), north.max()])
        if (mx < lo).any() or (mn > hi).any():
            continue
        scene.vertices = np.column_stack([east, v[:, 2], -north])
        if a.strip_veg:
            veg, ground = vegetation_faces(scene)
            trees += split_trees(scene, veg, ground)
            scene.update_faces(~veg)
            scene.remove_unreferenced_vertices()
        if st is not None:
            scene.update_faces(~street_faces(scene, st))
            scene.remove_unreferenced_vertices()
        if len(scene.faces) == 0:  # nothing left (a tile entirely on streets): keep a degenerate-free empty tile out of the index
            continue
        dest = out / (obj.stem + ".glb")
        scene.export(dest)
        entries.append({"file": dest.name, "east": [float(mn[0]), float(mx[0])], "north": [float(mn[1]), float(mx[1])],
                        "bytes": dest.stat().st_size})
    (out / "index.json").write_text(json.dumps({
        "level": a.level, "origin": {"south": s, "west": w}, "attribution": ATTRIBUTION, "tiles": entries}, indent=2))
    if a.trees_out:
        trees = merge_trees(trees)
        pathlib.Path(a.trees_out).write_text(json.dumps({"trees": trees}), encoding="utf-8")
        print(f"{len(trees)} tree crowns -> {a.trees_out}")
    total = sum(t["bytes"] for t in entries) / 2**20
    print(f"L{a.level}: {len(entries)} tiles, {total:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
