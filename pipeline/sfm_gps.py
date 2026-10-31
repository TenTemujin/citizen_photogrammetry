#!/usr/bin/env python3
"""Phase 4: redo the incremental SfM with each photo's GPS position as a prior, in real-world metres.

Usage:
    python pipeline/sfm_gps.py [--db data/colmap/database.db] [--images data/images] [--out data/colmap/gps]
                               [--sigma-xy 8] [--sigma-z 50]

Why: without priors the 229-photo model drifted (see georeference.py: the best similarity fit to the GPS is
off by 40+ m and scale changes between sequences). Dashcam photos always look forward, so scale is poorly
constrained; GPS positions fix it. The feature matches in the database are reused (a copy is edited, the
original is untouched). Positions are in the web client's local frame (x = east, y = north, metres from the
SW corner of --bbox, z = 0 with a large sigma because GPS height is unreliable).

Mapillary token: MAPILLARY_TOKEN or .env (only used to read the photos' positions).
"""
import argparse
import csv
import pathlib
import shutil
import sys

import numpy as np
import pycolmap
from pyproj import Transformer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from georeference import ROOT, fetch_positions, token  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/colmap/database.db")
    p.add_argument("--images", default="data/images")
    p.add_argument("--out", default="data/colmap/gps")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"), default=[60.165, 24.930, 60.174, 24.948])
    p.add_argument("--sigma-xy", type=float, default=8.0, help="assumed GPS error in metres, horizontal")
    p.add_argument("--sigma-z", type=float, default=50.0, help="assumed error in metres, vertical (z is set to 0)")
    a = p.parse_args()

    s, w, _, _ = a.bbox
    to_gk = Transformer.from_crs(4326, 3879, always_xy=True)
    e0, n0 = to_gk.transform(w, s)
    with open(ROOT / a.images / "attribution.csv", newline="", encoding="utf-8") as f:
        ids = [row[0] for row in list(csv.reader(f))[1:] if row]
    pos = fetch_positions(ids, token())

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "database.db"
    shutil.copy(ROOT / a.db, db_path)
    db = pycolmap.Database.open(str(db_path))
    db.clear_pose_priors()
    cov = np.diag([a.sigma_xy ** 2, a.sigma_xy ** 2, a.sigma_z ** 2])
    n = 0
    for im in db.read_all_images():
        key = im.name.rsplit(".", 1)[0]
        if key not in pos:
            continue
        e, nn = to_gk.transform(*pos[key][0])
        pr = pycolmap.PosePrior()
        pr.position = np.array([e - e0, nn - n0, 0.0])
        pr.position_covariance = cov
        pr.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        pr.corr_data_id = im.data_id
        db.write_pose_prior(pr)
        n += 1
    db.close()
    print(f"wrote {n} position priors (sigma {a.sigma_xy} m xy, {a.sigma_z} m z)")

    opts = pycolmap.IncrementalPipelineOptions()
    opts.use_prior_position = True
    opts.use_robust_loss_on_prior_position = True
    recs = pycolmap.incremental_mapping(str(db_path), str(ROOT / a.images), str(out), opts)
    print(f"{len(recs)} model(s)")
    for k, r in recs.items():
        names = {im.name for im in r.images.values()}
        d = []
        for im in r.images.values():
            key = im.name.rsplit(".", 1)[0]
            if key in pos:
                e, nn = to_gk.transform(*pos[key][0])
                c = im.projection_center()
                d.append(np.hypot(c[0] - (e - e0), c[1] - (nn - n0)))
        d = np.array(d)
        print(f"model {k}: {len(names)} images, {r.num_points3D()} points, "
              f"horizontal distance to GPS: median {np.median(d):.1f} m, 90% {np.percentile(d, 90):.1f} m, max {d.max():.1f} m")


if __name__ == "__main__":
    main()
