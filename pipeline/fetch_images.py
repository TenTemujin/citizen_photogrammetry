#!/usr/bin/env python3
"""Phase 4 input: download Mapillary photos for a small bbox, recording attribution per image.

Usage:
    MAPILLARY_TOKEN=... python pipeline/fetch_images.py SOUTH WEST NORTH EAST [--limit 200] [--out data/images]
    add --dry-run to only count what would be downloaded.

Writes <out>/<id>.jpg and <out>/attribution.csv (id, username, captured_at, license, url).
Panoramas are skipped (equirectangular images break standard SfM). Re-running skips files already
downloaded. Keep attribution.csv with any model trained from these photos (CC-BY-SA 4.0).
"""
import argparse
import csv
import datetime
import json
import os
import pathlib
import sys
import urllib.parse
import urllib.request

MAPILLARY = "https://graph.mapillary.com/images"
FIELDS = "id,captured_at,is_pano,sequence,thumb_2048_url,creator"
LICENSE = "CC-BY-SA-4.0"  # Mapillary's published license for contributed imagery; verify before release
PAGE = 2000


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "citizen-photogrammetry/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as err:  # show the API's own message, not a traceback (the URL holds the token)
        raise SystemExit(f"HTTP {err.code} from {urllib.parse.urlparse(url).netloc}: {err.read()[:300].decode('utf-8', 'replace')}")


def list_images(token, s, w, n, e):
    qs = urllib.parse.urlencode({
        "bbox": f"{w},{s},{e},{n}", "fields": FIELDS, "limit": PAGE, "access_token": token,
    })
    return http_json(f"{MAPILLARY}?{qs}").get("data", [])


def captured(img):
    return datetime.datetime.fromtimestamp(img["captured_at"] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")


def pick(images, limit):
    """Drop panoramas and images without a download URL, then thin evenly down to `limit`."""
    usable = [i for i in images if not i.get("is_pano") and i.get("thumb_2048_url")]
    usable.sort(key=lambda i: (i.get("sequence", ""), i["captured_at"]))
    if len(usable) <= limit:
        return usable
    step = len(usable) / limit
    return [usable[int(k * step)] for k in range(limit)]


def write_attribution(path, rows):
    """Merge rows into attribution.csv by id, so photos from earlier runs keep their credit."""
    merged = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            merged = {row[0]: row for row in list(csv.reader(f))[1:] if row}
    merged.update({row[0]: row for row in rows})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "username", "captured_at", "license", "url"])
        w.writerows(merged.values())
    return len(merged)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("south", "west", "north", "east"):
        p.add_argument(name, type=float)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--out", default="data/images")
    p.add_argument("--since", help="keep images captured on/after YYYY-MM-DD")
    p.add_argument("--until", help="keep images captured on/before YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    token = os.environ.get("MAPILLARY_TOKEN")
    if not token:
        sys.exit("set MAPILLARY_TOKEN")

    images = list_images(token, a.south, a.west, a.north, a.east)
    usable = [i for i in images if not i.get("is_pano") and i.get("thumb_2048_url")]
    # photos from different years differ in light, season and construction; SfM works best on one window
    by_year = {}
    for i in usable:
        by_year.setdefault(captured(i)[:4], []).append(i)
    print("usable per year: " + "  ".join(f"{y}:{len(v)}" for y, v in sorted(by_year.items())))
    total = len(images)
    if a.since:
        images = [i for i in images if captured(i) >= a.since]
    if a.until:
        images = [i for i in images if captured(i) <= a.until]
    if a.since or a.until:
        by_month = {}
        for i in images:
            if not i.get("is_pano") and i.get("thumb_2048_url"):
                by_month[captured(i)[:7]] = by_month.get(captured(i)[:7], 0) + 1
        print("usable per month in range: " + "  ".join(f"{m}:{n}" for m, n in sorted(by_month.items())))
    chosen = pick(images, a.limit)
    sequences = len({i.get("sequence") for i in chosen})
    dates = sorted(captured(i) for i in chosen)
    print(f"{total} images in bbox, {len(images)} in date range, "
          f"{sum(bool(i.get('is_pano')) for i in images)} panoramas skipped, "
          f"{len(chosen)} selected from {sequences} sequences"
          + (f", dates {dates[0]} to {dates[-1]}" if dates else ""))
    if len(images) >= PAGE:
        print(f"note: hit the {PAGE}-image page cap; use a smaller bbox to see them all")
    if a.dry_run or not chosen:
        return

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, img in enumerate(chosen, 1):
        dest = out / f"{img['id']}.jpg"
        if not dest.exists():
            urllib.request.urlretrieve(img["thumb_2048_url"], dest)
        rows.append([img["id"], (img.get("creator") or {}).get("username", ""), captured(img), LICENSE,
                     f"https://www.mapillary.com/app/?pKey={img['id']}"])
        if k % 25 == 0:
            print(f"  {k}/{len(chosen)}")
    listed = write_attribution(out / "attribution.csv", rows)
    print(f"done: {len(rows)} images in {out}/, attribution.csv now credits {listed} photos")


if __name__ == "__main__":
    main()
