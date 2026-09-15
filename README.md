# Mount Vernon Estate Atlas

A 2D vector map of the Mount Vernon estate with a tour planner, rebuilt so that
every component is a separate, individually editable file.

No build step, no bundler, no backend. Edit a JSON file, refresh the browser.

## Run it

Double-click **`serve.cmd`**, or from a terminal:

```
serve.cmd            REM port 8000
serve.cmd 8080       REM any other port
```

It finds a real Python, serves the folder, and opens the browser. Stop with
`Ctrl+C`.

To do it by hand, note that `py` and `python` resolve to the Microsoft Store stub
on some Windows installs and fail with "Python was not found" — use the full
interpreter path:

```
REM cmd.exe
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m http.server 8000
```

```powershell
# PowerShell - note & and $env: are PowerShell-only syntax
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m http.server 8000
```

Then open <http://localhost:8000/>. Opening `index.html` as a `file://` URL will
not work — ES modules and `fetch` both need a real HTTP origin.

## Share it

The site is entirely static — no backend, no build step — so any static host
works. MapLibre and Three.js are vendored, so there is no CDN to fail.

**GitHub Pages**

```
git init
git add -A
git commit -m "Mount Vernon Estate Atlas"
git branch -M main
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

Then on GitHub: **Settings → Pages → Source: Deploy from a branch → main → / (root)**.
The site appears at `https://USERNAME.github.io/REPO/` within a minute or two.
All paths are relative, so it works from a subpath.

`.gitignore` excludes `build/ept-cache/` — 306 MB of lidar tiles that must never
be committed. Everything else totals about 6 MB.

**Netlify Drop / Cloudflare Pages** — drag the folder onto
<https://app.netlify.com/drop> for an instant URL with no git. Delete or move
`build/ept-cache/` first, or you will upload 306 MB for nothing.

One external runtime dependency remains: map label glyphs are fetched from
`fonts.openmaptiles.org` (set in `style/manifest.json`). Everything else is
served from the repository. Self-host the glyphs before anything
visitor-facing.

## Regenerate the data

```
py pipeline/fetch_osm.py     # Overpass        -> build/osm-raw.geojson
py pipeline/elevation.py     # USGS 3DEP       -> data/elevation.json
py pipeline/classify.py      # apply rules     -> layers/*.geojson
py pipeline/sprite.py        # textures, icons -> data/sprite*.png + .json
```

Checks — all three should pass before publishing:

```
py pipeline/check_style.py     # every style token and $ref resolves
py pipeline/check_app.py       # imports, DOM ids, tokens, layer ids, sprites
py pipeline/check_routing.py   # graph health, step-free guarantee, gradients
```

`fetch_osm.py` and `elevation.py` are optional day to day — `layers/` and
`data/` are committed. Run them to refresh; `fetch_osm.py` stamps the retrieval
date into `sources.json`.

## How to change things

| To change | Edit | Then |
|---|---|---|
| A colour or line width | `style/tokens.json` | refresh |
| A pigment wash, paper grain, or an icon | `pipeline/rules/sprite.json` | re-run `sprite.py` |
| How strong the paper grain reads | `src/app.css` → `--paper-strength` | refresh |
| How a layer is drawn | `style/layers/<name>.json` | refresh |
| Draw order, sources, initial view | `style/manifest.json` | refresh |
| Which OSM tags map to which class | `pipeline/rules/classification.json` | re-run `classify.py` |
| Render width per path class | `pipeline/rules/classification.json` → `width_by_class` | re-run `classify.py` |
| Which path classes a routing profile may use | `pipeline/rules/classification.json` → `profiles` | re-run `classify.py` |
| Walking pace, snap limits, gradient advisory | `config/routing.json` | refresh |
| Highlight places, categories, core bounds | `pipeline/rules/places.json` | re-run `classify.py` |
| The map's extent | `pipeline/fetch_osm.py` → `BBOX` | re-run everything |

## The look

Hand-tinted historical estate map: saturated pigment, laid unevenly, granulating
into the paper, with crisp ink linework drawn over it. Deliberately flat 2D —
no shaded relief, no extrusion, no invented depth.

How it is built, in the order it paints:

1. **Flat base tint** per land class — what shows before sprites load, and behind
   any class with no wash.
2. **Pigment wash** — a tileable sprite pattern per class, generated from fractal
   value noise so the tint is uneven and granulated the way a real wash is.
   Structural marks (orchard ranks, plough furrows, bed rows, canopy stipple) are
   laid over the wash, the way an engraver would work over a tint.
3. **Ink linework** — parcel edges, building outlines, path casings, walls and
   fences, all as crisp vector strokes in one sepia ink. These stay sharp at
   every zoom; only the fills are soft.
