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

ATTRIBUTION = "3D mesh: City of Helsinki, Helsinki 3D+ reality mesh, CC BY 4.0"


def srs_origin(block):
    root = ET.parse(block / "metadata.xml").getroot()
    return [float(v) for v in root.findtext("SRSOrigin").split(",")]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("block")
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
    entries = []
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
        dest = out / (obj.stem + ".glb")
        scene.export(dest)
        entries.append({"file": dest.name, "east": [float(mn[0]), float(mx[0])], "north": [float(mn[1]), float(mx[1])],
                        "bytes": dest.stat().st_size})
    (out / "index.json").write_text(json.dumps({
        "level": a.level, "origin": {"south": s, "west": w}, "attribution": ATTRIBUTION, "tiles": entries}, indent=2))
    total = sum(t["bytes"] for t in entries) / 2**20
    print(f"L{a.level}: {len(entries)} tiles, {total:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
