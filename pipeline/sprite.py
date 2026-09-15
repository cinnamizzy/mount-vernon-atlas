#!/usr/bin/env python
"""Generate the MapLibre sprite sheet: tileable pattern fills and category icons.

Spec lives in pipeline/rules/sprite.json - edit that, not this script.

    py pipeline/sprite.py

Writes data/sprite.png + data/sprite.json (and @2x variants), which the style
references as `fill-pattern` and `icon-image`.

numpy plus the standard library only - the PNG encoder is thirty lines at the
bottom rather than a Pillow dependency, so `pip install` is never needed to
rebuild the artwork.

Patterns must tile seamlessly, so every primitive is drawn with wraparound: a
dot near an edge is also drawn on the opposite edge.
"""
import argparse
import json
import os
import random
import struct
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SS = 4  # supersampling factor, for antialiased edges


# --- colour -----------------------------------------------------------------

def rgb(value):
    value = value.lstrip("#")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


# --- drawing primitives (operate on a float coverage mask) ------------------

def circle_mask(size, cx, cy, radius, wrap):
    """Coverage of a circle, optionally wrapping at the tile edges."""
    ys, xs = np.mgrid[0:size, 0:size]
    ys = ys + 0.5
    xs = xs + 0.5
    best = np.full((size, size), np.inf)
    shifts = (-size, 0, size) if wrap else (0,)
    for dx in shifts:
        for dy in shifts:
            d = np.hypot(xs - (cx + dx), ys - (cy + dy))
            best = np.minimum(best, d)
    # 1 inside, 0 outside, soft over one supersampled pixel
    return np.clip(radius + 0.5 - best, 0, 1)


def line_mask(size, angle_deg, spacing, width, dash, wrap):
    """Parallel lines at an angle, spaced evenly. Seamless when spacing divides size."""
    ys, xs = np.mgrid[0:size, 0:size]
    ys = ys + 0.5
    xs = xs + 0.5
    theta = np.radians(angle_deg)
    # distance along the normal, wrapped into one spacing band
    proj = xs * np.sin(theta) + ys * np.cos(theta)
    band = np.mod(proj, spacing)
    dist = np.minimum(band, spacing - band)
    mask = np.clip(width / 2 + 0.5 - dist, 0, 1)
    if dash:
        on, off = dash
        along = xs * np.cos(theta) - ys * np.sin(theta)
        mask = mask * (np.mod(along, on + off) < on)
    return mask


