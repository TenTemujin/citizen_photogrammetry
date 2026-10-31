"""Export each photo's pose from the GPS-aligned COLMAP model (data/colmap/geo_gps15) as JSON: east/north, forward and up vectors (east,north,up), FOV, error vs GPS.
Usage: python pipeline/export_poses.py poses.json
"""
import sys, json, numpy as np, pycolmap
sys.path.insert(0, "pipeline")
import georeference as G, csv
from pyproj import Transformer
rec = pycolmap.Reconstruction("data/colmap/geo_gps15")
ids=[r[0] for r in list(csv.reader(open("data/images/attribution.csv",newline="",encoding="utf-8")))[1:] if r]
pos=G.fetch_positions(ids,G.token())
to=Transformer.from_crs(4326,3879,always_xy=True); e0,n0=to.transform(24.930,60.165)
out=[]
for im in rec.images.values():
    k=im.name.rsplit(".",1)[0]
    if k not in pos: continue
    e,n=to.transform(*pos[k][0][:2])
    c=im.projection_center(); err=float(np.hypot(c[0]-(e-e0),c[1]-(n-n0)))
    R=im.cam_from_world().rotation.matrix()
    fwd=R.T@[0,0,1]; up=R.T@[0,-1,0]
    cam=rec.cameras[im.camera_id]
    w,h=cam.width,cam.height; f=cam.params[0]
    out.append(dict(name=im.name,err=err,x=float(c[0]),n=float(c[1]),fwd=fwd.tolist(),up=up.tolist(),fovy=float(np.degrees(2*np.arctan(h/(2*f)))),w=w,h=h,gps=[float(e-e0),float(n-n0)]))
out.sort(key=lambda d:d["err"])
json.dump(out,open(sys.argv[1],"w"))
print(len(out),[(o["name"],round(o["err"],1),round(o["fovy"]),o["w"],o["h"]) for o in out[:6]])
print("up z mean",np.mean([o["up"][2] for o in out]),"fwd z mean",np.mean([o["fwd"][2] for o in out]))
