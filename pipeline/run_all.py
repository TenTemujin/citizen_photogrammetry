#!/usr/bin/env python3
"""One-shot routine: coverage report, chunk export and Mapillary photo fetch, with logs.

Usage:
    python pipeline/run_all.py                      # defaults: central Helsinki, 2019 photos, dry run
    python pipeline/run_all.py --download           # actually download the photos
    python pipeline/run_all.py --bbox S W N E --since 2019-01-01 --until 2019-12-31 --limit 300

The Mapillary token is read from the MAPILLARY_TOKEN environment variable, then from .env, and
otherwise asked for with hidden input (and optionally saved to .env, which git ignores).
The token is passed to the steps through their environment only, never on a command line, and
is masked in every log.

Everything is written to runs/<timestamp>/: run.log, one log per step, manifest.json, the chunks
and attribution.csv. After a successful chunk step the chunks are also copied to ./chunks so the
web client sees them (use --no-publish to skip).
"""
import argparse
import datetime
import getpass
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIPELINE = ROOT / "pipeline"
ENV_FILE = ROOT / ".env"
TOKEN_VAR = "MAPILLARY_TOKEN"
IMAGES_DIR = ROOT / "data" / "images"  # shared cache: already-downloaded photos are skipped
TILE_DEG = 0.0025  # same tile size as coverage.py; default image area is the south-west tile


def read_env_file():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


MAPILLARY = "https://graph.mapillary.com/images"


def token_valid(token):
    """True or False when the API says so; None when it could not tell (network or server error)."""
    qs = urllib.parse.urlencode({"bbox": "24.93,60.165,24.9325,60.1675", "fields": "id",
                                 "limit": 1, "access_token": token})
    req = urllib.request.Request(f"{MAPILLARY}?{qs}", headers={"User-Agent": "citizen-photogrammetry/0.1"})
    try:
        urllib.request.urlopen(req, timeout=30).close()
        return True
    except urllib.error.HTTPError as err:
        try:
            code = json.load(err).get("error", {}).get("code")
        except ValueError:
            code = None
        return False if code == 190 else None  # 190 = OAuthException: token rejected
    except OSError:
        return None


def clean(token):
    return (token or "").strip().strip('"').strip("'")


def get_token():
    """Return a token the API accepts (or could not check), asking up to 3 times; never saves a rejected one."""
    token = clean(os.environ.get(TOKEN_VAR) or read_env_file().get(TOKEN_VAR))
    prompted = False
    for _ in range(3):
        if token:
            ok = token_valid(token)
            if ok is not False:
                if ok is None:
                    print("could not verify the Mapillary token (network or server problem); continuing")
                if prompted and input("Save it to .env (plain text, git-ignored) so you are not asked again? [y/N] ").strip().lower() == "y":
                    ENV_FILE.write_text(f"{TOKEN_VAR}={token}\n", encoding="utf-8")
                    print(f"saved to {ENV_FILE}")
                return token
            print("Mapillary rejected this token (Invalid OAuth access token). "
                  "Copy the full 'MLY|...' value from the Access Token column.")
        if not sys.stdin.isatty():
            return None
        token = clean(getpass.getpass("Mapillary token (input is hidden; paste and press Enter, blank to skip): "))
        prompted = True
        if not token:
            return None
    return None


def run_step(name, script, args, run_dir, log_all, env, token):
    """Run one pipeline script, tee its output to the console and logs, and return the result."""
    cmd = [sys.executable, "-X", "utf8", str(PIPELINE / script), *map(str, args)]
    shown = " ".join(cmd[3:])
    header = f"\n=== {name}: {script} {shown}"
    print(header)
    log_all.write(header + "\n")
    started = time.time()
    code = 0
    with open(run_dir / f"{name}.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        for line in proc.stdout:
            if token:
                line = line.replace(token, "***")
            print("  " + line.rstrip())
            log.write(line)
            log_all.write(line)
        code = proc.wait()
    seconds = round(time.time() - started, 1)
    log_all.write(f"--- {name} exit {code} in {seconds}s\n")
    log_all.flush()
    return {"name": name, "script": script, "args": [str(a) for a in args], "exit": code,
            "seconds": seconds, "log": f"{name}.log"}


def publish_chunks(src):
    dest = ROOT / "chunks"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"  copied chunks to {dest}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                   default=[60.165, 24.930, 60.174, 24.948], help="area for coverage and chunks")
    p.add_argument("--image-bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                   help="area for photos (default: the south-west 0.0025 deg tile of --bbox)")
    p.add_argument("--since", default="2019-01-01")
    p.add_argument("--until", default="2019-12-31")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--download", action="store_true", help="download photos (default is a dry run)")
    p.add_argument("--no-publish", action="store_true", help="do not copy chunks to ./chunks")
    p.add_argument("--skip", nargs="*", default=[], choices=["coverage", "chunks", "images"])
    a = p.parse_args()

    s, w, n, e = a.bbox
    image_bbox = a.image_bbox or [s, w, s + TILE_DEG, w + TILE_DEG]
    token = get_token()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if token:
        env[TOKEN_VAR] = token

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / stamp
    run_dir.mkdir(parents=True)
    steps = []
    with open(run_dir / "run.log", "w", encoding="utf-8") as log_all:
        log_all.write(f"run {stamp}\nbbox {a.bbox}\nimage_bbox {image_bbox}\n"
                      f"mapillary token: {'yes' if token else 'no'}\n")
        if "coverage" not in a.skip:
            steps.append(run_step("coverage", "coverage.py", a.bbox, run_dir, log_all, env, token))
        if "chunks" not in a.skip:
            res = run_step("chunks", "chunks.py", [*a.bbox, "--out", run_dir / "chunks"], run_dir, log_all, env, token)
            steps.append(res)
            if res["exit"] == 0 and not a.no_publish:
                publish_chunks(run_dir / "chunks")
        if "images" not in a.skip:
            if not token:
                msg = "images: skipped, no Mapillary token"
                print("\n" + msg)
                log_all.write(msg + "\n")
                steps.append({"name": "images", "exit": None, "skipped": "no token"})
            else:
                args = [*image_bbox, "--since", a.since, "--until", a.until, "--limit", a.limit,
                        "--out", IMAGES_DIR]
                if not a.download:
                    args.append("--dry-run")
                res = run_step("images", "fetch_images.py", args, run_dir, log_all, env, token)
                steps.append(res)
                attribution = IMAGES_DIR / "attribution.csv"
                if res["exit"] == 0 and a.download and attribution.exists():
                    shutil.copy(attribution, run_dir / "attribution.csv")

    manifest = {"started": stamp, "bbox": a.bbox, "image_bbox": image_bbox, "since": a.since,
                "until": a.until, "limit": a.limit, "download": a.download,
                "token_used": bool(token), "images_dir": str(IMAGES_DIR), "steps": steps}
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("\n=== summary")
    for st in steps:
        status = "skipped" if st["exit"] is None else ("ok" if st["exit"] == 0 else f"FAILED ({st['exit']})")
        print(f"  {st['name']:<9} {status}")
    print(f"logs and exports: {run_dir}")
    return 1 if any(st["exit"] for st in steps) else 0


if __name__ == "__main__":
    sys.exit(main())
