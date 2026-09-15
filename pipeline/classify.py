#!/usr/bin/env python
"""Normalise an OpenStreetMap extract into semantic map layers.

One output file per layer. The rules live in pipeline/rules/*.json - edit those,
not this script, to change how source tags map to map classes.

    py pipeline/classify.py
    py pipeline/classify.py --input build/osm-raw.geojson --out layers

Stdlib only. Every output feature carries source, confidence and surveyed so that
nothing downstream can mistake derived data for surveyed data.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_INPUT = os.path.join(ROOT, "build", "osm-raw.geojson")
DEFAULT_OUT = os.path.join(ROOT, "layers")

AREA_TYPES = ("Polygon", "MultiPolygon")
LINE_TYPES = ("LineString", "MultiLineString")


def strip_comments(obj):
    """Drop $comment keys so rule files can document themselves."""
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


def load_rules(name):
    path = os.path.join(HERE, "rules", name)
    with open(path, encoding="utf-8") as fh:
        return strip_comments(json.load(fh))


def bbox(geometry):
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")

    def visit(coords):
        nonlocal x0, y0, x1, y1
        if coords and isinstance(coords[0], (int, float)):
            x0 = min(x0, coords[0]); x1 = max(x1, coords[0])
            y0 = min(y0, coords[1]); y1 = max(y1, coords[1])
        else:
            for c in coords:
                visit(c)

    visit(geometry["coordinates"])
    return x0, y0, x1, y1


def centre(geometry):
    x0, y0, x1, y1 = bbox(geometry)
    return [(x0 + x1) / 2, (y0 + y1) / 2]


def feature(src, geometry, props, provenance):
    out = {"id": src["id"], **props}
    out.update(provenance)
    return {"type": "Feature", "id": src["id"], "properties": out, "geometry": geometry}


def write_layer(out_dir, name, features, report):
    path = os.path.join(out_dir, name + ".geojson")
    payload = {"type": "FeatureCollection", "name": name, "features": features}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    counts = {}
    for f in features:
        cls = f["properties"].get("class", "?")
        counts[cls] = counts.get(cls, 0) + 1
    report.append((name, len(features), counts, os.path.getsize(path)))


# --- layer extractors -------------------------------------------------------

def extract_paths(features, rules, provenance):
    cfg = rules["paths"]
    class_by = cfg["class_by_highway"]
    not_step_free = set(cfg["not_step_free_classes"])
    routable = set(cfg["routable_classes"])
    excluded = set(cfg["excluded_access_values"])
    access_keys = cfg["access_keys_checked"]
    advisory = set(cfg["advisory_surfaces"])
    profiles = cfg["profiles"]
    width_by = cfg["width_by_class"]
    default_width = cfg["default_width"]

    out = []
    for f in features:
        p = f["properties"]
        hw = p.get("highway")
        if hw not in class_by or f["geometry"]["type"] not in LINE_TYPES:
            continue
        cls = class_by[hw]
        blocked = any(p.get(k) in excluded for k in access_keys)
        surface = p.get("surface")
        step_free = cls not in not_step_free
        allowed = [name for name, classes in profiles.items()
                   if cls in classes and not blocked]
        out.append(feature(f, f["geometry"], {
            "name": p.get("name"),
            "class": cls,
            "highway": hw,
            "surface": surface,
            "base_width": width_by.get(cls, default_width),
            "step_free": step_free,
            "surface_advisory": surface in advisory,
            "routable": cls in routable and not blocked,
            "access_blocked": blocked,
            "profiles": allowed,
        }, provenance))
    return out


def extract_parking(features, rules, provenance):
    cfg = rules["parking"]
    wanted = set(cfg["amenity_values"])
    class_by = cfg["class_by_amenity"]
    out = []
    for f in features:
        p = f["properties"]
        amenity = p.get("amenity")
        if amenity not in wanted or f["geometry"]["type"] not in AREA_TYPES:
            continue
        out.append(feature(f, f["geometry"], {
            "name": p.get("name"),
            "class": class_by.get(amenity, "parking_lot"),
            "capacity": p.get("capacity"),
            "access": p.get("access"),
            "surface": p.get("surface"),
            "parking_type": p.get("parking"),
        }, provenance))
    return out


def extract_buildings(features, rules, provenance, heights, ground):
    cfg = rules["buildings"]
    class_by = cfg["class_by_building"]
    default = cfg["default_class"]
    out = []
    for f in features:
        p = f["properties"]
        b = p.get("building")
        if not b or f["geometry"]["type"] not in AREA_TYPES:
            continue
        h = heights.get(f["id"], {})
        out.append(feature(f, f["geometry"], {
            "name": p.get("name"),
            "class": class_by.get(b, default),
            "building": b,
            # Ground level under the building, from USGS 3DEP (pipeline/elevation.py).
            # Real, surveyed terrain - not to be confused with building height.
            "ground_elevation_m": ground.get(f["id"]),
            # No source building carries a height tag; these stay null until a
            # surface model or estate plans supply them.
            "height_m": h.get("height_m"),
            "height_source": h.get("source"),
            "height_confidence": h.get("confidence"),
            "height_surveyed": h.get("surveyed"),
            # Drives the 3D roof geometry: form picks flat vs pitched, spread
            # gives the ridge rise above the eaves.
            "roof_form": h.get("roof_form"),
            "roof_spread_m": h.get("roof_spread_m"),
        }, provenance))
    return out


def extract_landcover(features, rules, provenance):
    cfg = rules["landcover"]
    keys = cfg["keys"]
    class_by = cfg["class_by_value"]
    default = cfg["default_class"]
    out = []
    for f in features:
        p = f["properties"]
        if p.get("building") or f["geometry"]["type"] not in AREA_TYPES:
            continue
        value = next((p[k] for k in keys if p.get(k)), None)
        if not value:
            continue
        out.append(feature(f, f["geometry"], {
            "name": p.get("name"),
            "class": class_by.get(value, default),
            "source_value": value,
        }, provenance))
    return out


def extract_barriers(features, rules, provenance):
    cfg = rules["barriers"]
    class_by = cfg["class_by_barrier"]
    default = cfg["default_class"]
    out = []
    for f in features:
        p = f["properties"]
        b = p.get("barrier")
        if not b or f["geometry"]["type"] not in LINE_TYPES:
            continue
        out.append(feature(f, f["geometry"], {
            "name": p.get("name"),
            "class": class_by.get(b, default),
            "barrier": b,
            "material": p.get("material") or p.get("fence_type"),
        }, provenance))
    return out


def extract_places(features, place_rules, provenance):
    by_id = {f["id"]: f for f in features}
    highlights = place_rules["highlights"]
    highlight_ids = {h["id"] for h in highlights}
    west, south, east, north = place_rules["core_bounds"]["bbox"]
    compiled = [(re.compile(r["pattern"], re.I), r["category"])
                for r in place_rules["category_rules"]]
    default_cat = place_rules["default_category"]
    audio_default = place_rules.get("audio_url_default")

    def categorise(name):
        for pattern, category in compiled:
            if pattern.search(name):
                return category
        return default_cat

    out = []
    for order, h in enumerate(highlights, start=1):
        src = by_id.get(h["id"])
        if not src:
            print("  ! highlight not found in source: %s (%s)" % (h["id"], h["name"]))
            continue
        out.append(feature(src, {"type": "Point", "coordinates": centre(src["geometry"])}, {
            "name": h["name"],
            "class": "highlight",
            "category": h["category"],
            "order": order,
            "source_name": src["properties"].get("name"),
            "audio_url": audio_default,
        }, provenance))

    discovered = []
    for f in features:
        p = f["properties"]
        name = p.get("name")
        if not p.get("building") or not name or f["id"] in highlight_ids:
            continue
        if f["geometry"]["type"] not in AREA_TYPES:
            continue
        lon, lat = centre(f["geometry"])
        if not (west <= lon <= east and south <= lat <= north):
            continue
        discovered.append((categorise(name), name, f))

    discovered.sort(key=lambda t: (t[0], t[1]))
    for offset, (category, name, f) in enumerate(discovered, start=len(out) + 1):
        out.append(feature(f, {"type": "Point", "coordinates": centre(f["geometry"])}, {
            "name": name,
            "class": "place",
            "category": category,
            "order": offset,
            "source_name": name,
            "audio_url": audio_default,
        }, provenance))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT,
                    help="source GeoJSON (default: build/osm-raw.geojson)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory (default: layers/)")
    ap.add_argument("--heights", default=os.path.join(ROOT, "build", "heights.json"),
                    help="optional building heights from pipeline/heights.py")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit("No source extract at %s\nRun: py pipeline/fetch_osm.py" % args.input)

    rules = load_rules("classification.json")
    place_rules = load_rules("places.json")
    provenance = rules["provenance"]

    heights = {}
    if os.path.exists(args.heights):
        with open(args.heights, encoding="utf-8") as fh:
            heights = json.load(fh).get("buildings", {})
        print("Heights loaded for %d buildings" % len(heights))
    else:
        print("No heights file yet - buildings will carry height_m: null")

    ground = {}
    elevation_path = os.path.join(ROOT, "data", "elevation.json")
    if os.path.exists(elevation_path):
        with open(elevation_path, encoding="utf-8") as fh:
            ground = json.load(fh).get("buildings", {})
        print("Ground elevation loaded for %d buildings (USGS 3DEP)" % len(ground))
    else:
        print("No elevation file yet - run: py pipeline/elevation.py")

    with open(args.input, encoding="utf-8") as fh:
        features = json.load(fh)["features"]
    print("Source: %s (%d features)\n" % (args.input, len(features)))

    os.makedirs(args.out, exist_ok=True)
    report = []
    write_layer(args.out, "paths", extract_paths(features, rules, provenance), report)
    write_layer(args.out, "parking", extract_parking(features, rules, provenance), report)
    write_layer(args.out, "buildings", extract_buildings(features, rules, provenance, heights, ground), report)
    write_layer(args.out, "landcover", extract_landcover(features, rules, provenance), report)
    write_layer(args.out, "barriers", extract_barriers(features, rules, provenance), report)
    write_layer(args.out, "places", extract_places(features, place_rules, provenance), report)

    width = max(len(name) for name, _, _, _ in report)
    total = 0
    for name, count, counts, size in report:
        total += size
        breakdown = ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        print("%-*s %5d features  %6.1f KB   %s" % (width, name, count, size / 1024, breakdown))
    print("\n%d layers, %.1f KB total -> %s" % (len(report), total / 1024, args.out))


if __name__ == "__main__":
    main()

