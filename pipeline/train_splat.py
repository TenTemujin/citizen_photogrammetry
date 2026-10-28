#!/usr/bin/env python3
"""Phase 4: train a Gaussian splat (gsplat, Apache-2.0) from an undistorted COLMAP model.

Usage:
    python pipeline/train_splat.py [--data data/colmap/undist] [--images data/images]
                                   [--steps 7000] [--out runs/splat]

Input is the output of `colmap image_undistorter` (PINHOLE cameras, sparse/ with points3D).
Every 8th image is held out and reported as PSNR. Writes <out>/splat.ply, <out>/eval/*.png and
<out>/train.log. Needs a CUDA GPU; sized for 8 GB (images <= 1600 px, ~1M gaussians).
"""
import argparse
import math
import pathlib
import time

import imageio.v2 as imageio
import numpy as np
import pycolmap
import torch
from scipy.spatial import cKDTree
from torchmetrics.functional.image import structural_similarity_index_measure as ssim

from gsplat import export_splats
from gsplat.rendering import rasterization
from gsplat.strategy import DefaultStrategy

SH_DEGREE = 3


def load_scene(data, min_points=0):
    """Views of the undistorted model; images seen by fewer than `min_points` 3D points have weak poses and are dropped."""
    rec = pycolmap.Reconstruction(str(pathlib.Path(data) / "sparse"))
    views = []
    for im in sorted(rec.images.values(), key=lambda i: i.name):
        if im.num_points3D < min_points:
            continue
        cam = rec.cameras[im.camera_id]
        fx, fy, cx, cy = cam.params
        w2c = np.eye(4)
        w2c[:3] = im.cam_from_world().matrix()
        path = pathlib.Path(data) / "images" / im.name
        views.append({"K": np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], np.float32),
                      "w2c": w2c.astype(np.float32), "img": np.asarray(imageio.imread(path))[..., :3],
                      "name": im.name})
    pts = np.array([p.xyz for p in rec.points3D.values()], np.float32)
    rgb = np.array([p.color for p in rec.points3D.values()], np.float32) / 255
    return views, pts, rgb


def init_params(pts, rgb, device):
    dist = cKDTree(pts).query(pts, k=4)[0][:, 1:].mean(1)  # mean distance to 3 neighbours
    scales = torch.log(torch.from_numpy(dist).float().clamp(min=1e-6))[:, None].repeat(1, 3)
    n = len(pts)
    quats = torch.rand(n, 4)
    sh = torch.zeros(n, (SH_DEGREE + 1) ** 2, 3)
    sh[:, 0] = (torch.from_numpy(rgb) - 0.5) / 0.28209479177387814
    return torch.nn.ParameterDict({
        "means": torch.nn.Parameter(torch.from_numpy(pts)),
        "scales": torch.nn.Parameter(scales),
        "quats": torch.nn.Parameter(quats),
        "opacities": torch.nn.Parameter(torch.logit(torch.full((n,), 0.1))),
        "sh0": torch.nn.Parameter(sh[:, :1]),
        "shN": torch.nn.Parameter(sh[:, 1:]),
    }).to(device)


def render(params, view, sh_degree, device):
    img = view["img"]
    h, w = img.shape[:2]
    out, alpha, info = rasterization(
        params["means"], params["quats"], torch.exp(params["scales"]), torch.sigmoid(params["opacities"]),
        torch.cat([params["sh0"], params["shN"]], 1),
        torch.from_numpy(view["w2c"]).to(device)[None], torch.from_numpy(view["K"]).to(device)[None],
        w, h, sh_degree=sh_degree, packed=False)
    return out[0].clamp(0, 1), info


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", default="data/colmap/undist")
    p.add_argument("--steps", type=int, default=7000)
    p.add_argument("--out", default="runs/splat")
    p.add_argument("--min-points", type=int, default=0, help="drop images with fewer 3D points than this")
    p.add_argument("--grow-grad2d", type=float, default=2e-4, help="lower = more densification")
    a = p.parse_args()

    device = "cuda"
    out = pathlib.Path(a.out)
    (out / "eval").mkdir(parents=True, exist_ok=True)
    views, pts, rgb = load_scene(a.data, a.min_points)
    train = [v for k, v in enumerate(views) if k % 8]
    held = [v for k, v in enumerate(views) if k % 8 == 0]
    centers = np.array([-v["w2c"][:3, :3].T @ v["w2c"][:3, 3] for v in views])
    scene_scale = float(np.linalg.norm(centers - centers.mean(0), axis=1).max()) * 1.1
    print(f"{len(train)} train / {len(held)} held-out views, {len(pts)} init points, scene_scale {scene_scale:.2f}")

    params = init_params(pts, rgb, device)
    lrs = {"means": 1.6e-4 * scene_scale, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2,
           "sh0": 2.5e-3, "shN": 2.5e-3 / 20}
    opts = {k: torch.optim.Adam([{"params": params[k], "lr": lr, "name": k}], eps=1e-15) for k, lr in lrs.items()}
    strategy = DefaultStrategy(refine_stop_iter=a.steps // 2, reset_every=max(a.steps // 3, 1000),
                               grow_grad2d=a.grow_grad2d)
    strategy.check_sanity(params, opts)
    state = strategy.initialize_state(scene_scale=scene_scale)
    decay = 0.01 ** (1 / a.steps)  # means lr decays to 1% of its start

    started = time.time()
    for step in range(a.steps):
        view = train[np.random.randint(len(train))]
        target = torch.from_numpy(view["img"]).to(device).float() / 255
        sh_degree = min(step // 1000, SH_DEGREE)
        img, info = render(params, view, sh_degree, device)
        strategy.step_pre_backward(params, opts, state, step, info)
        loss = 0.8 * (img - target).abs().mean() + 0.2 * (1 - ssim(img[None].permute(0, 3, 1, 2),
                                                                      target[None].permute(0, 3, 1, 2)))
        loss.backward()
        strategy.step_post_backward(params, opts, state, step, info, packed=False)
        for o in opts.values():
            o.step()
            o.zero_grad(set_to_none=True)
        for g in opts["means"].param_groups:
            g["lr"] *= decay
        if step % 250 == 0 or step == a.steps - 1:
            print(f"step {step:5d} loss {loss.item():.4f} gaussians {len(params['means'])} "
                  f"vram {torch.cuda.max_memory_allocated() / 2**30:.1f} GB {time.time() - started:.0f}s", flush=True)

    psnrs = []
    with torch.no_grad():
        for k, view in enumerate(held):
            img, _ = render(params, view, SH_DEGREE, device)
            target = torch.from_numpy(view["img"]).to(device).float() / 255
            mse = ((img - target) ** 2).mean().item()
            psnrs.append(-10 * math.log10(max(mse, 1e-10)))
            if k < 6:
                side = torch.cat([target, img], 1).cpu().numpy()
                imageio.imwrite(out / "eval" / f"{view['name']}.png", (side * 255).astype(np.uint8))
    print(f"held-out PSNR mean {np.mean(psnrs):.2f} dB over {len(psnrs)} views (min {min(psnrs):.2f}, max {max(psnrs):.2f})")

    with torch.no_grad():
        export_splats(params["means"], params["scales"], params["quats"], params["opacities"],
                      params["sh0"], params["shN"], format="ply", save_to=str(out / "splat.ply"))
    print(f"wrote {out / 'splat.ply'}")


if __name__ == "__main__":
    main()
