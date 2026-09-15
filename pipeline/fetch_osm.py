#!/usr/bin/env python
"""Fetch a fresh OpenStreetMap extract for the estate and convert it to GeoJSON.

The bounding box and tag filters live here in version control, so the extract is
reproducible rather than a frozen blob of unknown provenance.

    py pipeline/fetch_osm.py
    py pipeline/fetch_osm.py --out build/osm-raw.geojson --timeout 300

Writes build/osm-raw.geojson and stamps the retrieval date into sources.json.
Stdlib only. Data is © OpenStreetMap contributors, ODbL 1.0.

Multipolygon handling stitches outer and inner rings from member ways. Relations
whose rings cannot be closed are skipped and reported rather than emitted as
broken geometry - re-run with --verbose to list them.
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

ENDPOINT = "https://overpass-api.de/api/interpreter"

# [west, south, east, north] - generous enough to include approach roads and the
# river frontage, matching the prototype's coverage.
BBOX = [-77.105, 38.700, -77.072, 38.720]

# Tags that make a closed way an area rather than a ring-shaped line.
AREA_KEYS = {
    "building", "landuse", "leisure", "natural", "amenity", "shop", "tourism",
    "historic", "man_made", "place", "boundary", "waterway", "military",
    "building:part", "public_transport", "parking",
}
# ...unless the way also carries one of these, which stay linear.
LINEAR_KEYS = {"highway", "barrier", "railway"}


def query(bbox, timeout):
    west, south, east, north = bbox
    box = f"{south},{west},{north},{east}"
    return f"""[out:json][timeout:{timeout}];
(
  node({box});
  way({box});
  relation({box});
);
out body geom;
"""


def fetch(ql, timeout, verbose):
    body = urllib.parse.urlencode({"data": ql}).encode()
    request = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"User-Agent": "mount-vernon-atlas/1.0 (pipeline/fetch_osm.py)"},
    )
    if verbose:
        print(ql)
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout + 30) as response:
        payload = json.load(response)
    print("Overpass returned %d elements in %.1fs"
          % (len(payload.get("elements", [])), time.time() - started))
    return payload


def is_area(tags, coords):
    if len(coords) < 4 or coords[0] != coords[-1]:
        return False
    if tags.get("area") == "yes":
        return True
    if any(k in tags for k in LINEAR_KEYS):
        return False
    return any(k in tags for k in AREA_KEYS)


def ring(geometry):
    return [[p["lon"], p["lat"]] for p in geometry]


def stitch(ways):
    """Join member ways into closed rings. Returns (rings, leftover_count)."""
    segments = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while segments:
        current = segments.pop(0)
        changed = True
        while current[0] != current[-1] and changed:
            changed = False
            for i, seg in enumerate(segments):
                if seg[0] == current[-1]:
                    current += seg[1:]; segments.pop(i); changed = True; break
                if seg[-1] == current[-1]:
                    current += seg[::-1][1:]; segments.pop(i); changed = True; break
                if seg[-1] == current[0]:
                    current = seg[:-1] + current; segments.pop(i); changed = True; break
                if seg[0] == current[0]:
                    current = seg[::-1][:-1] + current; segments.pop(i); changed = True; break
        if current[0] == current[-1] and len(current) >= 4:
            rings.append(current)
        else:
            return rings, len(segments) + 1
    return rings, 0


def convert(payload, verbose):
    features = []
    skipped = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        kind = el["type"]

        if kind == "node":
            if not tags:
                continue  # untagged nodes are geometry for ways, not features
            geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}

        elif kind == "way":
            if "geometry" not in el:
                continue
            coords = ring(el["geometry"])
            if len(coords) < 2:
                continue
            if is_area(tags, coords):
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}

        elif kind == "relation":
            if tags.get("type") != "multipolygon":
                continue
            outer, inner = [], []
            for member in el.get("members", []):
                if member.get("type") != "way" or "geometry" not in member:
                    continue
                target = inner if member.get("role") == "inner" else outer
                target.append(ring(member["geometry"]))
            outer_rings, leftover = stitch(outer)
            if not outer_rings:
                skipped.append((el["id"], tags.get("name"), "no closed outer ring"))
                continue
            if leftover:
                skipped.append((el["id"], tags.get("name"), "%d unclosed segments" % leftover))
            inner_rings, _ = stitch(inner)
            if len(outer_rings) == 1:
                geometry = {"type": "Polygon", "coordinates": [outer_rings[0]] + inner_rings}
            else:
                geometry = {"type": "MultiPolygon",
                            "coordinates": [[r] for r in outer_rings]}
        else:
            continue

        features.append({
            "type": "Feature",
            "id": "%s/%d" % (kind, el["id"]),
            "properties": tags,
            "geometry": geometry,
        })

    if skipped:
        print("\n%d relation(s) partially or fully skipped:" % len(skipped))
        for rid, name, why in (skipped if verbose else skipped[:8]):
            print("  relation/%s %-32s %s" % (rid, (name or "")[:32], why))
        if not verbose and len(skipped) > 8:
            print("  ... %d more (--verbose to list)" % (len(skipped) - 8))
    return features


def stamp_sources(date):
    path = os.path.join(ROOT, "sources.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    osm = data.get("sources", {}).get("openstreetmap")
    if osm:
        osm["retrieved"] = date
        osm["retrieved_via"] = "pipeline/fetch_osm.py"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        print("sources.json: openstreetmap.retrieved = %s" % date)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "osm-raw.geojson"))
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    try:
        payload = fetch(query(BBOX, args.timeout), args.timeout, args.verbose)
    except urllib.error.HTTPError as err:
        sys.exit("Overpass HTTP %s: %s\nThe public endpoint rate-limits; wait and retry."
                 % (err.code, err.reason))
    except urllib.error.URLError as err:
        sys.exit("Could not reach Overpass: %s" % err.reason)

    features = convert(payload, args.verbose)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, separators=(",", ":"))

    kinds = {}
    for f in features:
        kinds[f["geometry"]["type"]] = kinds.get(f["geometry"]["type"], 0) + 1
    print("\n%d features -> %s (%.1f KB)"
          % (len(features), args.out, os.path.getsize(args.out) / 1024))
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print("   %-16s %d" % (k, v))

    stamp_sources(time.strftime("%Y-%m-%d"))
    print("\nNext: py pipeline/classify.py")


if __name__ == "__main__":
    main()
