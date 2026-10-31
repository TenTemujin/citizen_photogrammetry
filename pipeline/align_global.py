"""Fit one similarity (scale, yaw, shift) of the SfM sparse points to the vertices of meshes_novg/L18 (grid search + robust least squares).
Usage: python pipeline/align_global.py poses.json global.json
Result on the Helsinki block: median 2.1 m to the mesh, only a small gain over identity (2.66 -> 2.07): the SfM drift is local, one transform cannot fix it.
"""
import sys, json, glob, numpy as np, pycolmap, trimesh
from scipy.spatial import cKDTree
from scipy.optimize import least_squares
rec = pycolmap.Reconstruction("data/colmap/geo_gps15")
poses = {p["name"]: p for p in json.load(open(sys.argv[1]))}
good = {n for n, p in poses.items() if p["err"] < 15}
pts = []
for pid, p in rec.points3D.items():
    names = [rec.images[e.image_id].name for e in p.track.elements]
    if len(names) >= 3 and sum(n in good for n in names) >= 2 and p.error < 1.5:
        pts.append(p.xyz)
pts = np.array(pts)
print("sparse points used", len(pts), "x", pts[:,0].min().round(), pts[:,0].max().round(), "y", pts[:,1].min().round(), pts[:,1].max().round(), "z pct", np.percentile(pts[:,2],[5,50,95]).round(1))
idx = json.load(open("meshes_novg/L18/index.json"))["tiles"]
V = []
for t in idx:
    if t["east"][1] < pts[:,0].min()-40 or t["east"][0] > pts[:,0].max()+40 or t["north"][1] < pts[:,1].min()-40 or t["north"][0] > pts[:,1].max()+40: continue
    m = trimesh.load("meshes_novg/L18/" + t["file"], force="mesh")
    v = np.asarray(m.vertices); V.append(np.column_stack([v[:,0], -v[:,2], v[:,1]]))  # E,N,U
V = np.vstack(V); print("mesh vertices", len(V), "U pct", np.percentile(V[:,2],[1,50,99]).round(1))
tree = cKDTree(V)
c0 = pts.mean(0)
def apply(par, P):
    s, th, tx, ty, tz = par
    R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    return s * (P - c0) @ R.T + c0 + [tx, ty, tz]
def score(par, P=pts, cut=3.0):
    d, _ = tree.query(apply(par, P)); return np.minimum(d, cut).mean()
best = (1e9, None)
for tz in range(0, 40, 2):
    for tx in range(-16, 17, 2):
        for ty in range(-16, 17, 2):
            sc = score([1, 0, tx, ty, tz], pts[::4])
            if sc < best[0]: best = (sc, [1, 0, tx, ty, tz])
print("grid best", round(best[0], 3), best[1], "identity", round(score([1,0,0,0,12],pts[::4]),3))
def res(par): d, _ = tree.query(apply(par, pts)); return np.minimum(d, 3.0)
r = least_squares(res, best[1], loss="soft_l1", f_scale=1.0, x_scale=[0.05, 0.05, 2, 2, 2])
print("refined", r.x.round(3), "score", round(score(r.x), 3), "was", round(score(best[1]), 3))
json.dump(dict(par=list(r.x), c0=list(c0)), open(sys.argv[2], "w"))
d, _ = tree.query(apply(r.x, pts)); print("dist to mesh pct 25/50/75/90", np.percentile(d, [25, 50, 75, 90]).round(2))
