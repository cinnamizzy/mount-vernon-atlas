#!/usr/bin/env python
"""Validate that the style assembles and every token reference resolves.

Mirrors the resolution logic in src/style.js so breakage is caught from the
command line instead of in the browser console.

    py pipeline/check_style.py

Exits non-zero on any unresolved token, missing layer file, missing source,
duplicate layer id, or dangling layer data file.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STYLE = os.path.join(ROOT, "style")

errors = []
warnings = []


def strip_comments(obj):
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


def lookup(root, path):
    node = root
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def resolve(node, tokens, local, where):
    if isinstance(node, str) and node.startswith("$"):
        value = lookup(tokens, node[1:])
        if value is None:
            errors.append("unknown token %s (in %s)" % (node, where))
            return node
        return resolve(value, tokens, local, where)
    if isinstance(node, list):
        return [resolve(n, tokens, local, where) for n in node]
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            # $ref keeps its leading $: helper blocks are written as "$width_ramp",
            # so the path matches the fragment's keys literally.
            value = lookup(local, node["$ref"])
            if value is None:
                errors.append("unknown $ref %s (in %s)" % (node["$ref"], where))
                return node
            return resolve(value, tokens, local, where)
        return {k: resolve(v, tokens, local, where) for k, v in node.items()}
    return node


def find_unresolved(node, where, path="paint"):
    if isinstance(node, str) and node.startswith("$"):
        errors.append("unresolved %s at %s (%s)" % (node, path, where))
    elif isinstance(node, list):
        for i, n in enumerate(node):
            find_unresolved(n, where, "%s[%d]" % (path, i))
    elif isinstance(node, dict):
        for k, v in node.items():
            find_unresolved(v, where, "%s.%s" % (path, k))


def main():
    with open(os.path.join(STYLE, "tokens.json"), encoding="utf-8") as fh:
        tokens = strip_comments(json.load(fh))
    with open(os.path.join(STYLE, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)

    # Everything under `sources` is handed to MapLibre verbatim. Validate what is
    # actually there rather than quietly filtering - a $comment key here reaches
    # the browser as a source with no "type" and the whole style is rejected.
    raw_sources = manifest["sources"]
    for name, source in raw_sources.items():
        if name.startswith("$comment"):
            errors.append(
                "manifest `sources` contains the comment key '%s'. Keys under "
                "`sources` become MapLibre sources verbatim - move the note to "
                "the top level of manifest.json." % name)
        elif not isinstance(source, dict) or "type" not in source:
            errors.append("source '%s' has no \"type\"" % name)
        elif source["type"] == "geojson" and "data" not in source:
            errors.append("geojson source '%s' has no \"data\"" % name)
        elif source["type"] == "raster" and not (source.get("tiles") or source.get("url")):
            errors.append("raster source '%s' has neither \"tiles\" nor \"url\"" % name)
    sources = {k: v for k, v in raw_sources.items() if not k.startswith("$comment")}
    layers = []
    seen_ids = {}

    for name in manifest["order"]:
        path = os.path.join(STYLE, "layers", name + ".json")
        if not os.path.exists(path):
            errors.append("manifest lists '%s' but %s does not exist" % (name, path))
            continue
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        resolved = strip_comments(resolve(raw, tokens, raw, name))
        for layer in resolved.get("layers", []):
            find_unresolved(layer, "%s/%s" % (name, layer.get("id", "?")))
            lid = layer.get("id")
            if lid in seen_ids:
                errors.append("duplicate layer id '%s' (%s and %s)" % (lid, seen_ids[lid], name))
            seen_ids[lid] = name
            src = layer.get("source")
            if src and src not in sources:
                errors.append("layer '%s' uses source '%s' which the manifest does not define" % (lid, src))
            layers.append((name, layer))

    # every declared source must have a data file on disk
    for name, source in sources.items():
        data = source.get("data")
        if data and not os.path.exists(os.path.join(ROOT, data)):
            errors.append("source '%s' points at %s which does not exist - run classify.py" % (name, data))

    # every layer data file on disk should be referenced
    layers_dir = os.path.join(ROOT, "layers")
    if os.path.isdir(layers_dir):
        referenced = {os.path.basename(s.get("data", "")) for s in sources.values()}
        for f in sorted(os.listdir(layers_dir)):
            if f.endswith(".geojson") and f not in referenced:
                warnings.append("layers/%s exists but no source references it" % f)

    print("tokens      %d colour groups" % len(tokens.get("color", {})))
    print("manifest    %d fragments, %d sources" % (len(manifest["order"]), len(sources)))
    print("assembled   %d layers" % len(layers))
    for name, layer in layers:
        print("   %-10s %-26s %s" % (name, layer.get("id", "?"), layer.get("type", "?")))

    for w in warnings:
        print("\nWARN  " + w)
    if errors:
        print("\n%d ERROR(S):" % len(errors))
        for e in errors:
            print("  - " + e)
        sys.exit(1)
    print("\nStyle OK - every token resolved.")


if __name__ == "__main__":
    main()