4. **Paper grain** — one sheet of tileable paper texture multiplied over the whole
   map in CSS, so washes, ink and labels sit on the same paper rather than the
   type floating above the artwork.

Everything in steps 1–2 is regenerated by `py pipeline/sprite.py` from
`pipeline/rules/sprite.json`. Colour lives in `style/tokens.json`.

Four rules keep it that way:

1. **No colour literal outside `style/tokens.json`.** Fragments reference
   `"$color.path.visitor_path"`; `check_style.py` fails on any unresolved token.
2. **Style keys on semantic `class`, never raw OSM tags.** Reclassifying is a
   pipeline change, not a style change.
3. **The legend is generated from the style fragments and filtered by live
   visibility.** A legend key describing something the map is not drawing is
   impossible by construction.
4. **Routing profiles are defined once, in the pipeline.** Each path feature
   carries its own `profiles` array; the router only reads it. The step-free
   profile cannot silently regain steps.

## Layout

```
sources.json              every input, its license, and its retrieval date
pipeline/
  fetch_osm.py            Overpass query    -> build/osm-raw.geojson
  elevation.py            USGS 3DEP samples -> data/elevation.json
  classify.py             applies rules/    -> layers/*.geojson
  check_style.py          validates every token and $ref resolves
  check_app.py            static checks on the browser code
  check_routing.py        graph health, step-free guarantee, gradient policy
  rules/
    classification.json   tag -> semantic class, widths, routing profiles
    places.json           curated highlights, categories, core bounds
config/routing.json       pace, snap limits, gradient advisory settings
layers/                   one GeoJSON per semantic layer (pipeline output)
data/elevation.json       ground elevation per routing node and building
style/
  tokens.json             THE palette and base widths
  manifest.json           draw order, sources, initial view
  layers/*.json           one style fragment per layer, incl. its legend
src/
  style.js                assembles the MapLibre style at runtime
  map.js                  map shell, layer toggles, generated legend
  tour.js                 route panel: stops, summary, export
  routing/
    geo.js                haversine distance, node keys
    graph.js              per-profile graph, exact-coordinate joins
    search.js             A* with a binary heap
    snap.js               grid-indexed nearest node
    terrain.js            ascent, descent, steepest gradient (advisory)
    index.js              Router - the public API
  app.css                 app chrome only (no map colour lives here)
build/                    intermediates, not committed
```

## Current state

| Layer | Features |
|---|---|
| paths | 494 — service_road 229, visitor_path 188, public_road 36, trail 19, cycleway 14, steps 8 |
| buildings | 1,173 (all with ground elevation; none with height — see below) |
| landcover | 59 |
| barriers | 81 |
| parking | 19 |
| places | 45 (16 curated highlights + 29 discovered) |

Routing graph: 2,812 nodes / 2,858 edges (visitor), 2,784 / 2,822 (step-free).
All 16 highlights are connected in both profiles.

Working: data pipeline, semantic classification, modular style, generated legend
and layer toggles, feature inspector, geolocate, routing with walking-time and
ascent estimates, route GeoJSON export, and routing from the visitor's own GPS
position.

## Known gaps

- **No building heights.** Zero source buildings carry `height`,
  `building:levels`, `roof:shape` or `roof:material`. `ground_elevation_m` is
  real (USGS 3DEP, 1 m), but `height_m` stays `null` until a surface model
  (DSM / lidar point cloud) or estate plans supply it. Any 3D rendering before
  then would be invented — which is what went wrong in the prototype.
- **Gradient is advisory and does not filter routing.** Measured on this estate
  the bare-earth DEM gives a median gradient of 2% but a maximum of 53.5% on
  segments that are plainly not 53.5% walkways — short vertex spacing amplifies
  sub-metre DEM noise, and boardwalks and retaining walls read as terrain.
  Applying the ADA 8.33% ramp limit leaves only **8 of 16 highlights** on one
  connected network. A real step-free network needs an estate-supplied
  accessible-route inventory. `check_routing.py` fails if anyone turns the
  filter on.
- **No garden interiors.** The prototype's garden detail was traced from Google
  Maps imagery and is excluded for licensing reasons — see `sources.json`
  → `rejected`. It must be re-derived from estate plans or licensed imagery.
- **Nothing is surveyed.** Every feature carries `surveyed: false` and a
  `confidence` value. Access, closures and step-free suitability require estate
  review before this is used for visitor navigation.
- **External font dependency.** `style/manifest.json` points at
  `fonts.openmaptiles.org` for glyphs. Self-host before production.

## Attribution

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
ODbL 1.0. Derived geographic data remains subject to ODbL.
Elevation: USGS 3D Elevation Program (3DEP), public domain.
