#!/usr/bin/env python
"""Validate the routing graph and its accessibility guarantees.

An independent implementation of the same graph construction used by
src/routing/, so the guarantees are checked against the data rather than against
one implementation of the algorithm.

    py pipeline/check_routing.py

Fails if the step-free profile can traverse any segment that came from a `steps`
feature - the prototype's accessibility bug, which must not return.
"""
import json
import math
import os
import sys
import heapq
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
R = 6371008.8

errors = []


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return json.load(fh)


def strip_comments(obj):
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


def haversine(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.sin(dlng / 2) ** 2 * math.cos(lat1) * math.cos(lat2)
    return 2 * R * math.asin(math.sqrt(h))


def key(c, precision):
    return "%.*f,%.*f" % (precision, c[0], precision, c[1])


def lines(geometry):
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def build(paths, profile, precision):
    adjacency = defaultdict(list)
    nodes = {}
    edge_set = set()
    used = 0
    for f in paths["features"]:
        p = f["properties"]
        if not p.get("routable"):
            continue
        if profile not in (p.get("profiles") or []):
            continue
        used += 1
        for line in lines(f["geometry"]):
            for i in range(1, len(line)):
                a, b = line[i - 1], line[i]
                ka, kb = key(a, precision), key(b, precision)
                if ka == kb:
                    continue
                nodes[ka] = a
                nodes[kb] = b
                d = haversine(a, b)
                adjacency[ka].append((kb, d))
                adjacency[kb].append((ka, d))
                edge_set.add(frozenset((ka, kb)))
    return {"adjacency": adjacency, "nodes": nodes, "edges": edge_set, "features": used}


def components(graph):
    seen = {}
    cid = 0
    for start in graph["nodes"]:
        if start in seen:
            continue
        stack = [start]
        seen[start] = cid
        while stack:
            k = stack.pop()
            for nxt, _ in graph["adjacency"].get(k, []):
                if nxt not in seen:
                    seen[nxt] = cid
                    stack.append(nxt)
        cid += 1
    return seen, cid


def nearest(graph, position):
    best, bd = None, float("inf")
    for k, c in graph["nodes"].items():
        d = haversine(position, c)
        if d < bd:
            bd, best = d, k
    return best, bd


def shortest(graph, start, end):
    if start not in graph["adjacency"] or end not in graph["adjacency"]:
        return None
    dist = {start: 0.0}
    queue = [(0.0, start)]
    settled = set()
    while queue:
        d, k = heapq.heappop(queue)
        if k in settled:
            continue
        settled.add(k)
        if k == end:
            return d
        for nxt, w in graph["adjacency"][k]:
            nd = d + w
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                heapq.heappush(queue, (nd, nxt))
    return None


def main():
    paths = load("layers/paths.geojson")
    places = load("layers/places.geojson")
    config = strip_comments(load("config/routing.json"))
    precision = config["coordinate_precision"]

    # Every edge that came from a `steps` feature.
    steps_edges = set()
    for f in paths["features"]:
        if f["properties"].get("class") != "steps":
            continue
        for line in lines(f["geometry"]):
            for i in range(1, len(line)):
                ka = key(line[i - 1], precision)
                kb = key(line[i], precision)
                if ka != kb:
                    steps_edges.add(frozenset((ka, kb)))
    print("steps segments in source: %d\n" % len(steps_edges))

    graphs = {}
    for profile in config["profiles"]:
        g = build(paths, profile, precision)
        comp, count = components(g)
        sizes = defaultdict(int)
        for c in comp.values():
            sizes[c] += 1
        largest = max(sizes.values()) if sizes else 0
        graphs[profile] = (g, comp)
        print("%-10s %4d features  %5d nodes  %5d edges  %3d components  largest %d (%.0f%%)"
              % (profile, g["features"], len(g["nodes"]), len(g["edges"]),
                 count, largest, 100 * largest / max(1, len(g["nodes"]))))

        leaked = steps_edges & g["edges"]
        if profile == "step_free":
            if leaked:
                errors.append("step_free graph contains %d segment(s) from `steps` features" % len(leaked))
            else:
                print("           OK: no `steps` segment is traversable in this profile")

    # Reachability of the curated highlights, per profile.
    highlights = [f for f in places["features"] if f["properties"].get("class") == "highlight"]
    print("\nHighlight reachability (%d curated places):" % len(highlights))
    max_snap = config["snap"]["max_distance_m"]
    for profile, (g, comp) in graphs.items():
        snaps = {}
        far = []
        for f in highlights:
            k, d = nearest(g, f["geometry"]["coordinates"])
            snaps[f["properties"]["name"]] = (k, d)
            if d > max_snap:
                far.append((f["properties"]["name"], d))
        comp_ids = defaultdict(list)
        for name, (k, d) in snaps.items():
            if d <= max_snap:
                comp_ids[comp.get(k)].append(name)
        biggest = max(comp_ids.values(), key=len) if comp_ids else []
        print("  %-10s %2d/%d on one connected network; %d beyond the %d m snap limit"
              % (profile, len(biggest), len(highlights), len(far), max_snap))
        for name, d in sorted(far, key=lambda t: -t[1])[:4]:
            print("      far: %-28s %5.0f m" % (name[:28], d))

    # A concrete route, reported for both profiles.
    def find(name):
        return next((f for f in highlights if f["properties"]["name"] == name), None)

    pairs = [("Ford Orientation Center", "The Mansion"),
             ("The Mansion", "Washington's Tomb"),
             ("Museum & Education Center", "Upper Garden")]
    print("\nSample routes:")
    for a_name, b_name in pairs:
        a, b = find(a_name), find(b_name)
        if not a or not b:
            print("  %s -> %s: place not found" % (a_name, b_name))
            continue
        line = "  %-26s -> %-22s" % (a_name[:26], b_name[:22])
        for profile, (g, _) in graphs.items():
            ka, _ = nearest(g, a["geometry"]["coordinates"])
            kb, _ = nearest(g, b["geometry"]["coordinates"])
            metres = shortest(g, ka, kb)
            pace = config["profiles"][profile]["pace_m_per_min"]
            if metres is None:
                line += "  %s: no route" % profile
            else:
                line += "  %s: %4.0f m / %2.0f min" % (profile, metres, round(metres / pace))
        print(line)

    # --- gradient: advisory only, and this is why -------------------------
    elev_path = os.path.join(ROOT, "data", "elevation.json")
    grad_cfg = config.get("gradient", {})
    if os.path.exists(elev_path):
        elevations = json.load(open(elev_path, encoding="utf-8"))["nodes"]
        g, comp = graphs["step_free"]
        min_seg = grad_cfg.get("min_segment_m", 0.5)
        grads = []
        for edge in g["edges"]:
            ka, kb = tuple(edge)
            ea, eb = elevations.get(ka), elevations.get(kb)
            if ea is None or eb is None:
                continue
            d = haversine(g["nodes"][ka], g["nodes"][kb])
            if d < min_seg:
                continue
            grads.append(abs(eb - ea) / d)
        grads.sort()
        if grads:
            n = len(grads)
            pct = lambda q: 100 * grads[min(n - 1, int(n * q / 100))]
            print("\nGradient (bare-earth DEM, %d step-free segments):" % n)
            print("  median %.1f%%   p90 %.1f%%   p95 %.1f%%   max %.1f%%"
                  % (pct(50), pct(90), pct(95), pct(100)))
            warn = grad_cfg.get("warn_above", 0.0833)
            steep = sum(1 for x in grads if x > warn)
            print("  %d segment(s) (%.1f%%) exceed the %.1f%% advisory threshold"
                  % (steep, 100 * steep / n, 100 * warn))
            if grad_cfg.get("filters_routing"):
                errors.append(
                    "config sets gradient.filters_routing = true. Measured on this "
                    "estate an 8.33%% cut leaves only 8 of 16 highlights connected, "
                    "and the DEM's 53%% maximum is an artifact, not a walkway. "
                    "Gradient must stay advisory until the estate supplies a "
                    "surveyed accessible-route inventory.")
            else:
                print("  OK: gradient is advisory and does not filter routing")
    else:
        print("\nNo data/elevation.json - run: py pipeline/elevation.py")

    if errors:
        print("\n%d ERROR(S):" % len(errors))
        for e in errors:
            print("  - " + e)
        sys.exit(1)
    print("\nRouting OK.")


if __name__ == "__main__":
    main()
