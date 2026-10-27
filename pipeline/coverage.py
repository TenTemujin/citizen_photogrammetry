#!/usr/bin/env python3
"""Phase 0 coverage report for a bbox: OSM building completeness and Mapillary photo density.

Usage:
    python pipeline/coverage.py SOUTH WEST NORTH EAST
    MAPILLARY_TOKEN=... python pipeline/coverage.py SOUTH WEST NORTH EAST

Without MAPILLARY_TOKEN only the OSM part runs. Data is fetched live and nothing is stored.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

OVERPASS = "https://overpass-api.de/api/interpreter"
MAPILLARY = "https://graph.mapillary.com/images"
TILE_DEG = 0.0025  # ~275 m; small bbox per Mapillary request
MAPILLARY_LIMIT = 2000


def http_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "citizen-photogrammetry/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def osm_buildings(s, w, n, e):
    q = f'[out:json][timeout:90];way["building"]({s},{w},{n},{e});out tags;'
    els = http_json(OVERPASS, urllib.parse.urlencode({"data": q}).encode())["elements"]
    with_height = sum(1 for el in els if "height" in el["tags"] or "building:levels" in el["tags"])
    return len(els), with_height


def frange(a, b, step):
    while a < b:
        yield a
        a += step


def mapillary_tiles(token, s, w, n, e):
    tiles = []
    for lat in frange(s, n, TILE_DEG):
        for lon in frange(w, e, TILE_DEG):
            qs = urllib.parse.urlencode({
                "bbox": f"{lon},{lat},{min(lon + TILE_DEG, e)},{min(lat + TILE_DEG, n)}",
                "fields": "id",
                "limit": MAPILLARY_LIMIT,
                "access_token": token,
            })
            tiles.append(len(http_json(f"{MAPILLARY}?{qs}").get("data", [])))
    return tiles


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("south", type=float)
    p.add_argument("west", type=float)
    p.add_argument("north", type=float)
    p.add_argument("east", type=float)
    # placeholder thresholds: calibrate once real reconstructions exist
    p.add_argument("--dense", type=int, default=100, help="photos/tile for tier A")
    p.add_argument("--medium", type=int, default=20, help="photos/tile for tier B")
    a = p.parse_args()

    total, with_h = osm_buildings(a.south, a.west, a.north, a.east)
    pct = 100 * with_h / total if total else 0
    print(f"OSM buildings: {total}, with height/levels: {with_h} ({pct:.0f}%)")

    token = os.environ.get("MAPILLARY_TOKEN")
    if not token:
        print("Mapillary: skipped (set MAPILLARY_TOKEN)")
        return
    tiles = mapillary_tiles(token, a.south, a.west, a.north, a.east)
    dense = sum(t >= a.dense for t in tiles)
    medium = sum(a.medium <= t < a.dense for t in tiles)
    sparse = len(tiles) - dense - medium
    print(f"Mapillary tiles ({TILE_DEG} deg): {len(tiles)}  A: {dense}  B: {medium}  C/D: {sparse}")
    if any(t >= MAPILLARY_LIMIT for t in tiles):
        print(f"note: some tiles hit the {MAPILLARY_LIMIT}-image cap; real density is higher")


if __name__ == "__main__":
    sys.exit(main())
