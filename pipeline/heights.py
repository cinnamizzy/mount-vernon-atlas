#!/usr/bin/env python
"""Derive real building heights from USGS 3DEP lidar.

    py pipeline/heights.py
    py pipeline/heights.py --depth 10 --percentile 90

Source: the VA_NorthernVA_1_B22 point cloud in USGS's public EPT store, which
covers the estate. Writes build/heights.json, which pipeline/classify.py picks up
and stamps onto every building as height_m.

Why this matters: NO source building carries a height tag - 0 of 1,173. The
original project invented every height and baked them into a scene file that was
then lost. These are measured.

Method, per footprint:
  ground = median Z of lidar-classified ground points in a ring around the
           building (never inside it, so the walls cannot drag it up)
  roof   = high percentile of Z of points classified BUILDING (6) inside it
  height = roof - ground

Using class 6 rather than "anything that is not ground" matters enormously here.
This tile set classifies ground (2) and building (6) but leaves vegetation in
"unassigned" (1), and the estate is heavily wooded. Counting non-ground returns
as roof measured the tree canopy overhanging each footprint instead: it put the
median building at 9.1 m and called half of them "complex" roofs.

Ground and roof come from the SAME point cloud, so the vertical datum cancels
and no datum conversion is needed. Note EPT stores XY in EPSG:3857 while Z is in
true metres; only Z differences are used, so the Mercator scale factor never
enters the height.

Requires: laspy[lazrs]  (pip install "laspy[lazrs]")
Downloads are cached under build/ept-cache/ so re-runs are cheap.
"""
import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np

try:
    import laspy
except ImportError:
    sys.exit('laspy is required:  pip install "laspy[lazrs]"')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/VA_NorthernVA_1_B22"
CACHE = os.path.join(ROOT, "build", "ept-cache")
R = 6378137.0

GROUND_CLASS = 2
BUILDING_CLASS = 6
NOISE_CLASSES = {7, 18}  # low/high noise


def merc(lon, lat):
    return (lon * R * math.pi / 180,
            R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def fetch_json(path):
    with urllib.request.urlopen(BASE + "/" + path, timeout=90) as response:
        return json.load(response)


def fetch_node(key):
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, key + ".laz")
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    url = "%s/ept-data/%s.laz" % (BASE, key)
    try:
        with urllib.request.urlopen(url, timeout=180) as response, open(local, "wb") as fh:
            fh.write(response.read())
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise
    return local


def outer_rings(geometry):
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    return []


