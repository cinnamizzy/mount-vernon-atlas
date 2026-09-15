#!/usr/bin/env python
"""Sample ground elevation for every routing node and building centroid.

Source: USGS 3DEP Bare Earth DEM ImageServer, 1 m resolution, public domain.
This is TERRAIN, not building height - see the note at the bottom of this file.

    py pipeline/elevation.py
    py pipeline/elevation.py --batch 400 --pause 0.5

Writes data/elevation.json (shipped - the browser loads it):
    { "nodes": {"<lng,lat key>": metres}, "buildings": {"way/123": metres}, ... }

Consumed by classify.py (stamps ground_elevation_m onto buildings) and by the
routing graph, which reports ascent and steep sections as ADVISORY information.
Gradient deliberately does not filter the step-free graph - see the reasoning in
config/routing.json under "gradient".

Stdlib only. Samples come back out of order, so they are matched on coordinates
rather than on position in the response.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SERVICE = ("https://elevation.nationalmap.gov/arcgis/rest/services/"
           "3DEPElevation/ImageServer/getSamples")
ATTRIBUTION = "USGS 3D Elevation Program (3DEP) Bare Earth DEM"


def strip_comments(obj):
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def key_of(coord, precision):
    return "%.*f,%.*f" % (precision, coord[0], precision, coord[1])


def lines(geometry):
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def centroid(geometry):
    xs, ys = [], []

    def visit(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for part in c:
                visit(part)

    visit(geometry["coordinates"])
    return [(min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2]


def sample(points, precision, pause, verbose):
    """points: list of [lng, lat]. Returns {key: metres} for those resolved."""
    geometry = {"points": [[round(x, 7), round(y, 7)] for x, y in points],
                "spatialReference": {"wkid": 4326}}
    params = urllib.parse.urlencode({
        "geometry": json.dumps(geometry),
        "geometryType": "esriGeometryMultipoint",
        "returnFirstValueOnly": "true",
        "f": "json",
    }).encode()
    request = urllib.request.Request(
        SERVICE, data=params,
        headers={"User-Agent": "mount-vernon-atlas/1.0 (pipeline/elevation.py)"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(json.dumps(payload["error"])[:300])

    out = {}
    for s in payload.get("samples", []):
        location = s.get("location") or {}
        value = s.get("value")
        if value in (None, "", "NoData"):
            continue
        try:
            metres = float(value)
        except (TypeError, ValueError):
            continue
        # 3DEP returns NoData as a large negative sentinel in some tiles.
        if metres < -1000:
            continue
        out[key_of([location["x"], location["y"]], precision)] = round(metres, 2)
    if pause:
        time.sleep(pause)
    return out


def collect(points, label, precision, batch, pause, verbose):
    resolved = {}
    total = len(points)
    for start in range(0, total, batch):
        chunk = points[start:start + batch]
        try:
            resolved.update(sample(chunk, precision, pause, verbose))
        except (urllib.error.URLError, RuntimeError, TimeoutError) as err:
            print("  %s batch %d-%d failed: %s" % (label, start, start + len(chunk), err))
            continue
        done = min(start + batch, total)
        print("  %s %d/%d sampled (%d resolved)" % (label, done, total, len(resolved)))
    return resolved


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", type=int, default=400, help="points per request")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between requests")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "elevation.json"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    config = strip_comments(load("config/routing.json"))
    precision = config["coordinate_precision"]

    paths = load("layers/paths.geojson")
    buildings = load("layers/buildings.geojson")

    node_points = {}
    for f in paths["features"]:
        if not f["properties"].get("routable"):
            continue
        for line in lines(f["geometry"]):
            for c in line:
                node_points.setdefault(key_of(c, precision), c)

    building_points = {f["id"]: centroid(f["geometry"]) for f in buildings["features"]}

    print("Sampling %s\n  %d routing nodes\n  %d building centroids\n"
          % (ATTRIBUTION, len(node_points), len(building_points)))

    nodes = collect(list(node_points.values()), "nodes", precision,
                    args.batch, args.pause, args.verbose)

    ids = list(building_points.keys())
    coords = [building_points[i] for i in ids]
    sampled = collect(coords, "buildings", precision, args.batch, args.pause, args.verbose)
    by_id = {}
    for bid, coord in zip(ids, coords):
        value = sampled.get(key_of(coord, precision))
        if value is not None:
            by_id[bid] = value

    payload = {
        "source": ATTRIBUTION,
        "source_url": SERVICE,
        "license": "public domain (US Government work)",
        "resolution_m": 1,
        "sampled": time.strftime("%Y-%m-%d"),
        "measures": "ground elevation above sea level, metres",
        "note": ("Bare-earth terrain. This is NOT building height - deriving that "
                 "needs a surface model (DSM) or lidar point cloud, or estate plans."),
        "coordinate_precision": precision,
        "nodes": nodes,
        "buildings": by_id,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    values = list(nodes.values())
    print("\n%d/%d nodes and %d/%d buildings resolved -> %s (%.1f KB)"
          % (len(nodes), len(node_points), len(by_id), len(building_points),
             args.out, os.path.getsize(args.out) / 1024))
    if values:
        print("elevation range: %.1f m to %.1f m across the routing network"
              % (min(values), max(values)))
    print("\nNext: py pipeline/classify.py   (attaches ground_elevation_m to buildings)")


if __name__ == "__main__":
    main()

