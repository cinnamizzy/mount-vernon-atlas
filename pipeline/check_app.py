#!/usr/bin/env python
"""Static checks on the browser code, for environments with no JS runtime.

Not a substitute for running it - it cannot catch logic errors - but it catches
the failure modes that silently break a no-build-step app:

  1. an ES import that does not resolve to a real file
  2. getElementById on an id that index.html does not define
  3. a tokens.* path that style/tokens.json does not contain
  4. unbalanced braces, brackets or parens

    py pipeline/check_app.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")

errors = []
checked = []


def js_files():
    for base, _, names in os.walk(SRC):
        for n in sorted(names):
            if n.endswith(".js"):
                yield os.path.join(base, n)


def strip_literals(text):
    """Blank out strings, template literals, regex-ish and comments so delimiter
    counting and identifier scans are not fooled by them."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        elif c in "\"'`":
            quote = c
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append('""')
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_balance(path, code):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    line = 1
    for ch in code:
        if ch == "\n":
            line += 1
        elif ch in "([{":
            stack.append((ch, line))
        elif ch in ")]}":
            if not stack or stack[-1][0] != pairs[ch]:
                errors.append("%s: unexpected '%s' at line %d" % (rel(path), ch, line))
                return
            stack.pop()
    if stack:
        ch, line = stack[-1]
        errors.append("%s: unclosed '%s' opened at line %d" % (rel(path), ch, line))


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def main():
    # --- 1. imports resolve ------------------------------------------------
    import_re = re.compile(r"""^\s*import\s+[^'"]*from\s+['"]([^'"]+)['"]""", re.M)
    for path in js_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for target in import_re.findall(text):
            if not target.startswith("."):
                continue  # bare specifier: not used in this project
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(resolved):
                errors.append("%s: import '%s' does not resolve" % (rel(path), target))
        checked.append(path)

    # --- 2. DOM ids exist --------------------------------------------------
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    id_re = re.compile(r"""\$\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")
    getel_re = re.compile(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")
    used_ids = set()
    for path in js_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for match in list(id_re.findall(text)) + list(getel_re.findall(text)):
            used_ids.add(match)
            if match not in html_ids:
                errors.append("%s: getElementById('%s') but index.html has no such id"
                              % (rel(path), match))

    # --- 3. token paths exist ---------------------------------------------
    with open(os.path.join(ROOT, "style", "tokens.json"), encoding="utf-8") as fh:
        tokens = json.load(fh)
    token_re = re.compile(r"tokens\.((?:[A-Za-z_][A-Za-z0-9_]*\.)+[A-Za-z_][A-Za-z0-9_]*)")
    for path in js_files():
        with open(path, encoding="utf-8") as fh:
            text = strip_literals(fh.read())
        for dotted in set(token_re.findall(text)):
            node = tokens
            ok = True
            for part in dotted.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if not ok:
                errors.append("%s: tokens.%s is not defined in style/tokens.json"
                              % (rel(path), dotted))

    # --- 4. layer ids referenced from JS exist in the style ----------------
    style_dir = os.path.join(ROOT, "style", "layers")
    style_layer_ids = set()
    sprite_ids = set()
    # Sprite ids are the VALUES of icon-image / fill-pattern, never arbitrary
    # strings - "icon-image" and "icon-size" are MapLibre property names.
    SPRITE_PROPS = ("icon-image", "fill-pattern", "line-pattern", "background-pattern")

    def collect_sprites(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in SPRITE_PROPS:
                    stack = [v]
                    while stack:
                        item = stack.pop()
                        if isinstance(item, str):
                            sprite_ids.add(item)
                        elif isinstance(item, list):
                            # skip the expression operator and ["get", ...] operands
                            stack.extend(x for x in item[1:] if not isinstance(x, dict))
                else:
                    collect_sprites(v)
        elif isinstance(node, list):
            for v in node:
                collect_sprites(v)

    if os.path.isdir(style_dir):
        for name in sorted(os.listdir(style_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(style_dir, name), encoding="utf-8") as fh:
                fragment = json.load(fh)
            for layer in fragment.get("layers", []):
                if "id" in layer:
                    style_layer_ids.add(layer["id"])
            collect_sprites(fragment.get("layers", []))
    # "get"/"match" operands leak in as bare words; keep only real sprite names.
    sprite_ids = {s for s in sprite_ids if s.startswith(("pat-", "icon-"))}

    # Only ALL_CAPS *_LAYERS arrays are treated as layer-id lists, so ordinary
    # style objects with a `layers` key are not mistaken for one.
    layer_list_re = re.compile(r"[A-Z][A-Z_]*LAYERS\s*=\s*\[([^\]]*)\]")
    for path in js_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for body in layer_list_re.findall(text):
            for lid in re.findall(r"""['"]([a-z][a-z0-9-]+)['"]""", body):
                if "-" in lid and lid not in style_layer_ids:
                    errors.append("%s: references layer id '%s' which no style fragment defines"
                                  % (rel(path), lid))

    # --- 5. sprite references resolve -------------------------------------
    sprite_index = os.path.join(ROOT, "data", "sprite.json")
    if sprite_ids:
        if not os.path.exists(sprite_index):
            errors.append("style references sprites but data/sprite.json is missing "
                          "- run: py pipeline/sprite.py")
        else:
            with open(sprite_index, encoding="utf-8") as fh:
                available = set(json.load(fh).keys())
            for sid in sorted(sprite_ids - available):
                errors.append("sprite '%s' is referenced by the style but not in "
                              "data/sprite.json" % sid)
            print("sprites: %d referenced statically, %d available" % (len(sprite_ids), len(available)))

            # Data-driven references - e.g. icon-image ["get","sprite"] on the
            # trees layer - are invisible to the static scan, so check the values
            # that actually appear in the generated layer.
            trees_path = os.path.join(ROOT, "layers", "trees.geojson")
            if os.path.exists(trees_path):
                with open(trees_path, encoding="utf-8") as fh:
                    trees = json.load(fh)
                used = {f["properties"].get("sprite") for f in trees["features"]}
                used.discard(None)
                missing = sorted(used - available)
                for sid in missing:
                    errors.append("layers/trees.geojson uses sprite '%s' which is not "
                                  "in data/sprite.json - re-run pipeline/sprite.py" % sid)
                print("         %d tree sprite(s) in data, all present: %s"
                      % (len(used), not missing))

    # --- 6. delimiter balance ---------------------------------------------
    for path in js_files():
        with open(path, encoding="utf-8") as fh:
            check_balance(path, strip_literals(fh.read()))

    print("checked %d module(s):" % len(checked))
    for path in checked:
        print("   " + rel(path))
    print("\n%d DOM id(s) referenced, %d defined in index.html" % (len(used_ids), len(html_ids)))
    unused = sorted(html_ids - used_ids - {"map"})
    if unused:
        print("ids in index.html not referenced by JS: %s" % ", ".join(unused))

    if errors:
        print("\n%d ERROR(S):" % len(errors))
        for e in errors:
            print("  - " + e)
        sys.exit(1)
    print("\nApp static checks OK.")


if __name__ == "__main__":
    main()
