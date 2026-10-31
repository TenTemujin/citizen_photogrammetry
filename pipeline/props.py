#!/usr/bin/env python3
"""Phase 3: street props from OSM (trees, street lamps, benches, bins) as points in the web client's local frame.

Usage:
    python pipeline/props.py [S W N E] [--out meshes/props.json] [--refresh]

Same bbox and local frame as chunks.py (origin = SW corner, x = east, z = -north in the client).
The client places one instanced model per kind at each point and drops it onto the mesh surface.
Data: (c) OpenStreetMap contributors, ODbL 1.0.
"""
import argparse
import json
import math
import pathlib

from chunks import M_PER_DEG_LAT, overpass

KINDS = {  # kind -> Overpass selector
    "tree": 'node["natural"="tree"]',
    "lamp": 'node["highway"="street_lamp"]',
    "bench": 'node["amenity"="bench"]',
    "bin": 'node["amenity"="waste_basket"]',
}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bbox", nargs="*", type=float, metavar="S W N E", default=[60.165, 24.930, 60.174, 24.948])
    p.add_argument("--out", default="meshes/props.json")
    p.add_argument("--refresh", action="store_true")
    a = p.parse_args()
    s, w, n, e = a.bbox
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(s))

    props = {}
    for kind, selector in KINDS.items():
        els = overpass(f"[out:json][timeout:90];{selector}({s},{w},{n},{e});out;", a.refresh)
        props[kind] = [{"x": round((el["lon"] - w) * m_per_deg_lon, 2), "z": round(-(el["lat"] - s) * M_PER_DEG_LAT, 2)}
                       for el in els]
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"origin": {"south": s, "west": w}, "attribution": "(c) OpenStreetMap contributors, ODbL 1.0",
                               "props": props}))
    print("props: " + ", ".join(f"{k} {len(v)}" for k, v in props.items()) + f" -> {out}")


if __name__ == "__main__":
    main()