def inside_mask(xs, ys, ring):
    """Vectorised even-odd point-in-polygon."""
    result = np.zeros(xs.shape, dtype=bool)
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if y0 == y1:
            continue
        crosses = (y0 > ys) != (y1 > ys)
        if not crosses.any():
            continue
        xint = x0 + (ys - y0) * (x1 - x0) / (y1 - y0)
        result ^= crosses & (xs < xint)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--depth", type=int, default=10,
                    help="deepest octree level to fetch (10 = ~0.9 m resolution)")
    ap.add_argument("--percentile", type=float, default=90.0,
                    help="percentile of roof points taken as the height")
    ap.add_argument("--ring", type=float, default=12.0,
                    help="metres around a footprint searched for ground points")
    ap.add_argument("--min-points", type=int, default=8,
                    help="fewest roof points for a usable height")
    ap.add_argument("--workers", type=int, default=12,
                    help="parallel downloads (default 12)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report node count and download size, fetch nothing")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "heights.json"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "layers", "buildings.geojson"), encoding="utf-8") as fh:
        buildings = json.load(fh)["features"]

    # Footprints into EPT's coordinate system, with a ring for ground sampling.
    # The ring is in Mercator units, which are stretched by 1/cos(lat) here; that
    # only makes the ground search slightly wider than asked, which is harmless.
    parcels = []
    for feature in buildings:
        rings = [[merc(x, y) for x, y in ring] for ring in outer_rings(feature["geometry"])]
        rings = [r for r in rings if len(r) >= 4]
        if not rings:
            continue
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        # Shoelace in Mercator, scaled back to true ground area.
        merc_area = 0.0
        for r in rings:
            merc_area += abs(sum(r[i][0] * r[(i + 1) % len(r)][1]
                                 - r[(i + 1) % len(r)][0] * r[i][1]
                                 for i in range(len(r)))) / 2
        parcels.append({
            "id": feature["id"],
            "rings": rings,
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "area": merc_area * math.cos(math.radians(38.71)) ** 2,
            "roof": [],
            "other": [],
            "ground": [],
        })
    print("%d building footprints" % len(parcels))

    ept = fetch_json("ept.json")
    b = ept["bounds"]
    root_size = b[3] - b[0]
    print("EPT: %s points, span %d, root %.0f m" % (format(ept["points"], ","),
                                                    ept["span"], root_size))

    ring_pad = args.ring / math.cos(math.radians(38.71))
    targets = [(p["bbox"][0] - ring_pad, p["bbox"][1] - ring_pad,
                p["bbox"][2] + ring_pad, p["bbox"][3] + ring_pad) for p in parcels]
    tx0 = min(t[0] for t in targets); ty0 = min(t[1] for t in targets)
    tx1 = max(t[2] for t in targets); ty1 = max(t[3] for t in targets)

    def node_box(key):
        d, x, y, _z = map(int, key.split("-"))
        size = root_size / (2 ** d)
        return (b[0] + x * size, b[1] + y * size, b[0] + (x + 1) * size, b[1] + (y + 1) * size)

    def hits_any_building(box):
        nx0, ny0, nx1, ny1 = box
        if nx1 < tx0 or nx0 > tx1 or ny1 < ty0 or ny0 > ty1:
            return False
        for t in targets:
            if not (nx1 < t[0] or nx0 > t[2] or ny1 < t[1] or ny0 > t[3]):
                return True
        return False

    # Walk the hierarchy, descending only where buildings actually are.
    print("\nwalking octree to depth %d..." % args.depth)
    hierarchy = dict(fetch_json("ept-hierarchy/0-0-0-0.json"))
    pending = [k for k, v in hierarchy.items() if v == -1]
    while pending:
        key = pending.pop()
        depth = int(key.split("-")[0])
        if depth > args.depth or not hits_any_building(node_box(key)):
            continue
        sub = fetch_json("ept-hierarchy/%s.json" % key)
        hierarchy.update(sub)
        pending.extend(k for k, v in sub.items() if v == -1)

    wanted = []
    for key, count in hierarchy.items():
        if count <= 0:
            continue
        if int(key.split("-")[0]) > args.depth:
            continue
        if hits_any_building(node_box(key)):
            wanted.append((key, count))
    wanted.sort(key=lambda kc: int(kc[0].split("-")[0]))
    total_pts = sum(c for _, c in wanted)
    print("%d nodes touch a building, %s points (~%.0f MB)"
          % (len(wanted), format(total_pts, ","), total_pts * 5 / 1e6))

    # Index parcels on a coarse grid so each node only tests nearby footprints.
    CELL = 200.0
    grid = defaultdict(list)
    for i, t in enumerate(targets):
        for cx in range(int(t[0] // CELL), int(t[2] // CELL) + 1):
            for cy in range(int(t[1] // CELL), int(t[3] // CELL) + 1):
                grid[(cx, cy)].append(i)

    cached = sum(1 for k, _ in wanted
                 if os.path.exists(os.path.join(CACHE, k + ".laz")))
    print("%d already cached, %d to download" % (cached, len(wanted) - cached))

    if args.dry_run:
        print("\n--dry-run: nothing downloaded.")
        return

    # Download in parallel. Sequentially this is twenty minutes of silence, which
    # is indistinguishable from a hang; cached nodes return immediately.
    print("\ndownloading (%d workers)..." % args.workers)
    done = [0]
    lock = threading.Lock()
    started = time.time()
    fetched = {}

    def grab(item):
        key, _count = item
        try:
            path = fetch_node(key)
        except Exception as err:
            path = None
            with lock:
                print("  %s failed: %s" % (key, err))
        with lock:
            done[0] += 1
            if done[0] % 20 == 0 or done[0] == len(wanted):
                mb = sum(os.path.getsize(p) for p in fetched.values() if p) / 1e6
                print("  %d/%d nodes  %.0f MB  %.0fs"
                      % (done[0], len(wanted), mb, time.time() - started))
            fetched[key] = path
        return key, path

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(grab, wanted))

    print("\nreading nodes...")
    read = skipped = 0
    for n, (key, _count) in enumerate(wanted, 1):
        path = fetched.get(key)
        if not path:
            skipped += 1
            continue
        try:
            with laspy.open(path) as reader:
                las = reader.read()
        except Exception as err:                      # a truncated cache entry
            os.remove(path)
            print("  %s unreadable (%s) - removed from cache" % (key, err))
            skipped += 1
            continue
        read += 1

        xs = np.asarray(las.x, dtype=np.float64)
        ys = np.asarray(las.y, dtype=np.float64)
        zs = np.asarray(las.z, dtype=np.float64)
        cls = np.asarray(las.classification, dtype=np.uint8)
        keep = ~np.isin(cls, list(NOISE_CLASSES))
        xs, ys, zs, cls = xs[keep], ys[keep], zs[keep], cls[keep]
        if not len(xs):
            continue

        nx0, ny0, nx1, ny1 = node_box(key)
        candidates = set()
        for cx in range(int(nx0 // CELL), int(nx1 // CELL) + 1):
            for cy in range(int(ny0 // CELL), int(ny1 // CELL) + 1):
                candidates.update(grid.get((cx, cy), ()))

        for i in candidates:
            parcel = parcels[i]
            t = targets[i]
            near = (xs >= t[0]) & (xs <= t[2]) & (ys >= t[1]) & (ys <= t[3])
            if not near.any():
                continue
            px, py, pz, pc = xs[near], ys[near], zs[near], cls[near]
            inner = np.zeros(px.shape, dtype=bool)
            for ring in parcel["rings"]:
                inner |= inside_mask(px, py, ring)
            # Class 6 only. "Not ground" would include the tree canopy over the
            # footprint, which this dataset leaves in class 1 alongside everything
            # else it did not classify.
            roof = inner & (pc == BUILDING_CLASS)
            if roof.any():
                parcel["roof"].append(pz[roof])
            # Kept for the fallback below: small outbuildings often get no
            # building class at all, and everything unclassified lands here.
            other = inner & (pc != BUILDING_CLASS) & (pc != GROUND_CLASS)
            if other.any():
                parcel["other"].append(pz[other])
            ground = (~inner) & (pc == GROUND_CLASS)
            if ground.any():
                parcel["ground"].append(pz[ground])

        if n % 50 == 0 or n == len(wanted):
            measured = sum(1 for p in parcels if p["roof"])
            print("  %d/%d nodes read, %d footprints have roof points"
                  % (n, len(wanted), measured))

    print("read %d nodes (%d skipped)" % (read, skipped))

    # 3DEP terrain as a fallback ground reference where lidar ground is missing.
    fallback = {}
    elevation_path = os.path.join(ROOT, "data", "elevation.json")
    if os.path.exists(elevation_path):
        with open(elevation_path, encoding="utf-8") as fh:
            fallback = json.load(fh).get("buildings", {})

    def modal_roof(heights_above_ground, area):
        """Roof height from unclassified returns, for footprints the classifier missed.

        A roof is a dense band of returns at one level; canopy above it is
        diffuse and spread over many metres. So: bound the search by what a
        footprint of this size could plausibly be, histogram what is left, and
        take the densest band as the roof rather than the highest return.
        """
        cap = min(14.0, 2.5 + 1.4 * math.sqrt(max(area, 1.0)))
        band = heights_above_ground[(heights_above_ground >= 1.0)
                                    & (heights_above_ground <= cap)]
        if len(band) < 20:
            return None
        bins = np.arange(1.0, cap + 0.5, 0.5)
        counts, edges = np.histogram(band, bins=bins)
        if not counts.size or counts.max() == 0:
            return None
        mode = edges[int(np.argmax(counts))]
        cluster = band[(band >= mode - 1.5) & (band <= mode + 2.5)]
        if len(cluster) < 10:
            return None
        return float(np.percentile(cluster, 90))

    results = {}
    stats = defaultdict(int)
    for parcel in parcels:
        roof_z = np.concatenate(parcel["roof"]) if parcel["roof"] else np.array([])
        classified = len(roof_z) >= args.min_points
        if not classified and not parcel["other"]:
            stats["no_points"] += 1
            continue

        if parcel["ground"]:
            ground_z = np.concatenate(parcel["ground"])
            ground = float(np.median(ground_z))
            ground_source = "lidar_ground_class"
            ground_n = int(len(ground_z))
        elif parcel["id"] in fallback:
            ground = float(fallback[parcel["id"]])
            ground_source = "usgs_3dep_dem"
            ground_n = 0
        else:
            stats["no_ground"] += 1
            continue

        if classified:
            height = float(np.percentile(roof_z, args.percentile)) - ground
            spread = float(np.percentile(roof_z, 95) - np.percentile(roof_z, 25))
            n_roof = int(len(roof_z))
            confidence = "high" if n_roof >= 120 else "medium" if n_roof >= 30 else "low"
            method = ("p%g of lidar building-class returns inside the footprint, "
                      "minus median ground in a %g m ring" % (args.percentile, args.ring))
            measured_from = "building_class"
            surveyed = True
        else:
            other_z = np.concatenate(parcel["other"])
            height = modal_roof(other_z - ground, parcel["area"])
            if height is None:
                stats["no_roof_cluster"] += 1
                continue
            band = other_z - ground
            near = band[(band >= height - 1.5) & (band <= height + 1.0)]
            spread = float(np.percentile(near, 95) - np.percentile(near, 25)) if len(near) > 4 else 0.0
            n_roof = int(len(near))
            confidence = "low"
            method = ("densest return band inside the footprint, bounded by footprint "
                      "size - the lidar classifier gave this building no building-class "
                      "returns, so canopy could not be excluded by class")
            measured_from = "modal_cluster"
            surveyed = False

        if not (1.5 <= height <= 60):
            stats["implausible"] += 1
            continue

        form = "flat" if spread < 1.2 else ("pitched" if spread < 6 else "complex")
        results[parcel["id"]] = {
            "height_m": round(height, 1),
            "ground_m": round(ground, 2),
            "roof_form": form,
            "roof_spread_m": round(spread, 2),
            "points": n_roof,
            "ground_points": ground_n,
            "ground_source": ground_source,
            "measured_from": measured_from,
            "confidence": confidence,
            "source": "USGS 3DEP lidar VA_NorthernVA_1_B22",
            "method": method,
            "surveyed": surveyed,
        }
        stats[confidence] += 1
        stats["from_" + measured_from] += 1

    payload = {
        "source": "USGS 3DEP lidar, VA_NorthernVA_1_B22 (public domain)",
        "source_url": BASE,
        "derived": "building height above local ground",
        "octree_depth": args.depth,
        "percentile": args.percentile,
        "buildings": results,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)

    heights = [v["height_m"] for v in results.values()]
    print("\n%d of %d buildings measured -> %s" % (len(results), len(parcels), args.out))
    if heights:
        arr = np.array(heights)
        print("  height  min %.1f  median %.1f  p90 %.1f  max %.1f m"
              % (arr.min(), np.median(arr), np.percentile(arr, 90), arr.max()))
        forms = defaultdict(int)
        for v in results.values():
            forms[v["roof_form"]] += 1
        print("  roof forms:", dict(forms))
        print("  confidence:", {k: stats[k] for k in ("high", "medium", "low") if stats[k]})
    dropped = {k: v for k, v in stats.items() if k not in ("high", "medium", "low")}
    if dropped:
        print("  not measured:", dropped)
    print("\nNext: py pipeline/classify.py")


if __name__ == "__main__":
    main()
