#!/usr/bin/env python
"""Scatter drawn tree symbols across woodland and parkland.

    py pipeline/scatter.py
    py pipeline/scatter.py --spacing 22 --max 2600

Writes layers/trees.geojson.

THESE TREES ARE DECORATIVE. OpenStreetMap carries only 6 individual trees for
this estate, nowhere near a canopy, so positions are generated on a jittered
grid inside the mapped woodland and park polygons. Every feature is stamped
`surveyed: false`, `decorative: true`, `source: "generated"` so nothing
downstream can mistake them for surveyed planting. They are illustration, in the
same spirit as a pictorial map's drawn canopy - not a tree inventory.

Deterministic: the same seed gives the same forest, so the output is
reproducible and diffs stay meaningful.

Stdlib only.
"""
import argparse
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# class -> (relative density, which sprites may be used)
PLANTING = {
    "woodland":   (1.00, ["tree-oak", "tree-oak", "tree-elm", "tree-pine", "tree-shrub"]),
    "park":       (0.38, ["tree-oak", "tree-elm", "tree-shrub"]),
    "scrub":      (0.55, ["tree-shrub", "tree-shrub", "tree-elm"]),
    "cemetery":   (0.30, ["tree-oak", "tree-elm"]),
    "orchard":    (0.00, []),   # the orchard wash already draws ranked trees
    "recreation": (0.12, ["tree-oak"]),
}

M_PER_DEG_LAT = 110574.0


def rings(geometry):
    """Outer rings and their holes, as ([outer], [holes...]) pairs."""
    if geometry["type"] == "Polygon":
        return [(geometry["coordinates"][0], geometry["coordinates"][1:])]
    if geometry["type"] == "MultiPolygon":
        return [(poly[0], poly[1:]) for poly in geometry["coordinates"]]
    return []


def inside(ring, x, y):
    """Even-odd point-in-ring test."""
    result = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xint:
                result = not result
    return result


def contains(outer, holes, x, y):
    if not inside(outer, x, y):
        return False
    return not any(inside(hole, x, y) for hole in holes)


def bbox(ring):
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spacing", type=float, default=20.0,
                    help="metres between trees in dense woodland (default 20)")
    ap.add_argument("--jitter", type=float, default=0.45,
                    help="fraction of spacing to randomise position by")
    ap.add_argument("--max", type=int, default=3000, help="cap on total symbols")
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument("--out", default=os.path.join(ROOT, "layers", "trees.geojson"))
    args = ap.parse_args()

    with open(os.path.join(ROOT, "layers", "landcover.geojson"), encoding="utf-8") as fh:
        landcover = json.load(fh)

    rng = random.Random(args.seed)
    features = []
    per_class = {}

    for feature in landcover["features"]:
        cls = feature["properties"].get("class")
        if cls not in PLANTING:
            continue
        density, palette = PLANTING[cls]
        if density <= 0 or not palette:
            continue
        step = args.spacing / density

        for outer, holes in rings(feature["geometry"]):
            if len(outer) < 4:
                continue
            west, south, east, north = bbox(outer)
            lat_mid = (south + north) / 2
            m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat_mid))
            if m_per_deg_lon <= 0:
                continue
            dlat = step / M_PER_DEG_LAT
            dlon = step / m_per_deg_lon

            rows = int((north - south) / dlat) + 1
            cols = int((east - west) / dlon) + 1
            if rows * cols > 400000:      # refuse to grind on a huge polygon
                continue

            for r in range(rows):
                for c in range(cols):
                    # offset alternate rows so the grid does not read as a grid
                    y = south + r * dlat
                    x = west + c * dlon + (dlon / 2 if r % 2 else 0)
                    x += rng.uniform(-args.jitter, args.jitter) * dlon
                    y += rng.uniform(-args.jitter, args.jitter) * dlat
                    if not contains(outer, holes, x, y):
                        continue
                    features.append({
                        "type": "Feature",
                        "properties": {
                            "sprite": rng.choice(palette),
                            "scale": round(rng.uniform(0.78, 1.22), 2),
                            "cover": cls,
                            "source": "generated",
                            "method": "jittered grid inside mapped %s polygons" % cls,
                            "decorative": True,
                            "surveyed": False,
                            "confidence": "none",
                        },
                        "geometry": {"type": "Point",
                                     "coordinates": [round(x, 7), round(y, 7)]},
                    })
                    per_class[cls] = per_class.get(cls, 0) + 1

    # Thin uniformly if over the cap, so density stays even rather than clipped
    # in whichever polygon happened to be processed last.
    if len(features) > args.max:
        rng.shuffle(features)
        features = features[:args.max]
        per_class = {}
        for f in features:
            k = f["properties"]["cover"]
            per_class[k] = per_class.get(k, 0) + 1

    # Draw back-to-front so nearer canopies overlap those behind them.
    features.sort(key=lambda f: -f["geometry"]["coordinates"][1])
    for i, f in enumerate(features):
        f["properties"]["order"] = i

    payload = {
        "type": "FeatureCollection",
        "name": "trees",
        "note": ("Decorative canopy symbols generated on a jittered grid inside mapped "
                 "woodland and parkland. Not a tree inventory and not surveyed."),
        "generated_with": {"spacing_m": args.spacing, "jitter": args.jitter,
                           "seed": args.seed, "max": args.max},
        "features": features,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    print("%d tree symbols -> %s (%.1f KB)"
          % (len(features), args.out, os.path.getsize(args.out) / 1024))
    for cls, count in sorted(per_class.items(), key=lambda kv: -kv[1]):
        print("   %-12s %d" % (cls, count))
    print("\nAll marked decorative: true, surveyed: false.")


if __name__ == "__main__":
    main()
