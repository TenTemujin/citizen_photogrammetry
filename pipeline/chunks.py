#!/usr/bin/env python3
"""Phase 2 skeleton: fetch OSM buildings and roads for a bbox and split them into 100 m chunks.

Usage:
    python pipeline/chunks.py SOUTH WEST NORTH EAST [--out chunks] [--size 100]

Writes <out>/<ix>_<iy>.geojson (one FeatureCollection per chunk, WGS84) and <out>/index.json.
Features carry properties.kind = "building" | "road". Roads are cut into one quad per segment so
each piece lands in exactly one chunk; bends leave small wedge gaps (no joins yet).
Chunk indices come from a local equirectangular grid anchored at the bbox south-west corner.
Data is fetched live; the output stays local (see .gitignore) and carries OSM attribution.
"""
import argparse
import hashlib
import json
import math
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
M_PER_DEG_LAT = 111_320.0
DEFAULT_HEIGHT_M = 9.0  # fallback when OSM has neither height nor building:levels
LEVEL_M = 3.0
DEFAULT_ROAD_M = 4.0
# rough widths per highway class; OSM rarely tags the real width
ROAD_WIDTH_M = {
    "motorway": 12, "trunk": 10, "primary": 9, "secondary": 8, "tertiary": 7,
    "residential": 6, "unclassified": 6, "living_street": 5, "service": 4,
    "pedestrian": 5, "footway": 2, "path": 2, "cycleway": 2.5, "steps": 2, "track": 3,
}
# "corridor" is an indoor passage inside buildings (malls, stations), not a street
SKIP_HIGHWAY = {"proposed", "construction", "raceway", "bus_guideway", "elevator", "corridor"}


CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache"  # git-ignored
ROUNDS = 2  # full passes over the mirror list
RETRY_WAIT_S = 10


def overpass(query, refresh=False):
    """Run one Overpass query: cached on disk, mirrors tried in turn, whole list retried."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"overpass-{hashlib.sha1(query.encode()).hexdigest()[:16]}.json"
    if cached.exists() and not refresh:
        print(f"overpass: cache hit {cached.name}")
        return json.loads(cached.read_text(encoding="utf-8"))
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(ROUNDS):
        for url in OVERPASS:  # public instances are flaky; try each mirror in turn
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "citizen-photogrammetry/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    payload = json.load(r)
                if "remark" in payload:  # HTTP 200 with a server-side error and partial data: never cache it
                    raise OSError(payload["remark"])
                cached.write_text(json.dumps(payload["elements"]), encoding="utf-8")
                return payload["elements"]
            except (OSError, ValueError) as err:  # URLError and TimeoutError are OSError
                print(f"overpass {url} failed: {err}")
                last = err
        if attempt < ROUNDS - 1:
            print(f"all mirrors failed, retrying in {RETRY_WAIT_S}s")
            time.sleep(RETRY_WAIT_S)
    raise SystemExit(f"all Overpass mirrors failed: {last}")


def fetch_elements(s, w, n, e, refresh=False):
    # two smaller queries instead of one union: each is cached on its own, so a retry resumes
    box = f"({s},{w},{n},{e})"
    elements = []
    for selector in ('way["building"]', 'way["highway"]'):
        elements += overpass(f"[out:json][timeout:90];{selector}{box};out geom tags;", refresh)
    return elements


def height_m(tags):
    try:
        if "height" in tags:
            return float(tags["height"].split()[0])
        if "building:levels" in tags:
            return float(tags["building:levels"]) * LEVEL_M
    except ValueError:
        pass
    return DEFAULT_HEIGHT_M


def road_width_m(tags):
    try:
        if "width" in tags:
            return float(tags["width"].split()[0])
    except ValueError:
        pass
    return ROAD_WIDTH_M.get(tags.get("highway"), DEFAULT_ROAD_M)


class Grid:
    """Local equirectangular frame anchored at the bbox south-west corner."""

    def __init__(self, south, west, size):
        self.s, self.w, self.size = south, west, size
        self.m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(south))

    def to_m(self, lon, lat):
        return (lon - self.w) * self.m_per_deg_lon, (lat - self.s) * M_PER_DEG_LAT

    def to_deg(self, x, y):
        return [self.w + x / self.m_per_deg_lon, self.s + y / M_PER_DEG_LAT]

    def key(self, x, y):
        return int(x // self.size), int(y // self.size)


def building_feature(el, grid):
    ring = [[p["lon"], p["lat"]] for p in el["geometry"]]
    if len(ring) < 4 or ring[0] != ring[-1]:
        return None  # open or degenerate way
    tags = el.get("tags", {})
    pts = [grid.to_m(*p) for p in ring]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    feat = {
        "type": "Feature",
        "properties": {"kind": "building", "osm_id": el["id"], "height_m": height_m(tags),
                       "height_known": "height" in tags or "building:levels" in tags},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }
    return grid.key(cx, cy), feat


def road_features(el, grid):
    tags = el.get("tags", {})
    if tags.get("highway") in SKIP_HIGHWAY or tags.get("area") == "yes" or tags.get("indoor") == "yes":
        return
    half = road_width_m(tags) / 2
    pts = [grid.to_m(p["lon"], p["lat"]) for p in el["geometry"]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 0.01:
            continue
        nx, ny = -(y1 - y0) / length * half, (x1 - x0) / length * half
        quad = [grid.to_deg(x0 + nx, y0 + ny), grid.to_deg(x1 + nx, y1 + ny),
                grid.to_deg(x1 - nx, y1 - ny), grid.to_deg(x0 - nx, y0 - ny)]
        quad.append(quad[0])
        yield grid.key((x0 + x1) / 2, (y0 + y1) / 2), {
            "type": "Feature",
            "properties": {"kind": "road", "osm_id": el["id"], "width_m": half * 2,
                           "highway": tags.get("highway")},
            "geometry": {"type": "Polygon", "coordinates": [quad]},
        }


def build_chunks(elements, grid):
    chunks = {}
    for el in elements:
        if "geometry" not in el:
            continue
        if "building" in el.get("tags", {}):
            hit = building_feature(el, grid)
            hits = [hit] if hit else []
        else:
            hits = list(road_features(el, grid))
        for key, feat in hits:
            chunks.setdefault(key, []).append(feat)
    return chunks


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("south", "west", "north", "east"):
        p.add_argument(name, type=float)
    p.add_argument("--out", default="chunks")
    p.add_argument("--size", type=float, default=100.0, help="chunk edge in meters")
    p.add_argument("--refresh", action="store_true", help="ignore the Overpass cache in data/cache")
    a = p.parse_args()

    grid = Grid(a.south, a.west, a.size)
    chunks = build_chunks(fetch_elements(a.south, a.west, a.north, a.east, a.refresh), grid)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for (ix, iy), feats in sorted(chunks.items()):
        name = f"{ix}_{iy}.geojson"
        (out / name).write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
        kinds = [f["properties"]["kind"] for f in feats]
        index.append({"file": name, "ix": ix, "iy": iy,
                      "buildings": kinds.count("building"), "roads": kinds.count("road")})
    meta = {"origin": {"south": a.south, "west": a.west}, "size_m": a.size, "chunks": index,
            "attribution": "(c) OpenStreetMap contributors, ODbL 1.0"}
    (out / "index.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"{sum(c['buildings'] for c in index)} buildings, {sum(c['roads'] for c in index)} road segments "
          f"in {len(index)} chunks -> {out}/")


if __name__ == "__main__":
    main()
