"""Per-photo refinement: for each photo with GPS error < 15 m, shift/yaw its own sparse points onto the mesh vertices (grid search +-12 m, robust refine).
Usage: python pipeline/align_to_mesh.py poses.json global.json local.json
Helsinki block, 115 photos: mean distance to mesh 2.66 -> 1.82 m, 110 improved; shifts vary by about 6 m between photos, so poses are roughly right (some match the mesh well, some do not) but not yet good enough to project photos on facades.
"""
import sys, json, numpy as np, pycolmap, trimesh
from scipy.spatial import cKDTree
from scipy.optimize import least_squares
rec = pycolmap.Reconstruction("data/colmap/geo_gps15")
poses = json.load(open(sys.argv[1])); gl = json.load(open(sys.argv[2]))
G, c0g = np.array(gl["par"]), np.array(gl["c0"])
by = {p["name"]: p for p in poses}
idx = json.load(open("meshes_novg/L18/index.json"))["tiles"]
V = []
for t in idx:
    m = trimesh.load("meshes_novg/L18/" + t["file"], force="mesh"); v = np.asarray(m.vertices); V.append(np.column_stack([v[:,0], -v[:,2], v[:,1]]))
V = np.vstack(V); tree = cKDTree(V)
def rotz(th): return np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
def tf(par, P, c):  # yaw about c then shift
    th, tx, ty, tz = par; return (P - c) @ rotz(th).T + c + [tx, ty, tz]
def sc(par, P, c, cut=3.0): d, _ = tree.query(tf(par, P, c)); return np.minimum(d, cut).mean()
out = []
for im in rec.images.values():
    p = by.get(im.name)
    if p is None or p["err"] >= 15: continue
    P = np.array([q.xyz for q in (rec.points3D[o.point3D_id] for o in im.points2D if o.has_point3D()) if q.error < 1.5 and len(q.track.elements) >= 3])
    if len(P) < 40: continue
    c = np.median(P, 0)
    base = sc([0, 0, 0, 12], P, c)
    best = (1e9, None)
    for tz in range(0, 36, 2):
        for tx in range(-12, 13, 2):
            for ty in range(-12, 13, 2):
                s = sc([0, tx, ty, tz], P, c)
                if s < best[0]: best = (s, [0, tx, ty, tz])
    r = least_squares(lambda q: np.minimum(tree.query(tf(q, P, c))[0], 3.0), best[1], loss="soft_l1", x_scale=[0.05, 2, 2, 2])
    cam = np.array([p["x"], p["n"], 0.0])  # camera centre in model frame (z from model below)
    cc = im.projection_center()
    cn = tf(r.x, cc[None], c)[0]
    th = r.x[0]; f = np.array(p["fwd"]); f2 = rotz(th) @ f
    out.append(dict(name=im.name, npts=len(P), s0=float(base), s1=float(sc(r.x, P, c)), th=float(th), shift=[float(x) for x in r.x[1:]], x=float(cn[0]), n=float(cn[1]), z=float(cn[2]), fwd=f2.tolist(), fovy=p["fovy"], err=p["err"], gps=p["gps"]))
json.dump(out, open(sys.argv[3], "w"))
a = np.array([[o["s0"], o["s1"]] for o in out]); print(len(out), "images; mean score before/after", a.mean(0).round(2), "improved", int((a[:,1] < a[:,0]-0.2).sum()))
sh = np.array([o["shift"] for o in out]); print("shift xy median", np.median(sh[:, :2], 0).round(1), "spread(std)", sh[:, :2].std(0).round(1), "dz median", np.median(sh[:, 2]).round(1), "std", sh[:,2].std().round(1))
print("corrected-vs-GPS dist median", np.median([np.hypot(o["x"]-o["gps"][0], o["n"]-o["gps"][1]) for o in out]).round(1), "yaw corr deg pct", np.percentile(np.degrees([o["th"] for o in out]), [10, 50, 90]).round(1))