def polygon_mask(size, points, scale):
    """Even-odd fill of a polygon given in normalised 0-1 coordinates."""
    pts = np.array(points, dtype=np.float64) * scale
    ys, xs = np.mgrid[0:size, 0:size]
    xs = xs + 0.5
    ys = ys + 0.5
    inside = np.zeros((size, size), dtype=bool)
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if y0 == y1:
            continue
        crosses = ((y0 > ys) != (y1 > ys))
        xint = x0 + (ys - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (xs < xint)
    return inside.astype(np.float64)


def value_noise(size, cells, seed, octaves=4, persistence=0.55):
    """Tileable fractal value noise in 0..1.

    Lattice indices wrap with modulo, so opposite edges match and the pattern
    tiles seamlessly - which is what lets a watercolour wash cover a whole
    polygon without visible seams.
    """
    rng = np.random.default_rng(seed)
    total = np.zeros((size, size))
    amplitude = 1.0
    norm = 0.0
    for octave in range(octaves):
        c = max(1, int(cells * (2 ** octave)))
        lattice = rng.random((c, c))
        t = np.arange(size) * c / size
        i0 = np.floor(t).astype(int) % c
        i1 = (i0 + 1) % c
        f = t - np.floor(t)
        f = f * f * (3 - 2 * f)  # smoothstep
        fy = f[:, None]
        fx = f[None, :]
        y0, y1 = i0[:, None], i1[:, None]
        x0, x1 = i0[None, :], i1[None, :]
        value = (lattice[y0, x0] * (1 - fx) * (1 - fy)
                 + lattice[y0, x1] * fx * (1 - fy)
                 + lattice[y1, x0] * (1 - fx) * fy
                 + lattice[y1, x1] * fx * fy)
        total += value * amplitude
        norm += amplitude
        amplitude *= persistence
    total /= norm
    lo, hi = total.min(), total.max()
    return (total - lo) / (hi - lo) if hi > lo else total


def composite(canvas, mask, colour, alpha):
    """Alpha-blend a flat colour into an RGB canvas using a coverage mask."""
    a = (mask * alpha)[..., None]
    canvas[:] = canvas * (1 - a) + colour * a


def multiply(canvas, factor):
    """Darken by a 0..1 field, the way pigment settles into paper."""
    canvas *= factor[..., None]


def downsample(arr, factor):
    h, w = arr.shape[:2]
    trailing = arr.shape[2:]
    return arr.reshape(h // factor, factor, w // factor, factor, *trailing).mean(axis=(1, 3))


# --- builders ---------------------------------------------------------------

def build_pattern(spec, size):
    big = size * SS
    canvas = np.tile(rgb(spec["base"]), (big, big, 1))
    for mark in spec.get("marks", []):
        kind = mark["type"]
        colour = rgb(mark.get("color", "#000000"))
        alpha = mark.get("alpha", 1.0)
        if kind == "speckle":
            rng = random.Random(mark.get("seed", 0))
            lo, hi = mark["radius"]
            for _ in range(mark["count"]):
                cx = rng.uniform(0, size) * SS
                cy = rng.uniform(0, size) * SS
                r = rng.uniform(lo, hi) * SS
                composite(canvas, circle_mask(big, cx, cy, r, wrap=True), colour, alpha)
        elif kind == "grid_dots":
            spacing = mark["spacing"] * SS
            ox, oy = [v * SS for v in mark.get("offset", [0, 0])]
            r = mark["radius"] * SS
            y = oy
            while y < big + spacing:
                x = ox
                while x < big + spacing:
                    composite(canvas, circle_mask(big, x, y, r, wrap=True), colour, alpha)
                    x += spacing
                y += spacing
        elif kind == "lines":
            mask = line_mask(big, mark.get("angle", 0), mark["spacing"] * SS,
                             mark["width"] * SS,
                             [v * SS for v in mark["dash"]] if mark.get("dash") else None,
                             wrap=True)
            composite(canvas, mask, colour, alpha)
        elif kind == "wash":
            # Uneven pigment density: a second tint laid in where the wash pooled.
            noise = value_noise(big, mark.get("cells", 3), mark.get("seed", 0),
                                mark.get("octaves", 4))
            gamma = mark.get("contrast", 1.0)
            composite(canvas, np.power(noise, gamma), colour, alpha)
        elif kind == "granulation":
            # Pigment settling into the tooth of the paper - fine, high frequency.
            noise = value_noise(big, mark.get("cells", 14), mark.get("seed", 0),
                                mark.get("octaves", 2))
            strength = mark.get("strength", 0.15)
            multiply(canvas, 1.0 - strength * noise)
        else:
            raise ValueError("unknown mark type: %s" % kind)
    rgb_small = downsample(canvas, SS)
    alpha_small = np.full(rgb_small.shape[:2] + (1,), 255.0)
    return np.concatenate([rgb_small, alpha_small], axis=2)


def build_icon(spec, size):
    big = size * SS
    canvas = np.zeros((big, big, 3), dtype=np.float64)
    coverage = np.zeros((big, big), dtype=np.float64)
    default = rgb(spec.get("color", "#000000"))
    for shape in spec["shapes"]:
        colour = rgb(shape["color"]) if shape.get("color") else default
        if shape["type"] == "circle":
            cx, cy = [v * big for v in shape["center"]]
            mask = circle_mask(big, cx, cy, shape["radius"] * big, wrap=False)
        elif shape["type"] == "polygon":
            mask = polygon_mask(big, shape["points"], big)
        else:
            raise ValueError("unknown shape type: %s" % shape["type"])
        a = mask[..., None]
        canvas[:] = canvas * (1 - a) + colour * a
        coverage = np.maximum(coverage, mask)
    rgb_small = downsample(canvas, SS)
    alpha_small = downsample(coverage, SS)[..., None] * 255.0
    return np.concatenate([rgb_small, alpha_small], axis=2)


# --- atlas packing ----------------------------------------------------------

def pack(entries, padding=2):
    """Simple shelf packing into a square-ish atlas."""
    widest = max(e["image"].shape[1] for e in entries)
    per_row = max(1, int(np.ceil(np.sqrt(len(entries)))))
    width = per_row * (widest + padding) + padding

    x = y = padding
    row_height = 0
    placed = []
    for entry in entries:
        h, w = entry["image"].shape[:2]
        if x + w + padding > width:
            x = padding
            y += row_height + padding
            row_height = 0
        placed.append((entry, x, y))
        x += w + padding
        row_height = max(row_height, h)
    height = y + row_height + padding

    atlas = np.zeros((height, width, 4), dtype=np.float64)
    index = {}
    for entry, px, py in placed:
        h, w = entry["image"].shape[:2]
        atlas[py:py + h, px:px + w] = entry["image"]
        index[entry["name"]] = {
            "x": px, "y": py, "width": w, "height": h,
            "pixelRatio": entry["ratio"],
        }
    return atlas, index


# --- PNG encoder (stdlib) ---------------------------------------------------

def write_png(path, rgba):
    data = np.clip(np.rint(rgba), 0, 255).astype(np.uint8)
    height, width = data.shape[:2]
    raw = bytearray()
    for row in data:
        raw.append(0)  # filter type 0
        raw.extend(row.tobytes())

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


# --- main -------------------------------------------------------------------

def strip_comments(obj):
    if isinstance(obj, dict):
        return {k: strip_comments(v) for k, v in obj.items() if not k.startswith("$comment")}
    if isinstance(obj, list):
        return [strip_comments(v) for v in obj]
    return obj


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "data"))
    args = ap.parse_args()

    with open(os.path.join(HERE, "rules", "sprite.json"), encoding="utf-8") as fh:
        spec = strip_comments(json.load(fh))

    ratio = spec.get("pixel_ratio", 2)
    pattern_size = spec["pattern_size"] * ratio
    icon_size = spec["icon_size"] * ratio

    # Namespaced, because a pattern and an icon may share a subject - "garden"
    # is both a fill texture and a category marker.
    entries = []
    for name, pattern in spec["patterns"].items():
        entries.append({"name": "pat-" + name, "ratio": ratio,
                        "image": build_pattern(pattern, pattern_size)})
        print("  pattern  pat-%-12s %d x %d" % (name, pattern_size, pattern_size))
    for name, icon in spec["icons"].items():
        entries.append({"name": "icon-" + name, "ratio": ratio,
                        "image": build_icon(icon, icon_size)})
        print("  icon     icon-%-11s %d x %d" % (name, icon_size, icon_size))

    # Trees share the icon renderer but are drawn larger - they carry the
    # illustration rather than acting as a marker.
    tree_size = spec.get("tree_size", spec["icon_size"]) * ratio
    for name, tree in spec.get("trees", {}).items():
        entries.append({"name": name, "ratio": ratio,
                        "image": build_icon(tree, tree_size)})
        print("  tree     %-16s %d x %d" % (name, tree_size, tree_size))

    names = [e["name"] for e in entries]
    if len(set(names)) != len(names):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        sys.exit("duplicate sprite names: %s" % ", ".join(duplicates))

    atlas, index = pack(entries)
    os.makedirs(args.out, exist_ok=True)

    # Paper grain. Not part of the atlas - it is laid over the whole map by CSS
    # with multiply blending, so the linework and labels sit on the same sheet
    # as the washes rather than floating above them.
    paper_spec = spec.get("paper")
    if paper_spec:
        size = paper_spec.get("size", 256)
        tone = rgb(paper_spec.get("color", "#ffffff"))
        fibre = value_noise(size, paper_spec.get("cells", 5),
                            paper_spec.get("seed", 7), octaves=5)
        tooth = value_noise(size, paper_spec.get("tooth_cells", 40),
                            paper_spec.get("seed", 7) + 1, octaves=2)
        shade = (1.0
                 - paper_spec.get("fibre_strength", 0.10) * fibre
                 - paper_spec.get("tooth_strength", 0.06) * tooth)
        sheet = tone * shade[..., None]
        paper = np.concatenate([sheet, np.full(shade.shape + (1,), 255.0)], axis=2)
        paper_bytes = write_png(os.path.join(args.out, "paper.png"), paper)
        print("\n  paper    %d x %d  %.1f KB" % (size, size, paper_bytes / 1024))

    # MapLibre looks for "<sprite>.png" and "<sprite>@2x.png". Everything here is
    # authored at the higher ratio, so the @2x file is the real one and the base
    # file is a half-size copy for ratio-1 displays.
    hi = os.path.join(args.out, "sprite@2x.png")
    lo = os.path.join(args.out, "sprite.png")
    size_hi = write_png(hi, atlas)
    half = downsample(atlas, 2) if atlas.shape[0] % 2 == 0 and atlas.shape[1] % 2 == 0 else atlas
    size_lo = write_png(lo, half)

    with open(os.path.join(args.out, "sprite@2x.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)
    lo_index = {k: {**v, "x": v["x"] // 2, "y": v["y"] // 2,
                    "width": v["width"] // 2, "height": v["height"] // 2,
                    "pixelRatio": 1}
                for k, v in index.items()}
    with open(os.path.join(args.out, "sprite.json"), "w", encoding="utf-8") as fh:
        json.dump(lo_index, fh, indent=1)

    print("\natlas %d x %d, %d sprites" % (atlas.shape[1], atlas.shape[0], len(index)))
    print("  data/sprite@2x.png  %.1f KB" % (size_hi / 1024))
    print("  data/sprite.png     %.1f KB" % (size_lo / 1024))
    print("\nStyle references it as \"sprite\": \"data/sprite\"")


if __name__ == "__main__":
    main()
