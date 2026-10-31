#!/usr/bin/env python3
"""Phase 4: put the COLMAP model of the Mapillary photos in real-world metres using the photos' GPS.

Usage:
    python pipeline/georeference.py [--model data/colmap/sparse/0] [--images data/images]
                                    [--out data/colmap/geo] [--max-error 15]

Looks up each photo's GPS position (Mapillary Graph API, token from MAPILLARY_TOKEN or .env), converts it
to the local frame of the web client (x = east, y = north, metres from the SW corner of --bbox) and fits
scale, heading and offset of the model to those fixes. COLMAP's own model_aligner fails here (GPS has no
usable height and the streets are near-straight lines), so: "up" comes from the mean camera orientation
(dashcams are level), then a 2D similarity is fitted to the GPS fixes with RANSAC.

Prints the residual between the aligned camera centres and the GPS fixes: that is the accuracy to expect,
GPS alone is typically several metres. The vertical offset is NOT solved (z = 0 at the mean camera
height); it has to be set against the mesh afterwards. Writes the aligned model to --out.
"""
import argparse
import csv
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pycolmap
from pyproj import Transformer

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://graph.mapillary.com/images"


def token():
    t = os.environ.get("MAPILLARY_TOKEN")
    env = ROOT / ".env"
    if not t and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("MAPILLARY_TOKEN="):
                t = line.partition("=")[2].strip().strip("\"'")
    if not t:
        raise SystemExit("set MAPILLARY_TOKEN or put it in .env")
    return t


def fetch_positions(ids, tok):
    out = {}
    for i in range(0, len(ids), 50):
        qs = urllib.parse.urlencode({"image_ids": ",".join(ids[i:i + 50]),
                                     "fields": "id,geometry,computed_geometry", "access_token": tok})
        req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": "citizen-photogrammetry/0.1"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=60))["data"]
        except urllib.error.HTTPError as err:  # the URL holds the token: report the API message only
            raise SystemExit(f"HTTP {err.code} from graph.mapillary.com: {err.read()[:200].decode('utf-8', 'replace')}")
        for d in data:
            g = d.get("computed_geometry") or d.get("geometry")
            if g:
                out[d["id"]] = (g["coordinates"], bool(d.get("computed_geometry")))
    return out


def rotation_to_z(up):
    """Rotation matrix taking the unit vector `up` to +z."""
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(up, z)
    c = float(up @ z)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def fit_similarity_2d(p, g, thr, tries=5000, seed=0):
    """Complex-number similarity g ~ a * p + b, RANSAC over point pairs then least squares on the inliers."""
    rng = np.random.default_rng(seed)
    pc, gc = p[:, 0] + 1j * p[:, 1], g[:, 0] + 1j * g[:, 1]
    best = None
    for _ in range(tries):
        i, j = rng.choice(len(pc), 2, replace=False)
        if abs(pc[j] - pc[i]) < 1e-6:
            continue
        a = (gc[j] - gc[i]) / (pc[j] - pc[i])
        b = gc[i] - a * pc[i]
        inl = np.abs(a * pc + b - gc) < thr
        if best is None or inl.sum() > best.sum():
            best = inl
    pm, gm = pc[best].mean(), gc[best].mean()
    a = np.sum(np.conj(pc[best] - pm) * (gc[best] - gm)) / np.sum(np.abs(pc[best] - pm) ** 2)
    return a, gm - a * pm, best


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="data/colmap/sparse/0")
    p.add_argument("--images", default="data/images")
    p.add_argument("--out", default="data/colmap/geo")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"), default=[60.165, 24.930, 60.174, 24.948])
    p.add_argument("--max-error", type=float, default=15.0, help="RANSAC inlier threshold in metres")
    a = p.parse_args()

    s, w, _, _ = a.bbox
    to_gk = Transformer.from_crs(4326, 3879, always_xy=True)
    e0, n0 = to_gk.transform(w, s)

    with open(ROOT / a.images / "attribution.csv", newline="", encoding="utf-8") as f:
        ids = [row[0] for row in list(csv.reader(f))[1:] if row]
    pos = fetch_positions(ids, token())
    print(f"{len(pos)}/{len(ids)} photos have a GPS position "
          f"({sum(1 for v in pos.values() if v[1])} corrected by Mapillary's SfM)")

    rec = pycolmap.Reconstruction(str(ROOT / a.model))
    names, centers, ups, gps = [], [], [], []
    for im in rec.images.values():
        key = im.name.rsplit(".", 1)[0]
        if key not in pos:
            continue
        lon, lat = pos[key][0]
        e, n = to_gk.transform(lon, lat)
        names.append(im.name)
        centers.append(im.projection_center())
        ups.append(-im.cam_from_world().matrix()[1, :3])  # COLMAP camera y points down
        gps.append((e - e0, n - n0))
    centers, gps = np.array(centers), np.array(gps)
    up = np.mean(ups, axis=0)
    up /= np.linalg.norm(up)
    r_up = rotation_to_z(up)
    plan = centers @ r_up.T

    z_spread = np.percentile(plan[:, 2], [5, 95])
    print(f"camera height spread after levelling: {z_spread[1] - z_spread[0]:.2f} model units (small = dashcams are level)")
    c, b, inl = fit_similarity_2d(plan[:, :2], gps, a.max_error)
    scale, heading = abs(c), np.angle(c)
    rz = np.array([[np.cos(heading), -np.sin(heading), 0], [np.sin(heading), np.cos(heading), 0], [0, 0, 1]])
    rot = rz @ r_up
    t = np.array([b.real, b.imag, -scale * plan[:, 2].mean()])
    rec.transform(pycolmap.Sim3d(np.hstack([scale * rot, t[:, None]])))

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    rec.write(str(out))

    after = {im.name: im.projection_center() for im in rec.images.values()}
    d = np.array([np.hypot(*(after[nm][:2] - g)) for nm, g in zip(names, gps)])
    print(f"scale {scale:.3f} m per model unit, heading {np.degrees(heading):.1f} deg, {inl.sum()}/{len(inl)} RANSAC inliers (<{a.max_error} m)")
    print(f"horizontal residual vs GPS: median {np.median(d):.1f} m, 90% {np.percentile(d, 90):.1f} m, max {d.max():.1f} m")
    pts = np.array([q.xyz for q in rec.points3D.values()])
    print(f"sparse points {len(pts)}: x {pts[:, 0].min():.0f}..{pts[:, 0].max():.0f} m, y {pts[:, 1].min():.0f}..{pts[:, 1].max():.0f} m, "
          f"z {np.percentile(pts[:, 2], 5):.0f}..{np.percentile(pts[:, 2], 95):.0f} m")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
